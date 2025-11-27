from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..config import AppConfig, ensure_directories, save_config
from .. import config as config_module
from ..security import require_ui_token
from .. import audit, storage
import urllib.request
import urllib.error
import socket

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

templates = Jinja2Templates(directory="templates")


class SettingsPayload(BaseModel):
    kicad_root: str = Field(..., min_length=1)
    incoming_folder: str = Field(..., min_length=1)
    kicad_docker_port: int = Field(..., ge=1)
    ollama_port: int = Field(..., ge=1)
    ai_enabled: bool = Field(default=False)
    ai_base_url: Optional[str] = None
    ai_model: Optional[str] = None
    logs_folder: Optional[str] = None
    backup_folder: Optional[str] = None
    ui_token: Optional[str] = None
    require_token: Optional[bool] = None


def _get_config(request: Request) -> AppConfig:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="Configuration missing")
    return config


def _enforce_ui_token(
    request: Request,
    config: AppConfig = Depends(_get_config),
) -> None:
    require_ui_token(request, config)


def _serialize_settings(config: AppConfig) -> dict:
    return {
        "paths": {
            "root": config.paths.root,
            "incoming": config.paths.incoming,
            "logs": config.paths.logs,
            "backup": config.paths.backup,
            "jobs": config.paths.jobs,
        },
        "config_path": str(config_module.CONFIG_PATH) if config_module.CONFIG_PATH else None,
        "integration": {
            "kicad_docker_port": config.integration.kicad_docker_port,
            "ollama_port": config.integration.ollama_port,
        },
        "ai": {
            "enabled": config.ai.enabled,
            "base_url": config.ai.base_url,
            "model": config.ai.model,
            "timeout_seconds": config.ai.timeout_seconds,
        },
        "ui": {
            "require_token": config.ui.require_token,
            "token": config.ui.token,
        },
        "audit": {
            "path": str(audit.AUDIT_LOG),
        },
    }


@router.get("/jobs")
async def jobs_page(
    request: Request,
    config: AppConfig = Depends(_get_config),
    _: None = Depends(_enforce_ui_token),
):
    jobs = [job.model_dump(mode="json") for job in storage.list_jobs()]
    entries = audit.list_events(limit=50)
    return templates.TemplateResponse(
        "jobs.html",
        {
            "request": request,
            "jobs": jobs,
        "audit_entries": entries,
        "require_token": config.ui.require_token,
        "audit_log_path": str(audit.AUDIT_LOG),
    },
)


@router.get("/jobs/data")
async def jobs_data(
    request: Request,
    config: AppConfig = Depends(_get_config),
    _: None = Depends(_enforce_ui_token),
):
    jobs = [job.model_dump(mode="json") for job in storage.list_jobs()]
    entries = audit.list_events(limit=50)
    return {"jobs": jobs, "audit_entries": entries, "require_token": config.ui.require_token}


@router.get("/settings/data")
async def settings_data(
    request: Request,
    config: AppConfig = Depends(_get_config),
    _: None = Depends(_enforce_ui_token),
) -> dict:
    return _serialize_settings(config)


@router.post("/settings/apply")
async def update_settings(
    payload: SettingsPayload,
    request: Request,
    config: AppConfig = Depends(_get_config),
    _: None = Depends(_enforce_ui_token),
) -> dict:
    config.paths.root = payload.kicad_root.strip()
    config.paths.incoming = payload.incoming_folder.strip()
    if payload.logs_folder:
        config.paths.logs = payload.logs_folder.strip()
    if payload.backup_folder:
        config.paths.backup = payload.backup_folder.strip()
    config.integration.kicad_docker_port = payload.kicad_docker_port
    config.integration.ollama_port = payload.ollama_port
    config.ai.enabled = payload.ai_enabled
    config.ai.base_url = payload.ai_base_url.strip() if payload.ai_base_url else None
    config.ai.model = payload.ai_model.strip() if payload.ai_model else None
    if payload.ui_token is not None:
        config.ui.token = payload.ui_token.strip() or None
    if payload.require_token is not None:
        config.ui.require_token = payload.require_token

    ensure_directories(config.paths)
    storage.configure(config.paths)
    save_config(config)
    return _serialize_settings(config)


@router.get("/integration/check")
async def check_integration(
    request: Request,
    config: AppConfig = Depends(_get_config),
    _: None = Depends(_enforce_ui_token),
) -> dict:
    port = config.integration.kicad_docker_port
    url = f"http://localhost:{port}"
    result: dict = {"url": url, "reachable": False}
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            result["reachable"] = resp.status < 500
            result["status"] = resp.status
    except (urllib.error.URLError, socket.timeout):
        result["error"] = "unreachable"
    return result
