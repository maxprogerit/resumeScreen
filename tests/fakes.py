"""Test doubles. Tests never call the real OpenAI API."""

import re
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel

from app.llm.client import LLMInvalidOutput, LLMUnavailable, T
from app.llm.offline import hash_embed
from app.schemas import (
    EducationLevel,
    EvidenceResponse,
    ExplanationResponse,
    ExtractedEducation,
    ExtractedIdentity,
    ExtractedResume,
    ExtractedRole,
    JobRequirements,
)
from app.skills import canonical_skill, find_skill

KNOWN_SKILLS = [
    "Python", "FastAPI", "PostgreSQL", "Docker", "REST", "SQLAlchemy", "Django", "Flask",
    "Redis", "Kafka", "Kubernetes", "AWS", "RabbitMQ", "Git", "SQL", "Pandas", "scikit-learn",
    "Airflow", "Spark", "Snowflake", "PyTorch", "Machine Learning", "JavaScript", "TypeScript",
    "React", "HTML", "CSS", "Vue", "Node.js", "GraphQL", "Figma", "Terraform", "Linux",
    "CI/CD", "Azure", "Google Cloud",
]  # fmt: skip


def rule_based_resume(text: str) -> ExtractedResume:
    """Stand-in 'LLM' for tests: a regex reader over the synthetic resume layout.

    Name/contact come from the first two lines (display only). Skills come from a fixed
    list. It deliberately ignores everything else, including injected instructions.
    """
    lines = text.split("\n")
    name = lines[0].strip() if lines else None
    email = re.search(r"[\w.]+@[\w.]+", text)
    phone = re.search(r"\+?\d[\d-]{7,}", text)
    skills = [s for s in KNOWN_SKILLS if find_skill(text, s)]
    roles = [
        ExtractedRole(title=m[1].strip(), company=m[2].strip(), start=m[3], end=m[4], summary="")
        for m in re.finditer(r"^(.+?) - (.+)\n(\d{4}-\d{2}) - (\d{4}-\d{2}|present)", text, re.M)
    ]
    edu: list[ExtractedEducation] = []
    for word, level in (
        ("PhD", EducationLevel.PHD),
        ("Master", EducationLevel.MASTER),
        ("Bachelor", EducationLevel.BACHELOR),
    ):
        if word in text:
            edu.append(ExtractedEducation(level=level, field=None, institution=None))
            break
    langs = []
    if m := re.search(r"^LANGUAGES\n(.+)$", text, re.M):
        langs = [x.strip() for x in m[1].split(",")]
    return ExtractedResume(
        identity=ExtractedIdentity(
            display_name=name, email=email[0] if email else None, phone=phone[0] if phone else None
        ),
        skills=skills,
        total_years_experience=None,
        roles=roles,
        education=edu,
        languages=langs,
        certifications=[],
    )


def unwrap(user: str) -> str:
    """Resume text from inside the <resume> tags of a user prompt."""
    if "<resume>\n" in user:
        return user.split("<resume>\n", 1)[1].rsplit("\n</resume>", 1)[0]
    return user


class FakeLLM:
    chat_model = "fake-chat"
    embedding_model = "fake-embed"

    def __init__(
        self,
        *,
        resume: Callable[[str], ExtractedResume] = rule_based_resume,
        requirements: JobRequirements | None = None,
        evidence: EvidenceResponse | Callable[[str], EvidenceResponse] | None = None,
        summary: str = "Fake explanation.",
        invalid_resume_outputs: int = 0,
        unavailable: bool = False,
        parse_error: Exception | None = None,
    ) -> None:
        self.resume = resume
        self.requirements = requirements
        self.evidence = evidence
        self.summary = summary
        self.invalid_resume_outputs = invalid_resume_outputs
        self.unavailable = unavailable
        self.parse_error = parse_error
        self.calls: list[tuple[str, str, str]] = []  # (schema name, system, user)
        self.embed_inputs: list[str] = []

    def parse(self, *, system: str, user: str, schema: type[T]) -> T:
        self.calls.append((schema.__name__, system, user))
        if self.unavailable:
            raise LLMUnavailable("fake unavailable")
        if self.parse_error is not None:
            raise self.parse_error
        result: BaseModel
        if schema is ExtractedResume:
            if self.invalid_resume_outputs > 0:
                self.invalid_resume_outputs -= 1
                raise LLMInvalidOutput("fake invalid")
            result = self.resume(unwrap(user))
        elif schema is JobRequirements:
            if self.requirements is None:
                raise LLMInvalidOutput("no requirements configured")
            result = self.requirements
        elif schema is EvidenceResponse:
            ev: Any = self.evidence
            result = ev(user) if callable(ev) else (ev or EvidenceResponse(items=[]))
        elif schema is ExplanationResponse:
            result = ExplanationResponse(summary=self.summary)
        else:
            raise AssertionError(f"unexpected schema {schema}")
        assert isinstance(result, schema)  # noqa: S101
        return result

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if self.unavailable:
            raise LLMUnavailable("fake unavailable")
        self.embed_inputs.extend(texts)
        return [hash_embed(t) for t in texts]

    def calls_for(self, schema: type[BaseModel]) -> list[tuple[str, str, str]]:
        return [c for c in self.calls if c[0] == schema.__name__]


def skills_requirements(
    must: Sequence[str], nice: Sequence[str] = (), years: float | None = None
) -> JobRequirements:
    return JobRequirements(
        must_have_skills=list(must),
        nice_to_have_skills=list(nice),
        min_years_experience=years,
        languages=[],
        education_level=None,
    )


__all__ = ["FakeLLM", "canonical_skill", "rule_based_resume", "skills_requirements"]
