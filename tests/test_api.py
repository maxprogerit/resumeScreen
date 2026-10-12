import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import (
    Candidate,
    CandidateIdentity,
    CandidateProfile,
    Job,
    Match,
)
from app.schemas import ExtractedResume
from scripts.generate_resumes import docx_bytes, generate_candidates, pdf_bytes
from tests.conftest import Env
from tests.fakes import FakeLLM, skills_requirements
from tests.helpers import make_job, resume_files, row_count, upload, upload_synthetic

ERROR_KEYS = {"timestamp", "status", "code", "message", "path"}


# ------------------------------------------------------------------ auth and errors


def test_api_requires_bearer_key(env: Env) -> None:
    anon = TestClient(env.client.app)
    r = anon.get("/api/jobs")
    assert r.status_code == 401 and set(r.json()) == ERROR_KEYS
    assert r.json()["code"] == "unauthorized"
    bad = anon.get("/api/jobs", headers={"Authorization": "Bearer nope"})
    assert bad.status_code == 401


def test_api_fails_closed_without_configured_key(make_env: Callable[..., Env]) -> None:
    env = make_env(api_key_admin=None)
    r = TestClient(env.client.app).get("/api/jobs", headers={"Authorization": "Bearer x"})
    assert r.status_code == 503 and r.json()["code"] == "auth_not_configured"


