import os
import tempfile
from pathlib import Path

# Configure an isolated database before the app is imported.
_db_file = Path(tempfile.mkdtemp()) / "test.db"
os.environ["DATABASE_URL"] = f"sqlite:///{_db_file.as_posix()}"
os.environ["JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256-signing"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from init_db import DEMO_PASSWORD, seed, seed_demo  # noqa: E402

ADMIN_PASSWORD = "Admin1234"


ICD10_TITLES = {"E11.9": "Type 2 diabetes mellitus without complications", "I10": "Essential (primary) hypertension",
                "B34.9": "Viral infection, unspecified"}


@pytest.fixture(autouse=True)
def offline_terminology(monkeypatch):
    """Tests never call the terminology server; known ICD-10 titles stand in for $lookup."""
    from app import fhir_sources
    monkeypatch.setattr(fhir_sources, "terminology_display", lambda system, code: ICD10_TITLES.get(code))


@pytest.fixture()
def client():
    from app.routers import auth
    auth._failures.clear()  # sign-in throttling state is per process
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        hospital = seed(db, "Test Hospital", "TST", "admin", ADMIN_PASSWORD)
        seed_demo(db, hospital)
    return TestClient(app)


def login(client, username, password=DEMO_PASSWORD) -> dict:
    response = client.post("/api/auth/login", json={"username": username, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture()
def admin(client):
    return login(client, "admin", ADMIN_PASSWORD)
