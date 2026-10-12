"""ORM models.

Fairness boundary: `CandidateIdentity` (name, email, phone) lives in its own table
and is for display only. Scoring code reads `CandidateProfile` and never touches identity.
Every table derived from a candidate has ON DELETE CASCADE so that deleting a
candidate removes all derived data.
"""

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    Uuid,
)
from sqlalchemy import UniqueConstraint as Unique
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> datetime:
    return datetime.now(UTC)


class CandidateStatus(enum.StrEnum):
    PROCESSING = "PROCESSING"
    READY = "READY"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    title: Mapped[str] = mapped_column(String(200))
    description_text: Mapped[str] = mapped_column(Text)
    # {must_have_skills, nice_to_have_skills, min_years_experience, languages, education_level}
    requirements: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)  # float32 bytes
    embedding_model: Mapped[str | None] = mapped_column(String(100), default=None)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    matches: Mapped[list["Match"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Upload target. SET NULL on job delete: the candidate may be matched against other jobs.
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("jobs.id", ondelete="SET NULL"), index=True, default=None
    )
    source_filename: Mapped[str] = mapped_column(String(255))
    raw_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[CandidateStatus] = mapped_column(
        Enum(CandidateStatus, native_enum=False, length=20), default=CandidateStatus.PROCESSING
    )
    # Short machine-readable reason for NEEDS_REVIEW. Never contains resume text.
    status_reason: Mapped[str | None] = mapped_column(String(100), default=None)
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    # sha256 of the uploaded bytes / of the normalised text: duplicate detection within a job.
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    text_hash: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    # Set when processing starts (upload or retry); the stale-job sweep keys on it.
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=_now
    )
    # e.g. ["ocr", "hidden_text_removed"]: how the text was obtained, shown to the recruiter.
    parse_notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)

    profile: Mapped["CandidateProfile | None"] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", passive_deletes=True
    )
    identity: Mapped["CandidateIdentity | None"] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", passive_deletes=True
    )
    matches: Mapped[list["Match"]] = relationship(
        back_populates="candidate", cascade="all, delete-orphan", passive_deletes=True
    )


class CandidateProfile(Base):
    """Scoring data only. No personal identifiers here."""

    __tablename__ = "candidate_profiles"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    skills: Mapped[list[str]] = mapped_column(JSON, default=list)
    total_years_experience: Mapped[float | None] = mapped_column(Float, default=None)
    roles: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    education: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    languages: Mapped[list[str]] = mapped_column(JSON, default=list)
    certifications: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Embedding of a de-identified text (see Stage 4), float32 bytes.
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, default=None)
    embedding_model: Mapped[str | None] = mapped_column(String(100), default=None)
    # True once a human has corrected the extracted profile.
    manually_edited: Mapped[bool] = mapped_column(Boolean, default=False)

    candidate: Mapped[Candidate] = relationship(back_populates="profile")


class CandidateIdentity(Base):
    """Display-only personal fields. Must never be imported by scoring code."""

    __tablename__ = "candidate_identity"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), primary_key=True
    )
    display_name: Mapped[str | None] = mapped_column(String(200), default=None)
    email: Mapped[str | None] = mapped_column(String(320), default=None)
    phone: Mapped[str | None] = mapped_column(String(50), default=None)

    candidate: Mapped[Candidate] = relationship(back_populates="identity")


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (Unique("job_id", "candidate_id", name="uq_match_job_candidate"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    job_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), index=True
    )
    total_score: Mapped[float] = mapped_column(Float)
    breakdown: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    model_version: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    job: Mapped[Job] = relationship(back_populates="matches")
    candidate: Mapped[Candidate] = relationship(back_populates="matches")


class AppSetting(Base):
    """Tiny key/value store (currently only the scoring weights override)."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RateLimitHit(Base):
    """One row per rate-limited request; shared by all server processes."""

    __tablename__ = "rate_limit_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(100), index=True)
    ts: Mapped[float] = mapped_column(Float, index=True)
