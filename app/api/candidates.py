import contextlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request
from sqlalchemy.orm import Session

from app.api.schemas import (
    CandidateOut,
    IdentityOut,
    MatchedJob,
    MatchOut,
    ProfileOut,
)
from app.config import Settings
from app.deps import DB, LLMProviderDep, SettingsDep, get_candidate_or_404
from app.errors import ApiError
from app.llm.client import LLMClient, LLMUnavailable
from app.models import Candidate, CandidateStatus, Job
from app.services.matching import rescore_candidate
from app.services.pipeline import (
    LLMProvider,
    process_candidate,
    rescore_candidate_task,
)
from app.services.profile_edit import ProfileEdit, apply_profile_edit
from app.services.weights import load_weights

router = APIRouter(tags=["candidates"])


def _candidate_out(
    db: Session, c: Candidate, job_id: uuid.UUID | None, blind: bool
) -> CandidateOut:
    db.expire(c, ["matches"])  # matches may have just been added by a rescore
    matches = sorted(c.matches, key=lambda m: m.created_at)
    chosen_id = job_id or c.job_id
    match = next((m for m in matches if m.job_id == chosen_id), matches[0] if matches else None)
    matched_jobs: list[MatchedJob] = []
    for m in matches:
        job = db.get(Job, m.job_id)
        if job is not None:
            matched_jobs.append(
                MatchedJob(job_id=m.job_id, title=job.title, total_score=m.total_score)
            )
    return CandidateOut(
        id=c.id,
        job_id=match.job_id if match else c.job_id,
        source_filename=c.source_filename,
        status=c.status,
        status_reason=c.status_reason,
        is_demo=c.is_demo,
        created_at=c.created_at,
        raw_text=c.raw_text,
        parse_notes=c.parse_notes or [],
        manually_edited=bool(c.profile and c.profile.manually_edited),
        matched_jobs=matched_jobs,
        profile=ProfileOut(
            skills=c.profile.skills,
            total_years_experience=c.profile.total_years_experience,
            roles=c.profile.roles,
            education=c.profile.education,
            languages=c.profile.languages,
            certifications=c.profile.certifications,
        )
        if c.profile
        else None,
        identity=IdentityOut(
            display_name=c.identity.display_name, email=c.identity.email, phone=c.identity.phone
        )
        if c.identity and not blind
        else None,
        match=MatchOut(
            job_id=match.job_id,
            total_score=match.total_score,
            breakdown=match.breakdown,
            explanation=match.explanation,
            model_version=match.model_version,
        )
        if match
        else None,
    )


def _try_llm(provider: LLMProvider) -> LLMClient | None:
    with contextlib.suppress(LLMUnavailable):
        return provider()
    return None


def _rescore_now(db: Session, c: Candidate, settings: Settings) -> None:
    rescore_candidate(db, c, llm=None, weights=load_weights(db, settings), settings=settings)


@router.get("/candidates/{candidate_id}", response_model=CandidateOut)
def get_candidate(
    candidate_id: uuid.UUID,
    db: DB,
    blind: Annotated[bool, Query(description="omit name/email/phone")] = False,
    job_id: Annotated[uuid.UUID | None, Query(description="which job's match to show")] = None,
) -> CandidateOut:
    return _candidate_out(db, get_candidate_or_404(db, candidate_id), job_id, blind)


@router.patch("/candidates/{candidate_id}/profile", response_model=CandidateOut)
def edit_profile(
    candidate_id: uuid.UUID,
    body: ProfileEdit,
    background: BackgroundTasks,
    request: Request,
    db: DB,
    settings: SettingsDep,
    provider: LLMProviderDep,
) -> CandidateOut:
    """Correct the extracted profile, or enter it by hand when extraction failed.

    All of the candidate's matches are re-scored right away (deterministically); evidence
    quotes and explanation wording are refreshed in the background.
    """
    c = get_candidate_or_404(db, candidate_id)
    if c.status is CandidateStatus.PROCESSING:
        raise ApiError(409, "still_processing", "Candidate is still being processed")
    apply_profile_edit(c, body, _try_llm(provider))
    db.commit()
    _rescore_now(db, c, settings)
    background.add_task(
        rescore_candidate_task, request.app.state.session_factory, provider, settings, c.id
    )
    return _candidate_out(db, c, None, blind=False)


@router.delete("/candidates/{candidate_id}", status_code=204)
def delete_candidate(candidate_id: uuid.UUID, db: DB) -> None:
    """Hard delete: the candidate row and, via ON DELETE CASCADE, the profile, identity,
    embedding and all matches/explanations."""
    c = get_candidate_or_404(db, candidate_id)
    db.delete(c)
    db.commit()


@router.post("/candidates/{candidate_id}/reprocess", status_code=202)
def reprocess_candidate(
    candidate_id: uuid.UUID,
    background: BackgroundTasks,
    request: Request,
    db: DB,
    settings: SettingsDep,
    provider: LLMProviderDep,
) -> dict[str, str]:
    """Retry a NEEDS_REVIEW candidate from the stored text (the original file is not kept)."""
    c = get_candidate_or_404(db, candidate_id)
    if c.status is not CandidateStatus.NEEDS_REVIEW:
        raise ApiError(409, "not_retryable", "Only candidates in NEEDS_REVIEW can be reprocessed")
    c.status = CandidateStatus.PROCESSING
    c.status_reason = None
    c.processing_started_at = datetime.now(UTC)
    db.commit()
    background.add_task(
        process_candidate, request.app.state.session_factory, provider, settings, c.id, None
    )
    return {"candidate_id": str(c.id), "status": CandidateStatus.PROCESSING.value}