def test_error_format_not_found_and_validation(env: Env) -> None:
    r = env.client.get("/api/jobs/00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    assert set(r.json()) == ERROR_KEYS
    assert r.json()["path"] == "/api/jobs/00000000-0000-0000-0000-000000000000"

    secret = "TOP-SECRET-DESCRIPTION"
    bad = env.client.post("/api/jobs", json={"title": "", "description_text": secret})
    assert bad.status_code == 422 and set(bad.json()) == ERROR_KEYS
    assert bad.json()["code"] == "validation_error"
    assert secret not in bad.text  # validation errors never echo submitted values

    assert set(env.client.get("/api/does-not-exist").json()) == ERROR_KEYS


# ------------------------------------------------------------------ jobs


def test_create_job_extracts_and_normalizes_requirements(make_env: Callable[..., Env]) -> None:
    llm = FakeLLM(requirements=skills_requirements(["K8s", "Python"], ["Postgres", "python"], 3))
    env = make_env(llm)
    r = env.client.post(
        "/api/jobs",
        json={"title": "SRE", "description_text": "Looking for someone who knows k8s and Python."},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["requirements_source"] == "llm"
    assert body["requirements"]["must_have_skills"] == ["kubernetes", "python"]
    assert body["requirements"]["nice_to_have_skills"] == ["postgresql"]  # python already must-have
    with env.session_factory() as db:
        job = db.get(Job, uuid.UUID(body["id"]))
        assert job is not None and job.embedding is not None
        assert job.embedding_model == "fake-embed"


def test_create_job_without_llm_starts_with_empty_requirements(
    make_env: Callable[..., Env],
) -> None:
    env = make_env(FakeLLM(unavailable=True))
    r = env.client.post("/api/jobs", json={"title": "SRE", "description_text": "x" * 40})
    assert r.status_code == 201
    assert r.json()["requirements_source"] == "empty"
    assert r.json()["requirements"]["must_have_skills"] == []


def test_list_and_get_jobs(env: Env) -> None:
    job = make_job(env)
    assert [j["id"] for j in env.client.get("/api/jobs").json()] == [job["id"]]
    assert env.client.get(f"/api/jobs/{job['id']}").json()["title"] == job["title"]


# ------------------------------------------------------------------ upload + ranking


def test_upload_process_and_rank_end_to_end(env: Env) -> None:
    job = make_job(env, must=["python", "kubernetes"], nice=["docker"], years=2)
    cands = upload_synthetic(env, job["id"], count=6)

    listing = env.client.get(f"/api/jobs/{job['id']}/candidates").json()
    assert len(listing) == 6
    assert {c["status"] for c in listing} == {"READY"}

    r = env.client.get(f"/api/jobs/{job['id']}/ranking")
    assert r.status_code == 200
    body = r.json()
    assert "human makes the hiring decision" in body["notice"]
    scores = [i["total_score"] for i in body["items"]]
    assert scores == sorted(scores, reverse=True)
    assert [i["rank"] for i in body["items"]] == list(range(1, 7))
    item = body["items"][0]
    assert set(item["components"]) == {
        "must_have", "nice_to_have", "experience", "semantic", "language_education",
    }  # fmt: skip
    assert item["display_name"] in {c.name for c in cands}

    # must-have coverage in the API agrees with the synthetic ground truth
    truth = {c.name: c for c in cands}
    for it in body["items"]:
        have = {s.lower() for s in truth[it["display_name"]].skills}
        expected = int("python" in have) + int("kubernetes" in have)
        # >= because a job title such as "Python Developer" also mentions the skill
        assert expected <= it["must_have_matched"] <= 2


def test_ranking_filters_and_blind_mode(env: Env) -> None:
    job = make_job(env, must=["python", "kubernetes"])
    upload_synthetic(env, job["id"], count=8)
    base = env.client.get(f"/api/jobs/{job['id']}/ranking").json()
    top = base["items"][0]["total_score"]

    high = env.client.get(f"/api/jobs/{job['id']}/ranking", params={"min_score": top}).json()
    assert 1 <= len(high["items"]) <= len(base["items"])
    assert all(i["total_score"] >= top for i in high["items"])

    with_k8s = env.client.get(f"/api/jobs/{job['id']}/ranking", params={"skill": "k8s"}).json()
    assert all("kubernetes" in i["skills"] for i in with_k8s["items"])
    # ranks are global, filtering does not renumber
    ranks = {i["candidate_id"]: i["rank"] for i in base["items"]}
    assert all(ranks[i["candidate_id"]] == i["rank"] for i in with_k8s["items"])

    blind = env.client.get(f"/api/jobs/{job['id']}/ranking", params={"blind": True}).json()
    assert all(i["display_name"] is None for i in blind["items"])


def test_ranking_for_job_without_candidates_is_empty(env: Env) -> None:
    job = make_job(env)
    body = env.client.get(f"/api/jobs/{job['id']}/ranking").json()
    assert body["items"] == [] and body["total_candidates"] == 0


def test_upload_rejects_invalid_files(env: Env) -> None:
    job = make_job(env)
    fake_pdf = ("files", ("evil.pdf", b"<script>alert(1)</script>", "application/pdf"))
    r = upload(env, job["id"], [fake_pdf])
    assert r.status_code == 422 and r.json()["code"] == "no_valid_files"
    assert set(r.json()) == ERROR_KEYS

    good = resume_files(generate_candidates(1, 3))
    mixed = upload(
        env, job["id"], [fake_pdf, ("files", ("empty.pdf", b"", "application/pdf")), *good]
    )
    assert mixed.status_code == 202
    body = mixed.json()
    assert len(body["accepted"]) == 1
    assert {x["reason"] for x in body["rejected"]} == {"unsupported_file_type", "empty_file"}


def test_upload_size_and_count_limits(make_env: Callable[..., Env]) -> None:
    env = make_env(max_upload_mb=1, max_files_per_upload=2)
    job = make_job(env)
    big = ("files", ("big.pdf", b"%PDF-1.4\n" + b"0" * (1024 * 1024 + 10), "application/pdf"))
    r = upload(env, job["id"], [big])
    assert r.status_code == 422 and "file_too_large" in r.json()["message"]

    three = resume_files(generate_candidates(3, 1))
    r = upload(env, job["id"], three)
    assert r.status_code == 413 and r.json()["code"] == "too_many_files"

    r = env.client.post(
        f"/api/jobs/{job['id']}/candidates",
        files=three[:1],
        headers={"Content-Length": str(50 * 1024 * 1024)},
    )
    assert r.status_code == 413 and r.json()["code"] == "payload_too_large"


def test_upload_to_unknown_job_is_404(env: Env) -> None:
    r = upload(env, "00000000-0000-0000-0000-000000000000", resume_files(generate_candidates(1, 1)))
    assert r.status_code == 404


def test_upload_rate_limit(make_env: Callable[..., Env]) -> None:
    env = make_env(upload_rate_limit_per_minute=2)
    job = make_job(env)
    cands = generate_candidates(3, 1)
    assert upload(env, job["id"], resume_files(cands[:1])).status_code == 202
    assert upload(env, job["id"], resume_files(cands[1:2])).status_code == 202
    third = upload(env, job["id"], resume_files(cands[2:3]))
    assert third.status_code == 429
    assert third.json()["code"] == "rate_limited" and "Retry-After" in third.headers


# ------------------------------------------------------------------ NEEDS_REVIEW paths


def _statuses(env: Env, job_id: str) -> list[tuple[str, str | None]]:
    rows = env.client.get(f"/api/jobs/{job_id}/candidates").json()
    return [(c["status"], c["status_reason"]) for c in rows]


def test_one_invalid_llm_output_is_retried(make_env: Callable[..., Env]) -> None:
    env = make_env(FakeLLM(invalid_resume_outputs=1))
    job = make_job(env)
    upload_synthetic(env, job["id"], count=1)
    assert _statuses(env, job["id"]) == [("READY", None)]
    assert len(env.llm.calls_for(ExtractedResume)) == 2


def test_two_invalid_outputs_mark_needs_review(make_env: Callable[..., Env]) -> None:
    env = make_env(FakeLLM(invalid_resume_outputs=2))
    job = make_job(env)
    upload_synthetic(env, job["id"], count=1)
    assert _statuses(env, job["id"]) == [("NEEDS_REVIEW", "extraction_invalid")]
    assert env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"] == []


def test_llm_unavailable_marks_needs_review(make_env: Callable[..., Env]) -> None:
    env = make_env(FakeLLM(unavailable=True))
    job = make_job(env)
    upload_synthetic(env, job["id"], count=1)
    assert _statuses(env, job["id"]) == [("NEEDS_REVIEW", "llm_unavailable")]


def test_scanned_pdf_marks_needs_review(env: Env) -> None:
    job = make_job(env)
    r = upload(env, job["id"], [("files", ("scan.pdf", pdf_bytes(""), "application/pdf"))])
    assert r.status_code == 202
    assert _statuses(env, job["id"]) == [("NEEDS_REVIEW", "no_extractable_text")]


# ------------------------------------------------------------------ candidate detail + delete


def test_candidate_detail_has_evidence_that_is_real_text(env: Env) -> None:
    job = make_job(env, must=["python", "kubernetes", "aws"])
    upload_synthetic(env, job["id"], count=3)
    top = env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"][0]
    d = env.client.get(f"/api/candidates/{top['candidate_id']}").json()
    assert d["status"] == "READY" and d["raw_text"]
    assert d["identity"]["email"].endswith("@example.com")
    assert d["profile"]["skills"]
    exp = d["match"]["explanation"]
    assert exp["summary"] == "Fake explanation." and exp["summary_source"] == "llm"
    quoted = [i for i in exp["items"] if i["quote"]]
    assert quoted
    for item in quoted:
        assert d["raw_text"][item["start"] : item["end"]].split() == item["quote"].split()
    assert "human makes the hiring decision" in d["notice"]

    blind = env.client.get(f"/api/candidates/{top['candidate_id']}", params={"blind": True}).json()
    assert blind["identity"] is None


def test_delete_candidate_removes_everything(env: Env, db: Session) -> None:
    job = make_job(env)
    upload_synthetic(env, job["id"], count=2)
    victim = env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"][0]["candidate_id"]
    assert row_count(db, CandidateProfile) == 2

    assert env.client.delete(f"/api/candidates/{victim}").status_code == 204
    assert env.client.get(f"/api/candidates/{victim}").status_code == 404
    assert env.client.delete(f"/api/candidates/{victim}").status_code == 404

    db.expire_all()
    for model in (Candidate, CandidateProfile, CandidateIdentity, Match):
        assert row_count(db, model) == 1
    ids = [
        i["candidate_id"] for i in env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]
    ]
    assert victim not in ids


def test_delete_job_removes_its_candidates(env: Env, db: Session) -> None:
    job = make_job(env)
    upload_synthetic(env, job["id"], count=2)
    assert env.client.delete(f"/api/jobs/{job['id']}").status_code == 204
    db.expire_all()
    for model in (Job, Candidate, CandidateProfile, CandidateIdentity, Match):
        assert row_count(db, model) == 0


# ------------------------------------------------------------------ requirements editing


def test_editing_requirements_rescores_and_normalizes(env: Env) -> None:
    job = make_job(env, must=["python"])
    upload_synthetic(env, job["id"], count=4)
    before = {
        i["candidate_id"]: i["total_score"]
        for i in env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]
    }
    new_reqs: dict[str, Any] = {
        "must_have_skills": ["K8s", "Terraform", "Kafka"],
        "nice_to_have_skills": [],
        "min_years_experience": 8,
        "languages": ["German"],
        "education_level": "master",
    }
    r = env.client.patch(f"/api/jobs/{job['id']}/requirements", json=new_reqs)
    assert r.status_code == 200
    assert r.json()["requirements"]["must_have_skills"] == ["kubernetes", "terraform", "kafka"]
    after = env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]
    assert {i["candidate_id"]: i["total_score"] for i in after} != before
    assert all(set(i["missing_must_have"]) <= {"kubernetes", "terraform", "kafka"} for i in after)


