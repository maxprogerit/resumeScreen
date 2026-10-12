"""Regression tests for weaknesses found in the Stage 10 review."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import openai
import pytest
from openai.lib._parsing import type_to_response_format_param
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.llm.client import LLMInvalidOutput, LLMUnavailable
from app.llm.openai_client import OpenAIClient
from app.models import Candidate, CandidateStatus
from app.schemas import (
    EvidenceResponse,
    ExplanationResponse,
    ExtractedResume,
    JobRequirements,
)
from app.services.pipeline import recover_interrupted
from tests.conftest import Env, make_settings
from tests.fakes import FakeLLM
from tests.helpers import make_job, upload_synthetic

UNSUPPORTED_BY_STRICT_MODE = {"maxLength", "minLength", "minimum", "maximum", "default", "pattern"}


def _keys(node: Any, acc: set[str]) -> set[str]:
    if isinstance(node, dict):
        for k, v in node.items():
            acc.add(k)
            _keys(v, acc)
    elif isinstance(node, list):
        for v in node:
            _keys(v, acc)
    return acc


@pytest.mark.parametrize(
    "schema", [ExtractedResume, JobRequirements, EvidenceResponse, ExplanationResponse]
)
def test_llm_schemas_are_valid_openai_strict_schemas(schema: type[BaseModel]) -> None:
    fmt: Any = type_to_response_format_param(schema)
    assert fmt["json_schema"]["strict"] is True
    used = _keys(fmt["json_schema"]["schema"], set())
    assert not used & UNSUPPORTED_BY_STRICT_MODE


# ------------------------------------------------------------------ OpenAI adapter (stubbed SDK)


def _client_with(
    parse: Callable[..., Any] | None = None, embed: Callable[..., Any] | None = None
) -> OpenAIClient:
    client = OpenAIClient(make_settings(openai_api_key="sk-test"))
    client._client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(parse=parse)),
        embeddings=SimpleNamespace(create=embed),
    )
    return client


def _completion(parsed: object, refusal: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed, refusal=refusal))]
    )


def test_adapter_requires_a_key() -> None:
    with pytest.raises(LLMUnavailable):
        OpenAIClient(Settings(_env_file=None, openai_api_key=None))  # type: ignore[call-arg]


def test_adapter_maps_sdk_failures_without_leaking_content() -> None:
    secret = "SECRET-RESUME-CONTENT"
    ok = ExplanationResponse(summary="x")
    assert (
        _client_with(lambda **_: _completion(ok)).parse(
            system="s", user="u", schema=ExplanationResponse
        )
        == ok
    )

    def invalid(**_: Any) -> None:
        try:
            ExplanationResponse.model_validate({"summary": secret, "extra": 1})
        except ValidationError as exc:
            raise exc  # noqa: TRY201

    with pytest.raises(LLMInvalidOutput) as info:
        _client_with(invalid).parse(system="s", user="u", schema=ExplanationResponse)
    assert secret not in str(info.value) and info.value.__cause__ is None

    def down(**_: Any) -> None:
        raise openai.APIConnectionError(request=None)  # type: ignore[arg-type]

    with pytest.raises(LLMUnavailable):
        _client_with(down).parse(system="s", user="u", schema=ExplanationResponse)
    with pytest.raises(LLMInvalidOutput):
        _client_with(lambda **_: _completion(None, "I cannot help")).parse(
            system="s", user="u", schema=ExplanationResponse
        )
    with pytest.raises(LLMUnavailable):
        _client_with(embed=down).embed(["x"])


# ------------------------------------------------------------------ retry and recovery


def test_needs_review_candidate_can_be_reprocessed_from_stored_text(
    make_env: Callable[..., Env],
) -> None:
    llm = FakeLLM(unavailable=True)
    env = make_env(llm)
    job = make_job(env, must=["python"])
    upload_synthetic(env, job["id"], count=1)
    row = env.client.get(f"/api/jobs/{job['id']}/candidates").json()[0]
    assert row["status"] == "NEEDS_REVIEW" and row["status_reason"] == "llm_unavailable"

    llm.unavailable = False  # e.g. the API key was configured meanwhile
    r = env.client.post(f"/api/candidates/{row['id']}/reprocess")
    assert r.status_code == 202
    after = env.client.get(f"/api/jobs/{job['id']}/candidates").json()[0]
    assert after["status"] == "READY" and after["total_score"] is not None
    assert len(env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]) == 1

    again = env.client.post(f"/api/candidates/{row['id']}/reprocess")
    assert again.status_code == 409 and again.json()["code"] == "not_retryable"


def test_stale_processing_is_flagged_but_fresh_work_is_left_alone(
    session_factory: sessionmaker[Session], db: Session
) -> None:
    now = datetime.now(UTC)
    stale = Candidate(
        source_filename="x.pdf",
        status=CandidateStatus.PROCESSING,
        processing_started_at=now - timedelta(minutes=30),
    )
    fresh = Candidate(
        source_filename="f.pdf", status=CandidateStatus.PROCESSING, processing_started_at=now
    )
    done = Candidate(source_filename="y.pdf", status=CandidateStatus.READY)
    db.add_all([stale, fresh, done])
    db.commit()
    assert recover_interrupted(session_factory) == 1
    db.expire_all()
    assert stale.status is CandidateStatus.NEEDS_REVIEW and stale.status_reason == "interrupted"
    assert fresh.status is CandidateStatus.PROCESSING  # another worker may still be on it
    assert done.status is CandidateStatus.READY


def test_explanation_text_is_truncated(make_env: Callable[..., Env]) -> None:
    env = make_env(FakeLLM(summary="word " * 1000))
    job = make_job(env)
    upload_synthetic(env, job["id"], count=1)
    cid = env.client.get(f"/api/jobs/{job['id']}/candidates").json()[0]["id"]
    summary = env.client.get(f"/api/candidates/{cid}").json()["match"]["explanation"]["summary"]
    assert len(summary) <= 1500


def test_adapter_reports_actionable_reasons() -> None:
    import httpx

    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")

    def failing(status: int, exc_type: type[openai.APIStatusError]) -> Callable[..., Any]:
        def raiser(**_: Any) -> None:
            raise exc_type(
                "x",
                response=httpx.Response(status, request=request),  # type: ignore[arg-type]
                body=None,
            )

        return raiser

    cases = [
        (401, openai.AuthenticationError, "llm_auth_error"),
        (403, openai.PermissionDeniedError, "llm_auth_error"),
        (404, openai.NotFoundError, "llm_model_not_found"),
        (429, openai.RateLimitError, "llm_rate_limited"),
        (500, openai.InternalServerError, "llm_unavailable"),
    ]
    for status, exc_type, reason in cases:
        client = _client_with(failing(status, exc_type))
        with pytest.raises(LLMUnavailable) as info:
            client.parse(system="s", user="u", schema=ExplanationResponse)
        assert info.value.reason == reason

    with pytest.raises(LLMUnavailable) as no_key:
        OpenAIClient(Settings(_env_file=None, openai_api_key=None))  # type: ignore[call-arg]
    assert no_key.value.reason == "llm_not_configured"
