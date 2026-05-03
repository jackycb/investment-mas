"""Test fixtures."""

from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.core.config import get_settings
from app.main import app


@pytest.fixture(autouse=True)
def _stable_test_settings(monkeypatch: pytest.MonkeyPatch) -> Generator[None, None, None]:
    monkeypatch.setenv("INVESTMENT_MAS_ENABLE_FUTU_DOWNLOAD_FALLBACK", "false")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite+pysqlite://",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)
    db = testing_session_local()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from app.core.database import get_db_session

    def override_db():
        yield session

    app.dependency_overrides[get_db_session] = override_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
