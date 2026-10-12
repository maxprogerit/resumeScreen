from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models as _models  # noqa: F401
from app.config import Settings
from app.db import Base, make_engine
from app.llm.client import LLMClient
from app.main import create_app
from tests.fakes import FakeLLM

API_KEY = "test-key"
AUTH = {"Authorization": f"Bearer {API_KEY}"}


def make_settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "api_key_admin": SecretStr(API_KEY),
        "retention_days": 0,
        "upload_rate_limit_per_minute": 1000,
    }
    return Settings(_env_file=None, **{**base, **overrides})  # type: ignore[arg-type, call-arg]


@pytest.fixture
def engine() -> Iterator[Engine]:
    # In-memory SQLite shared across threads (endpoints and background tasks run in threads).
    eng = make_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as session:
        yield session


@pytest.fixture
def settings() -> Settings:
    return make_settings()


@pytest.fixture
def fake_llm() -> FakeLLM:
    return FakeLLM()


@dataclass
class Env:
    client: TestClient
    llm: FakeLLM
    session_factory: sessionmaker[Session]
    settings: Settings


@pytest.fixture
def make_env(session_factory: sessionmaker[Session], settings: Settings) -> Callable[..., Env]:
    def factory(llm: FakeLLM | None = None, **setting_overrides: object) -> Env:
        the_llm = llm or FakeLLM()
        the_settings = make_settings(**setting_overrides) if setting_overrides else settings
        provider: Callable[[], LLMClient] = lambda: the_llm  # noqa: E731
        app = create_app(
            settings=the_settings,
            session_factory=session_factory,
            llm_provider=provider,
            run_background_jobs=False,
        )
        client = TestClient(app, headers=AUTH)
        return Env(client, the_llm, session_factory, the_settings)

    return factory


@pytest.fixture
def env(make_env: Callable[..., Env]) -> Env:
    return make_env()
