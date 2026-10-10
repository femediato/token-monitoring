from fastapi.testclient import TestClient

from autolp.api.main import app


def test_liveness_without_database() -> None:
    client = TestClient(app)  # no lifespan: DB not required for liveness
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
