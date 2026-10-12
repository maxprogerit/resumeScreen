import contextlib
import statistics
import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, File, Query, Request, UploadFile
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api.schemas import (
    AcceptedFile,
    CandidateListItem,
    ComponentPoints,
    JobCreate,
    JobOut,
    JobStats,
    MatchRequest,
    MatchResponse,
    PreviewItem,
    RankingItem,
    RankingResponse,
    RejectedFile,
    UploadResponse,
)
from app.deps import DB, LLMProviderDep, SettingsDep, get_job_or_404
from app.errors import ApiError
from app.llm.client import LLMUnavailable
from app.models import Candidate, CandidateStatus, Job, Match
from app.parsing import UnsupportedFileType, safe_filename, sniff_file_kind
from app.schemas import JobRequirements, Weights
from app.security import enforce_upload_rate_limit
from app.services.matching import ensure_job_embedding, is_member, rescore_job, score_candidate
from app.services.pipeline import content_fingerprint, process_candidate, rescore_job_task
from app.services.requirements import extract_requirements, normalize_requirements
from app.services.scoring import COMPONENTS, combine
from app.services.weights import load_weights
from app.skills import canonical_skills

router = APIRouter(tags=["jobs"])


def _candidate_count(db: Session, job_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(Candidate).where(is_member(job_id))) or 0


def _job_out(db: Session, job: Job, source: str | None = None) -> JobOut:
    return JobOut(
        id=job.id,
        title=job.title,
        description_text=job.description_text,
        requirements=JobRequirements.model_validate(job.requirements),
        created_at=job.created_at,
        is_demo=job.is_demo,
        candidate_count=_candidate_count(db, job.id),
        requirements_source=source,  # type: ignore[arg-type]
    )


@router.post("/jobs", response_model=JobOut, status_code=201)
def create_job(body: JobCreate, db: DB, provider: LLMProviderDep) -> JobOut:
    llm = None
    with contextlib.suppress(LLMUnavailable):
        llm = provider()

    if body.requirements is not None:
        requirements, source = normalize_requirements(body.requirements), "manual"
    else:
        extracted = extract_requirements(llm, body.title, body.description_text) if llm else None
        if extracted is not None:
            requirements, source = extracted, "llm"
        else:
            requirements, source = (
                JobRequirements(
                    must_have_skills=[],
                    nice_to_have_skills=[],
                    min_years_experience=None,
                    languages=[],
                    education_level=None,
                ),
                "empty",
            )

    job = Job(
        title=body.title.strip(),
        description_text=body.description_text.strip(),
        requirements=requirements.model_dump(mode="json"),
    )
    db.add(job)
    db.commit()
    ensure_job_embedding(db, job, llm)
    return _job_out(db, job, source)


