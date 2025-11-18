from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.templating import Jinja2Templates

from ..config import AppConfig
from ..security import require_ui_token
from .. import audit, storage

router = APIRouter(prefix="/ui", tags=["ui"], include_in_schema=False)

templates = Jinja2Templates(directory="templates")


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
