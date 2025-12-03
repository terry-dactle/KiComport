from fastapi import status
from fastapi.testclient import TestClient

from v1.backend.main import app


def test_root_returns_ok_or_redirect(tmp_path, monkeypatch):
    config_path = tmp_path / "app_settings.yaml"
    monkeypatch.setenv("KICOMPORT_CONFIG_PATH", str(config_path))

    with TestClient(app) as client:
        resp = client.get("/", allow_redirects=False)
        assert resp.status_code in {status.HTTP_200_OK, status.HTTP_302_FOUND, status.HTTP_307_TEMPORARY_REDIRECT}

        if resp.is_redirect:
            follow = client.get(resp.headers["location"])
            assert follow.status_code == status.HTTP_200_OK

        health = client.get("/health")
        assert health.status_code == status.HTTP_200_OK