# ------------------------------------------------------------------ weights, preview, stats


def test_weights_preview_and_save(env: Env) -> None:
    job = make_job(env, must=["python", "kubernetes"], years=3)
    upload_synthetic(env, job["id"], count=8)
    current = env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]

    only_exp = {
        "must_have": 0, "nice_to_have": 0, "experience": 1, "semantic": 0, "language_education": 0,
    }  # fmt: skip
    preview = env.client.post(f"/api/jobs/{job['id']}/ranking/preview", json=only_exp)
    assert preview.status_code == 200
    rows = preview.json()
    assert len(rows) == len(current)
    assert {r["rank_current"] for r in rows} == set(range(1, len(rows) + 1))
    assert {r["rank_preview"] for r in rows} == set(range(1, len(rows) + 1))
    # preview does not persist
    assert env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"] == current

    saved = env.client.put("/api/settings/weights", json=only_exp)
    assert saved.status_code == 200 and saved.json()["is_override"] is True
    after = env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"]
    by_id = {r["candidate_id"]: r["total_preview"] for r in rows}
    assert all(abs(i["total_score"] - by_id[i["candidate_id"]]) < 0.05 for i in after)

    reset = env.client.delete("/api/settings/weights")
    assert reset.json()["is_override"] is False
    assert env.client.get(f"/api/jobs/{job['id']}/ranking").json()["items"] == current


