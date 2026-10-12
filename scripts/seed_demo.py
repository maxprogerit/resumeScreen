"""Seed the database with a DEMO job and SYNTHETIC candidates (flagged is_demo=true).

Works offline: no OpenAI key is needed. Profiles come from the generator's ground truth
(not from an LLM) and embeddings use the offline hashed bag-of-words embedder, labelled
"offline-hash-demo". Scores are therefore real outputs of the deterministic scorer, but the
semantic component is lexical overlap only. Explanations use the deterministic template.

    alembic upgrade head
    python -m scripts.seed_demo --count 25          # idempotent
    python -m scripts.seed_demo --reset             # delete demo rows first
"""

import argparse
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_sessionmaker
from app.llm.offline import OFFLINE_MODEL, OfflineEmbedder
from app.models import (
    Candidate,
    CandidateIdentity,
    CandidateProfile,
    CandidateStatus,
    Job,
)
from app.schemas import EducationLevel, ExtractedRole, JobRequirements
from app.services.extraction import compute_years
from app.services.matching import (
    embedding_text_for_job,
    embedding_text_for_profile,
    score_candidate,
)
from app.services.requirements import normalize_requirements
from app.services.weights import load_weights
from app.skills import canonical_languages, canonical_skills
from app.vectors import to_bytes
from scripts.generate_resumes import SyntheticCandidate, generate_candidates, render_text

DEMO_JOB_TITLE = "Backend Engineer (demo)"
DEMO_JOB_TEXT = (
    "DEMO JOB - synthetic. We are looking for a backend engineer to build and run Python "
    "services. Required: Python, FastAPI, PostgreSQL, Docker, at least 3 years of experience, "
    "fluent English, a bachelor's degree. Nice to have: Kubernetes, AWS, Redis."
)
DEMO_REQUIREMENTS = JobRequirements(
    must_have_skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
    nice_to_have_skills=["Kubernetes", "AWS", "Redis"],
    min_years_experience=3,
    languages=["English"],
    education_level=EducationLevel.BACHELOR,
)


def _profile_from_truth(c: SyntheticCandidate, raw_text: str, today: datetime) -> CandidateProfile:
    roles = [
        ExtractedRole(
            title=r["title"],
            company=r["company"],
            start=r["start"],
            end=r["end"],
            summary=r["bullet"],
        )
        for r in c.roles
    ]
    years = compute_years(roles, today.date())
    profile = CandidateProfile(
        skills=canonical_skills(c.skills),
        total_years_experience=years if years is not None else c.years,
        roles=[r.model_dump() for r in roles],
        education=[{"level": c.degree_level, "field": c.field_of_study, "institution": c.school}],
        languages=canonical_languages(c.languages),
        certifications=c.certifications,
    )
    profile.embedding = to_bytes(OfflineEmbedder().embed([embedding_text_for_profile(profile)])[0])
    profile.embedding_model = OFFLINE_MODEL
    return profile


def reset_demo(db: Session) -> None:
    db.execute(delete(Candidate).where(Candidate.is_demo.is_(True)))
    db.execute(delete(Job).where(Job.is_demo.is_(True)))
    db.commit()


def seed(db: Session, settings: Settings, count: int, seed_value: int = 42) -> int:
    if db.scalar(select(Job).where(Job.is_demo.is_(True))) is not None:
        return 0
    now = datetime.now(UTC)
    job = Job(
        title=DEMO_JOB_TITLE,
        description_text=DEMO_JOB_TEXT,
        requirements=normalize_requirements(DEMO_REQUIREMENTS).model_dump(mode="json"),
        is_demo=True,
    )
    job.embedding = to_bytes(OfflineEmbedder().embed([embedding_text_for_job(job)])[0])
    job.embedding_model = OFFLINE_MODEL
    db.add(job)
    db.commit()

    weights = load_weights(db, settings)
    for c in generate_candidates(count, seed_value):
        raw_text = render_text(c)
        candidate = Candidate(
            job_id=job.id,
            source_filename=f"{c.key}.synthetic",
            raw_text=raw_text,
            status=CandidateStatus.READY,
            is_demo=True,
        )
        candidate.profile = _profile_from_truth(c, raw_text, now)
        candidate.identity = CandidateIdentity(display_name=c.name, email=c.email, phone=c.phone)
        db.add(candidate)
        db.commit()
        score_candidate(db, job, candidate, llm=None, weights=weights, settings=settings)
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed synthetic DEMO data")
    parser.add_argument("--count", type=int, default=25)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reset", action="store_true", help="delete existing demo rows first")
    args = parser.parse_args()
    settings = get_settings()
    with get_sessionmaker()() as db:
        if args.reset:
            reset_demo(db)
        created = seed(db, settings, args.count, args.seed)
    print(
        f"Seeded {created} synthetic demo candidates."
        if created
        else "Demo data already present (use --reset to recreate)."
    )


if __name__ == "__main__":
    main()
