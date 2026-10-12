from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.deps import get_db
from tests.conftest import Env


def test_health_ok(env: Env) -> None:
    response = env.client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok", "version": "0.1.0"}


def test_health_is_public(make_env: Callable[..., Env]) -> None:
    client = TestClient(make_env().client.app)  # no Authorization header
    assert client.get("/api/health").status_code == 200


class _BrokenSession:
    def execute(self, *_: object, **__: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("db down"))


def test_health_reports_db_failure(env: Env) -> None:
    env.client.app.dependency_overrides[get_db] = lambda: _BrokenSession()  # type: ignore[attr-defined]
    response = env.client.get("/api/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["database"] == "error"
