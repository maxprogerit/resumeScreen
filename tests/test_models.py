from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Candidate, CandidateIdentity, CandidateProfile, Job, Match


def test_deleting_candidate_removes_all_derived_data(db: Session) -> None:
    job = Job(title="Demo job", description_text="demo")
    candidate = Candidate(source_filename="demo.pdf", raw_text="demo text")
    candidate.profile = CandidateProfile(skills=["python"])
    candidate.identity = CandidateIdentity(display_name="Demo Person")
    db.add_all([job, candidate])
    db.flush()
    db.add(Match(job_id=job.id, candidate_id=candidate.id, total_score=0.5))
    db.commit()

    db.delete(candidate)
    db.commit()

    for model in (Candidate, CandidateProfile, CandidateIdentity, Match):
        assert db.scalar(select(func.count()).select_from(model)) == 0
    assert db.scalar(select(func.count()).select_from(Job)) == 1