def test_invalid_weights_rejected(env: Env) -> None:
    zero = dict.fromkeys(
        ("must_have", "nice_to_have", "experience", "semantic", "language_education"), 0
    )
    assert env.client.put("/api/settings/weights", json=zero).status_code == 422


def test_job_stats(env: Env) -> None:
    job = make_job(env, must=["python", "kubernetes", "terraform"])
    upload_synthetic(env, job["id"], count=6)
    s = env.client.get(f"/api/jobs/{job['id']}/stats").json()
    assert s["candidates_total"] == 6 and s["by_status"]["READY"] == 6 and s["scored"] == 6
    assert sum(b["count"] for b in s["score_histogram"]) == 6
    assert s["median_score"] is not None
    assert s["most_missing_must_have"]


# ------------------------------------------------------------------ misc


def test_security_headers_present(env: Env) -> None:
    r = env.client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert "default-src 'self'" in r.headers["content-security-policy"]


def test_docx_upload_works_too(env: Env) -> None:
    job = make_job(env)
    cand = generate_candidates(1, 11)[0]
    from scripts.generate_resumes import render_text

    r = upload(
        env, job["id"], [("files", ("cv.docx", docx_bytes(render_text(cand)), "application/x"))]
    )
    assert r.status_code == 202
    assert _statuses(env, job["id"]) == [("READY", None)]


def test_blank_secrets_count_as_unset() -> None:
    from app.config import Settings

    s = Settings(_env_file=None, api_key_admin="", openai_api_key="  ")  # type: ignore[call-arg, arg-type]
    assert s.api_key_admin is None and s.openai_api_key is None
