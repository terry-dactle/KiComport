from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pathlib import Path

from ..config import AppConfig
from ..security import require_ui_token
from .. import audit

router = APIRouter(prefix="/audit", tags=["audit"])


def _get_config(request: Request) -> AppConfig:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="Configuration missing")
    return config


@router.get("")
async def list_audit_entries(
    request: Request,
    limit: int = Query(100, ge=1, le=500),
    job_id: str | None = None,
    config: AppConfig = Depends(_get_config),
):
    require_ui_token(request, config)
    entries = audit.list_events(limit=limit, job_id=job_id)
    return {"count": len(entries), "entries": entries}


@router.get("/{job_id}")
async def list_audit_entries_for_job(
    request: Request,
    job_id: str,
    limit: int = Query(100, ge=1, le=500),
    config: AppConfig = Depends(_get_config),
):
    require_ui_token(request, config)
    entries = audit.list_events(limit=limit, job_id=job_id)
    return {"count": len(entries), "entries": entries}


@router.get("/download")
async def download_audit_log(
    request: Request,
    config: AppConfig = Depends(_get_config),
):
    require_ui_token(request, config)
    log_path = Path(audit.AUDIT_LOG)
    if not log_path.exists():
        return JSONResponse({"detail": "audit.log not found"}, status_code=404)
    return FileResponse(log_path, media_type="text/plain", filename="audit.log")
