"""Glue between the DB and the pure scoring core: build scoring inputs, score, persist matches."""

import logging
from typing import Any

from sqlalchemy import ColumnElement, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.client import LLMClient, LLMInvalidOutput, LLMUnavailable
from app.models import Candidate, CandidateProfile, CandidateStatus, Job, Match
from app.schemas import EDUCATION_RANK, EducationLevel, JobRequirements, Weights
from app.services.evidence import build_evidence, items_to_json
from app.services.explain import build_summary
from app.services.scoring import (
    SCORING_VERSION,
    ScoringJob,
    ScoringProfile,
    combine,
    raw_components,
)
from app.skills import canonical_languages, canonical_skills
from app.vectors import from_bytes, to_bytes

log = logging.getLogger(__name__)


def embedding_text_for_profile(profile: CandidateProfile) -> str:
    """De-identified text used for the semantic score.

    Built from the structured profile only: no name, contact data, company or institution
    names (company/school can act as proxies for background). The raw resume text is never
    embedded, so a name or gendered wording in the document cannot move the semantic score.
    """
    lines = [f"Skills: {', '.join(profile.skills)}"]
    for role in profile.roles:
        lines.append(f"Role: {role.get('title', '')}. {role.get('summary', '')}".strip())
    for edu in profile.education:
        lines.append(f"Education: {edu.get('level', '')} {edu.get('field') or ''}".strip())
    if profile.certifications:
        lines.append(f"Certifications: {', '.join(profile.certifications)}")
    if profile.languages:
        lines.append(f"Languages: {', '.join(profile.languages)}")
    return "\n".join(lines)


def embedding_text_for_job(job: Job) -> str:
    return f"{job.title}\n{job.description_text}"


def highest_level(education: list[dict[str, Any]]) -> EducationLevel | None:
    levels = [EducationLevel(e["level"]) for e in education if e.get("level")]
    return max(levels, key=lambda lv: EDUCATION_RANK[lv]) if levels else None


def to_scoring_profile(profile: CandidateProfile) -> ScoringProfile:
    return ScoringProfile(
        skills=frozenset(canonical_skills(profile.skills)),
        years_experience=profile.total_years_experience,
        education_level=highest_level(profile.education),
        languages=frozenset(canonical_languages(profile.languages)),
        embedding=from_bytes(profile.embedding),
        embedding_model=profile.embedding_model,
    )


def to_scoring_job(job: Job) -> ScoringJob:
    req = JobRequirements.model_validate(job.requirements)
    return ScoringJob(
        must_have=tuple(canonical_skills(req.must_have_skills)),
        nice_to_have=tuple(canonical_skills(req.nice_to_have_skills)),
        min_years=req.min_years_experience,
        languages=tuple(canonical_languages(req.languages)),
        education_level=req.education_level,
        embedding=from_bytes(job.embedding),
        embedding_model=job.embedding_model,
    )


def ensure_job_embedding(db: Session, job: Job, llm: LLMClient | None) -> None:
    if job.embedding is not None or llm is None:
        return
    try:
        vec = llm.embed([embedding_text_for_job(job)])[0]
    except (LLMUnavailable, LLMInvalidOutput):
        log.warning("job %s: embedding unavailable", job.id)
        return
    job.embedding = to_bytes(vec)
    job.embedding_model = llm.embedding_model
    db.commit()


def score_candidate(
    db: Session,
    job: Job,
    candidate: Candidate,
    *,
    llm: LLMClient | None,
    weights: Weights,
    settings: Settings,
) -> Match:
    """Score one candidate against one job and upsert the Match.

    `llm` is optional: without it evidence uses rule-based quotes and the explanation uses
    the deterministic template. The score itself never depends on the LLM at this stage.
    """
    assert candidate.profile is not None  # noqa: S101
    components = raw_components(
        to_scoring_profile(candidate.profile),
        to_scoring_job(job),
        semantic_floor=settings.semantic_floor,
        semantic_ceil=settings.semantic_ceil,
    )
    breakdown = combine(components, weights)
    items, meta = build_evidence(
        candidate.raw_text,
        breakdown["components"],
        llm,
        loose="ocr" in (candidate.parse_notes or []),
    )
    summary, source = build_summary(llm, breakdown, items)
    explanation = {
        "summary": summary,
        "summary_source": source,
        "items": items_to_json(items),
        **meta,
    }
    model_version = f"{SCORING_VERSION}/{llm.chat_model if llm is not None else 'no-llm'}"

    match = db.scalar(
        select(Match).where(Match.job_id == job.id, Match.candidate_id == candidate.id)
    )
    if match is None:
        match = Match(job_id=job.id, candidate_id=candidate.id, total_score=0.0)
        db.add(match)
    match.total_score = breakdown["total"]
    match.breakdown = breakdown
    match.explanation = explanation
    match.model_version = model_version
    db.commit()
    return match


def is_member(job_id: object) -> ColumnElement[bool]:
    """Candidates belonging to a job: uploaded for it, or matched to it from elsewhere."""
    return or_(
        Candidate.job_id == job_id,
        Candidate.id.in_(select(Match.candidate_id).where(Match.job_id == job_id)),
    )


def rescore_candidate(
    db: Session,
    candidate: Candidate,
    *,
    llm: LLMClient | None,
    weights: Weights,
    settings: Settings,
) -> int:
    """Re-score one candidate against every job it is matched to."""
    if candidate.profile is None:
        return 0
    job_ids = {m.job_id for m in candidate.matches}
    if candidate.job_id:
        job_ids.add(candidate.job_id)
    count = 0
    for job_id in job_ids:
        job = db.get(Job, job_id)
        if job is not None:
            score_candidate(db, job, candidate, llm=llm, weights=weights, settings=settings)
            count += 1
    return count


def rescore_job(
    db: Session, job: Job, *, llm: LLMClient | None, weights: Weights, settings: Settings
) -> int:
    """Re-score every READY candidate of a job (used after requirements are edited)."""
    ensure_job_embedding(db, job, llm)
    candidates = db.scalars(
        select(Candidate).where(is_member(job.id), Candidate.status == CandidateStatus.READY)
    ).all()
    count = 0
    for candidate in candidates:
        if candidate.profile is None:
            continue
        score_candidate(db, job, candidate, llm=llm, weights=weights, settings=settings)
        count += 1
    return count
