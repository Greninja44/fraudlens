"""Test configuration.

By default tests use a temporary SQLite database. Set FL_TEST_DATABASE_URL to run against PostgreSQL, e.g.
  FL_TEST_DATABASE_URL=postgresql+psycopg://fraudlens:fraudlens@localhost:5440/fraudlens_test uv run pytest
"""
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="fraudlens-test-"))
os.environ["FL_DATABASE_URL"] = os.environ.get("FL_TEST_DATABASE_URL") or f"sqlite:///{_tmp / 'test.db'}"
os.environ["FL_STORAGE_DIR"] = str(_tmp / "storage")
os.environ["FL_SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789"
os.environ["FL_ENV"] = "test"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import Base, SessionLocal, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.storage import LocalStorage, set_storage  # noqa: E402
from app.models import Role, User  # noqa: E402

PASSWORD = "Test-Password-123"
DATASETS = Path(__file__).resolve().parents[2] / "datasets"


def reset_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)


@pytest.fixture()
def storage(tmp_path):
    s = LocalStorage(tmp_path / "storage")
    set_storage(s)
    yield s
    set_storage(None)


@pytest.fixture()
def db(storage):
    reset_db()
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture()
def users(db):
    out = {}
    for key, role in [("admin", Role.ADMIN), ("inv", Role.INVESTIGATOR), ("inv2", Role.INVESTIGATOR), ("rev", Role.REVIEWER)]:
        u = User(email=f"{key}@test.example", full_name=f"{key.title()} User", role=role, hashed_password=hash_password(PASSWORD))
        db.add(u)
        out[key] = u
    db.commit()
    return out


@pytest.fixture()
def client(users):
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def auth(client):
    cache = {}

    def headers(key: str) -> dict:
        if key not in cache:
            r = client.post("/api/auth/login", json={"email": f"{key}@test.example", "password": PASSWORD})
            assert r.status_code == 200, r.text
            cache[key] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return cache[key]
    return headers
