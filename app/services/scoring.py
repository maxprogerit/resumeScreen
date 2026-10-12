"""Deterministic scoring core.

FAIRNESS BOUNDARY: this module only accepts `ScoringProfile` and `ScoringJob`, which contain
job-relevant fields (skills, years, education level, languages, embedding of a de-identified
profile text). It never sees names, contact data, filenames or raw resume text, and it must
not import `app.models`. tests/test_fairness.py enforces both properties.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

from app.schemas import EDUCATION_RANK, EducationLevel, Weights

SCORING_VERSION = "v1"
COMPONENTS = ("must_have", "nice_to_have", "experience", "semantic", "language_education")

Vector = npt.NDArray[np.float32]


@dataclass(frozen=True)
class ScoringProfile:
    skills: frozenset[str]  # canonical
    years_experience: float | None
    education_level: EducationLevel | None  # highest
    languages: frozenset[str]  # canonical
    embedding: Vector | None
    embedding_model: str | None


@dataclass(frozen=True)
class ScoringJob:
    must_have: tuple[str, ...]  # canonical
    nice_to_have: tuple[str, ...]
    min_years: float | None
    languages: tuple[str, ...]
    education_level: EducationLevel | None
    embedding: Vector | None
    embedding_model: str | None


def cosine(a: Vector, b: Vector) -> float:
    na, nb = float(np.linalg.norm(a)), float(np.linalg.norm(b))
    if na == 0 or nb == 0:
        return 0.0
    return float(np.dot(a, b) / (na * nb))


def _coverage(required: tuple[str, ...], have: frozenset[str]) -> dict[str, Any]:
    if not required:
        return {"score": None, "matched": [], "missing": []}
    matched = [s for s in required if s in have]
    missing = [s for s in required if s not in have]
    return {"score": len(matched) / len(required), "matched": matched, "missing": missing}


def _experience(profile: ScoringProfile, job: ScoringJob) -> dict[str, Any]:
    if not job.min_years:
        return {"score": None, "years": profile.years_experience, "required": job.min_years}
    years = profile.years_experience
    score = 0.0 if years is None else min(years / job.min_years, 1.0)
    return {"score": score, "years": years, "required": job.min_years}


def _semantic(
    profile: ScoringProfile, job: ScoringJob, floor: float, ceil: float
) -> dict[str, Any]:
    a, b = profile.embedding, job.embedding
    if (
        a is None
        or b is None
        or a.shape != b.shape
        or profile.embedding_model != job.embedding_model
    ):
        return {"score": None, "cosine": None, "reason": "embedding_unavailable"}
    cos = cosine(a, b)
    score = min(max((cos - floor) / (ceil - floor), 0.0), 1.0)
    return {"score": score, "cosine": round(cos, 4), "floor": floor, "ceil": ceil}


def _language_education(profile: ScoringProfile, job: ScoringJob) -> dict[str, Any]:
    parts: list[float] = []
    detail: dict[str, Any] = {}
    if job.languages:
        matched = [x for x in job.languages if x in profile.languages]
        missing = [x for x in job.languages if x not in profile.languages]
        parts.append(len(matched) / len(job.languages))
        detail["languages_matched"] = matched
        detail["languages_missing"] = missing
    if job.education_level is not None:
        need = EDUCATION_RANK[job.education_level]
        have = EDUCATION_RANK[profile.education_level] if profile.education_level else 0
        # One level short earns half credit, two or more none.
        parts.append(1.0 if have >= need else max(0.0, 1.0 - 0.5 * (need - have)))
        detail["education_required"] = job.education_level.value
        detail["education_have"] = (
            profile.education_level.value if profile.education_level else None
        )
    return {"score": sum(parts) / len(parts) if parts else None, **detail}


def raw_components(
    profile: ScoringProfile, job: ScoringJob, *, semantic_floor: float, semantic_ceil: float
) -> dict[str, dict[str, Any]]:
    """Per-component scores in [0, 1] (None = not applicable) plus matched/missing detail."""
    return {
        "must_have": _coverage(job.must_have, profile.skills),
        "nice_to_have": _coverage(job.nice_to_have, profile.skills),
        "experience": _experience(profile, job),
        "semantic": _semantic(profile, job, semantic_floor, semantic_ceil),
        "language_education": _language_education(profile, job),
    }


def combine(components: Mapping[str, Mapping[str, Any]], weights: Weights) -> dict[str, Any]:
    """Weighted sum over applicable components; weights are renormalised over those.

    Needs only the stored component scores, so changing weights never requires re-running
    extraction, embeddings or any LLM call.
    """
    raw_w = weights.model_dump()
    applicable = [n for n in COMPONENTS if components.get(n, {}).get("score") is not None]
    wsum = sum(raw_w[n] for n in applicable)
    out: dict[str, Any] = {}
    total = 0.0
    for name in COMPONENTS:
        comp = dict(components.get(name, {"score": None}))
        score = comp.get("score")
        if score is not None and wsum > 0:
            eff = raw_w[name] / wsum
            points = score * eff * 100
        else:
            eff, points = 0.0, 0.0
        comp.update(applicable=score is not None, weight=round(eff, 4), points=round(points, 2))
        total += points
        out[name] = comp
    return {"scoring_version": SCORING_VERSION, "total": round(total, 1), "components": out}
