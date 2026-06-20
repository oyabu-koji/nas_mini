from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.deps import require_bearer_token


def create_test_app() -> FastAPI:
    app = FastAPI()

    @app.get("/protected", dependencies=[Depends(require_bearer_token)])
    def protected() -> dict[str, str]:
        return {"status": "ok"}

    return app


def test_bearer_token_accepts_valid_token(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    client = TestClient(create_test_app())

    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer secret-token"},
    )

    assert response.status_code == 200


def test_bearer_token_rejects_missing_token():
    client = TestClient(create_test_app())

    response = client.get("/protected")

    assert response.status_code == 401


def test_bearer_token_rejects_malformed_token():
    client = TestClient(create_test_app())

    response = client.get("/protected", headers={"Authorization": "Token abc"})

    assert response.status_code == 401


def test_bearer_token_rejects_invalid_token(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path / "media"))
    monkeypatch.setenv("API_TOKEN", "secret-token")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "db.sqlite3"))
    client = TestClient(create_test_app())

    response = client.get(
        "/protected",
        headers={"Authorization": "Bearer wrong-token"},
    )

    assert response.status_code == 401
    assert "wrong-token" not in response.text
