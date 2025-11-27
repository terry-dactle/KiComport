from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from .models import ImportJob
from .config import PathsConfig


JOBS_DIR = Path("data/jobs")


def configure(paths: PathsConfig) -> None:
    global JOBS_DIR
    JOBS_DIR = Path(paths.jobs)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_jobs_dir() -> None:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)


def save_job(job: ImportJob) -> None:
    _ensure_jobs_dir()
    job_path = JOBS_DIR / f"{job.id}.json"
    with job_path.open("w", encoding="utf-8") as handle:
        json.dump(job.model_dump(mode="json"), handle, indent=2)


def load_job(job_id: str) -> Optional[ImportJob]:
    _ensure_jobs_dir()
    job_path = JOBS_DIR / f"{job_id}.json"
    if not job_path.is_file():
        return None
    with job_path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return ImportJob(**data)


def list_jobs() -> List[ImportJob]:
    _ensure_jobs_dir()
    jobs: List[ImportJob] = []
    for job_file in sorted(JOBS_DIR.glob("*.json")):
        try:
            with job_file.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            jobs.append(ImportJob(**data))
        except (json.JSONDecodeError, TypeError):
            continue
    return jobs


def delete_job(job_id: str) -> bool:
    _ensure_jobs_dir()
    job_path = JOBS_DIR / f"{job_id}.json"
    if job_path.exists():
        job_path.unlink()
        return True
    return False
