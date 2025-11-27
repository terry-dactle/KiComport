from __future__ import annotations

from datetime import datetime, timezone
import tempfile
from pathlib import Path

import json

from fastapi.testclient import TestClient

from app.config import AppConfig, PathsConfig, UIConfig
from app.models import ImportJob, ImportJobStatus, ImportPlan, ReviewStatus
from app import audit, main, storage


def _build_temp_config(tmpdir: Path) -> AppConfig:
    paths = PathsConfig(
        root=str(tmpdir / "root"),
        incoming=str(tmpdir / "incoming"),
        logs=str(tmpdir / "logs"),
        backup=str(tmpdir / "backup"),
        jobs=str(tmpdir / "jobs"),
    )
    ui = UIConfig(require_token=True, token="secret")
    config = AppConfig(paths=paths, ui=ui)
    paths.root and Path(paths.root).mkdir(parents=True, exist_ok=True)
    Path(paths.incoming).mkdir(parents=True, exist_ok=True)
    Path(paths.logs).mkdir(parents=True, exist_ok=True)
    Path(paths.jobs).mkdir(parents=True, exist_ok=True)
    return config


def _prime_app(tmpdir: Path) -> TestClient:
    config = _build_temp_config(tmpdir)
    main.app.state.config = config
    main.app.state.ollama_client = None
    audit.configure(config.paths)
    storage.configure(config.paths)
    return TestClient(main.app)


def test_import_routes_require_token() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = _prime_app(Path(tmp))
        # Missing token should be rejected.
        resp = client.get("/imports")
        assert resp.status_code == 401
        # Token header should succeed.
        resp = client.get("/imports", headers={"X-KiComport-Token": "secret"})
        assert resp.status_code == 200


def test_apply_without_candidates_returns_error() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = _prime_app(Path(tmp))
        config: AppConfig = main.app.state.config

        job = ImportJob(
            id="job1",
            filename="dummy.zip",
            stored_path=str(Path(config.paths.incoming) / "dummy.zip"),
            md5="abc",
            status=ImportJobStatus.analyzed,
            created_at=datetime.now(timezone.utc),
            plan=ImportPlan(job_id="job1", candidates=[]),
            approval_status=ReviewStatus.approved,
        )
        storage.save_job(job)

        resp = client.post("/imports/job1/apply", headers={"X-KiComport-Token": "secret"})
        assert resp.status_code == 400
        assert "No symbol or footprint candidates" in resp.text


def test_preview_requires_token() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = _prime_app(Path(tmp))
        config: AppConfig = main.app.state.config
        incoming = Path(config.paths.incoming)
        incoming.mkdir(parents=True, exist_ok=True)
        stored = incoming / "file.kicad_sym"
        stored.write_text("dummy", encoding="utf-8")

        job = ImportJob(
            id="job2",
            filename="file.kicad_sym",
            stored_path=str(stored),
            md5="def",
            status=ImportJobStatus.uploaded,
            created_at=datetime.now(timezone.utc),
        )
        storage.save_job(job)

        assert client.get("/imports/job2/preview").status_code == 401
        assert client.get(
            "/imports/job2/preview", headers={"X-KiComport-Token": "secret"}
        ).status_code == 200


def test_apply_uses_payload_candidate() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        client = _prime_app(Path(tmp))
        config: AppConfig = main.app.state.config

        job = ImportJob(
            id="job3",
            filename="archive.zip",
            stored_path=str(Path(config.paths.incoming) / "archive.zip"),
            md5="ghi",
            status=ImportJobStatus.analyzed,
            created_at=datetime.now(timezone.utc),
            plan=ImportPlan(
                job_id="job3",
                candidates=[
                    {"path": "first_symbol", "kind": "symbol", "score": 0.1, "metadata": {}},
                    {"path": "second_symbol", "kind": "symbol", "score": 0.9, "metadata": {}},
                    {"path": "only_footprint", "kind": "footprint", "score": 0.5, "metadata": {}},
                ],
            ),
            approval_status=ReviewStatus.approved,
        )
        storage.save_job(job)

        payload = {"symbol_path": "first_symbol"}
        resp = client.post(
            "/imports/job3/apply",
            headers={"X-KiComport-Token": "secret", "Content-Type": "application/json"},
            content=json.dumps(payload),
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        sym_diff = data["table_diffs"].get("sym_lib_table", "")
        assert "first_symbol" in sym_diff
        assert "second_symbol" not in sym_diff
