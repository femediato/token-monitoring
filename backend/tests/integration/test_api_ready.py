from fastapi.testclient import TestClient

from autolp.api.main import app
from autolp.config import get_settings


def test_ready_reports_migrations_at_head(engine: object) -> None:
    get_settings.cache_clear()
    with TestClient(app) as client:
        resp = client.get("/health/ready")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ok"
    assert body["revision"] == body["head"] == "0006"
