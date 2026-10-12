from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import Candidate
from scripts.seed_demo import reset_demo, seed
from tests.conftest import Env
from tests.helpers import row_count


def test_seed_creates_flagged_demo_rows_and_is_idempotent(
    env: Env, db: Session, settings: Settings
) -> None:
    assert seed(db, settings, 12) == 12
    assert seed(db, settings, 12) == 0  # idempotent
    assert row_count(db, Candidate) == 12
    assert all(c.is_demo for c in db.query(Candidate))

    jobs = env.client.get("/api/jobs").json()
    assert len(jobs) == 1 and jobs[0]["is_demo"] is True
    ranking = env.client.get(f"/api/jobs/{jobs[0]['id']}/ranking").json()
    assert len(ranking["items"]) == 12 and all(i["is_demo"] for i in ranking["items"])
    scores = [i["total_score"] for i in ranking["items"]]
    assert scores == sorted(scores, reverse=True) and len(set(scores)) > 3  # not degenerate

    detail = env.client.get(f"/api/candidates/{ranking['items'][0]['candidate_id']}").json()
    assert detail["match"]["explanation"]["summary_source"] == "template"
    assert detail["match"]["breakdown"]["components"]["semantic"]["applicable"] is True

    reset_demo(db)
    assert row_count(db, Candidate) == 0


def test_demo_data_has_no_real_contact_data(env: Env, db: Session, settings: Settings) -> None:
    seed(db, settings, 5)
    for c in db.query(Candidate):
        assert c.identity is not None
        assert c.identity.email is not None and c.identity.email.endswith("@example.com")
        assert c.identity.phone is not None and c.identity.phone.startswith("+1-555-01")
    assert isinstance(env.client, TestClient)
