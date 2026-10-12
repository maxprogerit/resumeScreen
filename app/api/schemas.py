import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models import CandidateStatus
from app.schemas import JobRequirements


class JobCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description_text: str = Field(min_length=20, max_length=50_000)
    # If omitted the LLM extracts them; if the LLM is unavailable they start empty.
    requirements: JobRequirements | None = None


class JobOut(BaseModel):
    id: uuid.UUID
    title: str
    description_text: str
    requirements: JobRequirements
    created_at: datetime
    is_demo: bool
    candidate_count: int
    requirements_source: Literal["manual", "llm", "empty"] | None = None


class CandidateListItem(BaseModel):
    id: uuid.UUID
    source_filename: str
    status: CandidateStatus
    status_reason: str | None
    display_name: str | None
    is_demo: bool
    created_at: datetime
    total_score: float | None


class MatchRequest(BaseModel):
    # None = every READY candidate in the system.
    candidate_ids: list[uuid.UUID] | None = None


class MatchResponse(BaseModel):
    matched: int


class AcceptedFile(BaseModel):
    candidate_id: uuid.UUID
    filename: str
    status: CandidateStatus


class RejectedFile(BaseModel):
    filename: str
    reason: str


class UploadResponse(BaseModel):
    accepted: list[AcceptedFile]
    rejected: list[RejectedFile]


class ComponentPoints(BaseModel):
    score: float | None
    weight: float
    points: float
    applicable: bool


class RankingItem(BaseModel):
    rank: int
    candidate_id: uuid.UUID
    display_name: str | None
    is_demo: bool
    total_score: float
    components: dict[str, ComponentPoints]
    must_have_matched: int
    must_have_total: int
    missing_must_have: list[str]
    skills: list[str]


class RankingResponse(BaseModel):
    job_id: uuid.UUID
    total_candidates: int
    returned: int
    items: list[RankingItem]
    notice: str = "Decision support only. A human makes the hiring decision."


class PreviewItem(BaseModel):
    candidate_id: uuid.UUID
    display_name: str | None
    is_demo: bool
    rank_current: int
    rank_preview: int
    total_current: float
    total_preview: float


class ProfileOut(BaseModel):
    skills: list[str]
    total_years_experience: float | None
    roles: list[dict[str, Any]]
    education: list[dict[str, Any]]
    languages: list[str]
    certifications: list[str]


class IdentityOut(BaseModel):
    display_name: str | None
    email: str | None
    phone: str | None


class MatchOut(BaseModel):
    job_id: uuid.UUID
    total_score: float
    breakdown: dict[str, Any]
    explanation: dict[str, Any]
    model_version: str


class MatchedJob(BaseModel):
    job_id: uuid.UUID
    title: str
    total_score: float


class CandidateOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID | None
    source_filename: str
    status: CandidateStatus
    status_reason: str | None
    is_demo: bool
    created_at: datetime
    raw_text: str
    parse_notes: list[str]
    manually_edited: bool
    matched_jobs: list[MatchedJob]
    profile: ProfileOut | None
    identity: IdentityOut | None
    match: MatchOut | None
    notice: str = "Decision support only. A human makes the hiring decision."


class WeightsOut(BaseModel):
    weights: dict[str, float]
    defaults: dict[str, float]
    is_override: bool


class JobStats(BaseModel):
    job_id: uuid.UUID
    candidates_total: int
    by_status: dict[str, int]
    scored: int
    mean_score: float | None
    median_score: float | None
    score_histogram: list[dict[str, Any]]
    most_missing_must_have: list[dict[str, Any]]
