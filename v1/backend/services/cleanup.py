from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.models import Job

_UPLOAD_PREFIXES = ("upload_", "upload_url_")


def _norm(path: Path) -> str:
    try:
        return str(path.resolve())
    except Exception:
        return str(path)


def _remove_path(path: Path) -> bool:
    try:
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def purge_expired_jobs(db: Session, cfg: AppConfig) -> int:
    days = int(getattr(cfg, "retention_days", 0) or 0)
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    expired: list[Job] = db.query(Job).filter(Job.updated_at < cutoff).all()
    for job in expired:
        for path_str in [job.stored_path, job.extracted_path]:
            if not path_str:
                continue
            _remove_path(Path(path_str))
        db.delete(job)
    return len(expired)


def cleanup_orphans(db: Session, cfg: AppConfig) -> Dict[str, Any]:
    jobs: list[Job] = db.query(Job).all()
    referenced_files = {_norm(Path(j.stored_path)) for j in jobs if j.stored_path}
    referenced_dirs = {_norm(Path(j.extracted_path)) for j in jobs if j.extracted_path}

    uploads_dir = Path(cfg.uploads_dir)
    temp_dir = Path(cfg.temp_dir)

    removed_uploads = 0
    removed_temp_dirs = 0

    try:
        if uploads_dir.exists():
            for p in uploads_dir.iterdir():
                if not p.is_file():
                    continue
                if not p.name.startswith(_UPLOAD_PREFIXES):
                    continue
                if _norm(p) in referenced_files:
                    continue
                if _remove_path(p):
                    removed_uploads += 1
    except Exception:
        pass

    try:
        if temp_dir.exists():
            for p in temp_dir.iterdir():
                if not p.is_dir():
                    continue
                if not (p.name.startswith("job_") or p.name.startswith("upload_")):
                    continue
                if _norm(p) in referenced_dirs:
                    continue
                if _remove_path(p):
                    removed_temp_dirs += 1
    except Exception:
        pass

    return {"removed_uploads": removed_uploads, "removed_temp_dirs": removed_temp_dirs}
