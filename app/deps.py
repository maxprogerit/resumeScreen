from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import ApiError
from app.models import Candidate, Job
from app.services.pipeline import LLMProvider


def get_db(request: Request) -> Iterator[Session]:
    with request.app.state.session_factory() as session:
        yield session


def get_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_llm_provider(request: Request) -> LLMProvider:
    provider: LLMProvider = request.app.state.llm_provider
    return provider


DB = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
LLMProviderDep = Annotated[LLMProvider, Depends(get_llm_provider)]


def get_job_or_404(db: Session, job_id: object) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise ApiError(404, "job_not_found", "Job not found")
    return job


def get_candidate_or_404(db: Session, candidate_id: object) -> Candidate:
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise ApiError(404, "candidate_not_found", "Candidate not found")
    return candidate
