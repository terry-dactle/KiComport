from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.deps import get_db
from ..db.models import Job

router = APIRouter(tags=["system"])


def get_config(request: Request) -> AppConfig:
    cfg = getattr(request.app.state, "config", None)
    if not cfg:
        raise HTTPException(status_code=500, detail="Config not loaded")
    return cfg


@router.get("/api/diagnostics")
def diagnostics(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    cfg = get_config(request)
    total_jobs = db.query(Job).count()
    return {
        "app_name": cfg.app_name,
        "config": cfg.to_safe_dict(),
        "db_path": str(cfg.database_path),
        "job_count": total_jobs,
    }

