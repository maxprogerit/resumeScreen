"""Background processing of an uploaded resume.

Logging policy: only ids and exception class names are logged. Resume text, extracted
fields and exception messages (which may embed document content) are never logged.
"""

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.llm.client import LLMClient, LLMInvalidOutput, LLMUnavailable
from app.models import (
    Candidate,
    CandidateIdentity,
    CandidateProfile,
    CandidateStatus,
    Job,
)
from app.parsing import ParseError, UnsupportedFileType, extract_document
from app.services.extraction import ExtractionFailed, extract_resume
from app.services.matching import (
    embedding_text_for_profile,
    ensure_job_embedding,
    rescore_candidate,
    rescore_job,
    score_candidate,
)
from app.services.weights import load_weights
from app.vectors import to_bytes

log = logging.getLogger(__name__)

LLMProvider = Callable[[], LLMClient]


def text_fingerprint(text: str) -> str:
    """Hash of the whitespace/case-normalised text: the same resume as PDF and DOCX collides."""
    return hashlib.sha256(" ".join(text.lower().split()).encode()).hexdigest()


def content_fingerprint(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _is_duplicate_text(db: Session, candidate: Candidate) -> bool:
    if candidate.job_id is None or candidate.text_hash is None:
        return False
    return (
        db.scalar(
            select(Candidate.id)
            .where(
                Candidate.job_id == candidate.job_id,
                Candidate.text_hash == candidate.text_hash,
                Candidate.id != candidate.id,
            )
            .limit(1)
        )
        is not None
    )


def _needs_review(db: Session, candidate: Candidate, reason: str) -> None:
    candidate.status = CandidateStatus.NEEDS_REVIEW
    candidate.status_reason = reason
    db.commit()


def process_candidate(
    session_factory: sessionmaker[Session],
    llm_provider: LLMProvider,
    settings: Settings,
    candidate_id: object,
    data: bytes | None,
) -> None:
    """`data=None` re-processes from the stored raw_text (retry of a NEEDS_REVIEW candidate)."""
    with session_factory() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:  # deleted while queued
            return
        try:
            _process(db, candidate, llm_provider, settings, data)
        except Exception as exc:  # noqa: BLE001 - last-resort guard, status must not stay PROCESSING
            db.rollback()
            log.warning("candidate %s: processing_error (%s)", candidate_id, type(exc).__name__)
            candidate = db.get(Candidate, candidate_id)
            if candidate is not None:
                _needs_review(db, candidate, "processing_error")


def _process(
    db: Session,
    candidate: Candidate,
    llm_provider: LLMProvider,
    settings: Settings,
    data: bytes | None,
) -> None:
    if data is not None:
        try:
            parsed = extract_document(data)
        except UnsupportedFileType:
            return _needs_review(db, candidate, "unsupported_file_type")
        except ParseError as exc:
            return _needs_review(db, candidate, str(exc))
        candidate.raw_text = parsed.text
        candidate.parse_notes = parsed.notes
        candidate.text_hash = text_fingerprint(parsed.text)
        if _is_duplicate_text(db, candidate):
            return _needs_review(db, candidate, "duplicate_resume")
    elif not candidate.raw_text:
        return _needs_review(db, candidate, "no_extractable_text")
    candidate.profile = None  # a retry replaces any earlier derived data
    candidate.identity = None
    db.commit()

    try:
        llm = llm_provider()
        extracted = extract_resume(llm, candidate.raw_text, loose="ocr" in candidate.parse_notes)
    except LLMUnavailable:
        return _needs_review(db, candidate, "llm_unavailable")
    except ExtractionFailed as exc:
        return _needs_review(db, candidate, exc.reason)

    profile = CandidateProfile(
        candidate_id=candidate.id,
        skills=extracted.skills,
        total_years_experience=extracted.total_years_experience,
        roles=[r.model_dump() for r in extracted.roles],
        education=[e.model_dump(mode="json") for e in extracted.education],
        languages=extracted.languages,
        certifications=extracted.certifications,
    )
    candidate.profile = profile
    candidate.identity = CandidateIdentity(
        display_name=(extracted.identity.display_name or "")[:200] or None,
        email=(extracted.identity.email or "")[:320] or None,
        phone=(extracted.identity.phone or "")[:50] or None,
    )
    try:
        vec = llm.embed([embedding_text_for_profile(profile)])[0]
        profile.embedding = to_bytes(vec)
        profile.embedding_model = llm.embedding_model
    except (LLMUnavailable, LLMInvalidOutput):
        log.warning("candidate %s: embedding unavailable", candidate.id)

    candidate.status = CandidateStatus.READY
    candidate.status_reason = None
    db.commit()

    job = db.get(Job, candidate.job_id) if candidate.job_id else None
    if job is not None:
        ensure_job_embedding(db, job, llm)
        score_candidate(
            db, job, candidate, llm=llm, weights=load_weights(db, settings), settings=settings
        )


def rescore_job_task(
    session_factory: sessionmaker[Session],
    llm_provider: LLMProvider,
    settings: Settings,
    job_id: object,
) -> None:
    """Background re-score with LLM-written evidence/explanations (after requirements edits)."""
    try:
        llm: LLMClient | None = llm_provider()
    except LLMUnavailable:
        return  # the synchronous deterministic rescore already happened
    with session_factory() as db:
        job = db.get(Job, job_id)
        if job is None:
            return
        try:
            rescore_job(db, job, llm=llm, weights=load_weights(db, settings), settings=settings)
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("job %s: rescore failed (%s)", job_id, type(exc).__name__)


STALE_AFTER = timedelta(minutes=10)


def recover_interrupted(
    session_factory: sessionmaker[Session],
    stale_after: timedelta = STALE_AFTER,
    now: datetime | None = None,
) -> int:
    """Candidates stuck in PROCESSING longer than `stale_after` lost their background task
    (restart or crash). Mark them for review (a retry button is available). Age-based, so a
    second worker starting up does not touch another worker's in-flight jobs."""
    cutoff = (now or datetime.now(UTC)) - stale_after
    with session_factory() as db:
        result = db.execute(
            update(Candidate)
            .where(
                Candidate.status == CandidateStatus.PROCESSING,
                or_(
                    Candidate.processing_started_at.is_(None),
                    Candidate.processing_started_at < cutoff,
                ),
            )
            .values(status=CandidateStatus.NEEDS_REVIEW, status_reason="interrupted")
        )
        db.commit()
        return int(result.rowcount or 0)  # type: ignore[attr-defined]


def rescore_candidate_task(
    session_factory: sessionmaker[Session],
    llm_provider: LLMProvider,
    settings: Settings,
    candidate_id: object,
) -> None:
    """Background re-score of one candidate (after a manual profile edit) with LLM wording."""
    try:
        llm: LLMClient | None = llm_provider()
    except LLMUnavailable:
        return
    with session_factory() as db:
        candidate = db.get(Candidate, candidate_id)
        if candidate is None:
            return
        try:
            rescore_candidate(
                db, candidate, llm=llm, weights=load_weights(db, settings), settings=settings
            )
        except Exception as exc:  # noqa: BLE001
            db.rollback()
            log.warning("candidate %s: rescore failed (%s)", candidate_id, type(exc).__name__)