@router.get("/jobs", response_model=list[JobOut])
def list_jobs(db: DB) -> list[JobOut]:
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc())).all()
    return [_job_out(db, j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: uuid.UUID, db: DB) -> JobOut:
    return _job_out(db, get_job_or_404(db, job_id))


@router.patch("/jobs/{job_id}/requirements", response_model=JobOut)
def update_requirements(
    job_id: uuid.UUID,
    body: JobRequirements,
    background: BackgroundTasks,
    request: Request,
    db: DB,
    settings: SettingsDep,
    provider: LLMProviderDep,
) -> JobOut:
    job = get_job_or_404(db, job_id)
    job.requirements = normalize_requirements(body).model_dump(mode="json")
    db.commit()
    # Deterministic rescore now (UI is consistent immediately); LLM-written evidence and
    # explanations are refreshed in the background.
    rescore_job(db, job, llm=None, weights=load_weights(db, settings), settings=settings)
    background.add_task(
        rescore_job_task, request.app.state.session_factory, provider, settings, job.id
    )
    return _job_out(db, job)


@router.delete("/jobs/{job_id}", status_code=204)
def delete_job(job_id: uuid.UUID, db: DB) -> None:
    """Deletes the job and the candidates uploaded for it (with all derived data).

    A candidate that is also matched to another job is kept (detached from this job):
    deleting a job must not silently destroy data another job still uses.
    """
    job = get_job_or_404(db, job_id)
    matched_elsewhere = select(Match.candidate_id).where(Match.job_id != job.id)
    db.execute(
        delete(Candidate).where(Candidate.job_id == job.id, Candidate.id.not_in(matched_elsewhere))
    )
    db.delete(job)
    db.commit()


# ------------------------------------------------------------------------------ upload


@router.post(
    "/jobs/{job_id}/candidates",
    response_model=UploadResponse,
    status_code=202,
    dependencies=[Depends(enforce_upload_rate_limit)],
)
def upload_candidates(
    job_id: uuid.UUID,
    background: BackgroundTasks,
    request: Request,
    db: DB,
    settings: SettingsDep,
    provider: LLMProviderDep,
    files: Annotated[list[UploadFile], File(description="PDF or DOCX resumes")],
) -> UploadResponse:
    job = get_job_or_404(db, job_id)
    if len(files) > settings.max_files_per_upload:
        raise ApiError(
            413, "too_many_files", f"At most {settings.max_files_per_upload} files per request"
        )
    max_bytes = settings.max_upload_mb * 1024 * 1024
    accepted: list[AcceptedFile] = []
    rejected: list[RejectedFile] = []
    for upload in files:
        name = safe_filename(upload.filename)
        data = upload.file.read(max_bytes + 1)
        if not data:
            rejected.append(RejectedFile(filename=name, reason="empty_file"))
            continue
        if len(data) > max_bytes:
            rejected.append(RejectedFile(filename=name, reason="file_too_large"))
            continue
        try:
            sniff_file_kind(data)  # content, not extension / client content type
        except UnsupportedFileType:
            rejected.append(RejectedFile(filename=name, reason="unsupported_file_type"))
            continue
        digest = content_fingerprint(data)
        if db.scalar(
            select(Candidate.id)
            .where(Candidate.job_id == job.id, Candidate.content_hash == digest)
            .limit(1)
        ):
            rejected.append(RejectedFile(filename=name, reason="duplicate_file"))
            continue
        candidate = Candidate(job_id=job.id, source_filename=name, content_hash=digest)
        db.add(candidate)
        db.commit()
        accepted.append(
            AcceptedFile(candidate_id=candidate.id, filename=name, status=candidate.status)
        )
        background.add_task(
            process_candidate,
            request.app.state.session_factory,
            provider,
            settings,
            candidate.id,
            data,
        )
    if not accepted:
        raise ApiError(
            422,
            "no_valid_files",
            "No valid PDF/DOCX files in the request: "
            + ", ".join(sorted({r.reason for r in rejected})),
        )
    return UploadResponse(accepted=accepted, rejected=rejected)


@router.get("/jobs/{job_id}/candidates", response_model=list[CandidateListItem])
def list_candidates(
    job_id: uuid.UUID, db: DB, blind: Annotated[bool, Query()] = False
) -> list[CandidateListItem]:
    get_job_or_404(db, job_id)
    out: list[CandidateListItem] = []
    for c in db.scalars(
        select(Candidate).where(is_member(job_id)).order_by(Candidate.created_at.desc())
    ):
        match = next((m for m in c.matches if m.job_id == job_id), None)
        out.append(
            CandidateListItem(
                id=c.id,
                source_filename=c.source_filename,
                status=c.status,
                status_reason=c.status_reason,
                display_name=None if blind or c.identity is None else c.identity.display_name,
                is_demo=c.is_demo,
                created_at=c.created_at,
                total_score=match.total_score if match else None,
            )
        )
    return out


@router.post("/jobs/{job_id}/match", response_model=MatchResponse)
def match_existing_candidates(
    job_id: uuid.UUID,
    body: MatchRequest,
    background: BackgroundTasks,
    request: Request,
    db: DB,
    settings: SettingsDep,
    provider: LLMProviderDep,
) -> MatchResponse:
    """Match candidates that already exist (uploaded for other jobs) against this job.

    Scores appear immediately (deterministic); evidence quotes and explanation wording are
    refreshed in the background. Omit `candidate_ids` to match every READY candidate.
    """
    job = get_job_or_404(db, job_id)
    query = select(Candidate).where(Candidate.status == CandidateStatus.READY)
    if body.candidate_ids is not None:
        query = query.where(Candidate.id.in_(body.candidate_ids))
    llm = None
    with contextlib.suppress(LLMUnavailable):
        llm = provider()
    ensure_job_embedding(db, job, llm)
    weights = load_weights(db, settings)
    matched = 0
    for candidate in db.scalars(query).all():
        if candidate.profile is None:
            continue
        score_candidate(db, job, candidate, llm=None, weights=weights, settings=settings)
        matched += 1
    if matched:
        background.add_task(
            rescore_job_task, request.app.state.session_factory, provider, settings, job.id
        )
    return MatchResponse(matched=matched)


# ------------------------------------------------------------------------------ ranking


def _ranked(db: Session, job_id: uuid.UUID) -> list[tuple[Match, Candidate]]:
    rows = db.execute(
        select(Match, Candidate)
        .join(Candidate, Candidate.id == Match.candidate_id)
        .where(Match.job_id == job_id, Candidate.status == CandidateStatus.READY)
    ).all()
    pairs: list[tuple[Match, Candidate]] = [(r[0], r[1]) for r in rows]
    # Ties: more must-have coverage first, then the earlier upload; deterministic, no identity.
    pairs.sort(
        key=lambda mc: (
            -mc[0].total_score,
            -(mc[0].breakdown["components"]["must_have"].get("score") or 0.0),
            mc[1].created_at,
            str(mc[1].id),
        )
    )
    return pairs


def _item(rank: int, match: Match, cand: Candidate, blind: bool) -> RankingItem:
    comps = match.breakdown["components"]
    must = comps["must_have"]
    return RankingItem(
        rank=rank,
        candidate_id=cand.id,
        display_name=None if blind or cand.identity is None else cand.identity.display_name,
        is_demo=cand.is_demo,
        total_score=match.total_score,
        components={
            n: ComponentPoints(
                score=comps[n].get("score"),
                weight=comps[n]["weight"],
                points=comps[n]["points"],
                applicable=comps[n]["applicable"],
            )
            for n in COMPONENTS
        },
        must_have_matched=len(must.get("matched", [])),
        must_have_total=len(must.get("matched", [])) + len(must.get("missing", [])),
        missing_must_have=must.get("missing", []),
        skills=cand.profile.skills if cand.profile else [],
    )


@router.get("/jobs/{job_id}/ranking", response_model=RankingResponse)
def ranking(
    job_id: uuid.UUID,
    db: DB,
    min_score: Annotated[float, Query(ge=0, le=100)] = 0,
    skill: Annotated[
        str | None, Query(max_length=200, description="comma separated; all required")
    ] = None,
    blind: Annotated[bool, Query(description="hide names to reduce anchoring")] = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> RankingResponse:
    get_job_or_404(db, job_id)
    pairs = _ranked(db, job_id)
    wanted = canonical_skills(skill.split(",")) if skill else []
    items: list[RankingItem] = []
    for rank, (match, cand) in enumerate(pairs, start=1):  # rank is global, filters don't renumber
        if match.total_score < min_score:
            continue
        have = set(canonical_skills(cand.profile.skills)) if cand.profile else set()
        if wanted and not all(s in have for s in wanted):
            continue
        items.append(_item(rank, match, cand, blind))
    return RankingResponse(
        job_id=job_id, total_candidates=len(pairs), returned=len(items[:limit]), items=items[:limit]
    )


@router.post("/jobs/{job_id}/ranking/preview", response_model=list[PreviewItem])
def ranking_preview(
    job_id: uuid.UUID,
    weights: Weights,
    db: DB,
    blind: Annotated[bool, Query()] = False,
) -> list[PreviewItem]:
    """What the ranking would look like with other weights. Nothing is saved."""
    get_job_or_404(db, job_id)
    pairs = _ranked(db, job_id)
    previewed = sorted(
        ((combine(m.breakdown["components"], weights)["total"], m, c) for m, c in pairs),
        key=lambda t: (-t[0], str(t[2].id)),
    )
    preview_rank = {c.id: i for i, (_, _, c) in enumerate(previewed, start=1)}
    return [
        PreviewItem(
            candidate_id=c.id,
            display_name=None if blind or c.identity is None else c.identity.display_name,
            is_demo=c.is_demo,
            rank_current=i,
            rank_preview=preview_rank[c.id],
            total_current=m.total_score,
            total_preview=combine(m.breakdown["components"], weights)["total"],
        )
        for i, (m, c) in enumerate(pairs, start=1)
    ]


@router.get("/jobs/{job_id}/stats", response_model=JobStats)
def job_stats(job_id: uuid.UUID, db: DB) -> JobStats:
    get_job_or_404(db, job_id)
    status_rows = db.execute(
        select(Candidate.status, func.count()).where(is_member(job_id)).group_by(Candidate.status)
    ).all()
    by_status = {s.value: 0 for s in CandidateStatus}
    for status, n in status_rows:
        by_status[status.value] = n
    pairs = _ranked(db, job_id)
    scores = [m.total_score for m, _ in pairs]
    bins = [{"from": lo, "to": lo + 10, "count": 0} for lo in range(0, 100, 10)]
    for s in scores:
        bins[min(int(s // 10), 9)]["count"] += 1
    missing: dict[str, int] = {}
    for m, _ in pairs:
        for skill in m.breakdown["components"]["must_have"].get("missing", []):
            missing[skill] = missing.get(skill, 0) + 1
    return JobStats(
        job_id=job_id,
        candidates_total=sum(by_status.values()),
        by_status=by_status,
        scored=len(scores),
        mean_score=round(statistics.fmean(scores), 1) if scores else None,
        median_score=round(statistics.median(scores), 1) if scores else None,
        score_histogram=bins,
        most_missing_must_have=[
            {"skill": k, "candidates": v}
            for k, v in sorted(missing.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        ],
    )
