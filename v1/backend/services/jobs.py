from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from sqlalchemy.orm import Session

from ..db.models import CandidateType, Component, Job, JobLog, JobStatus


def log_job(db: Session, job: Job, message: str, level: str = "INFO") -> None:
    entry = JobLog(job_id=job.id, level=level.upper(), message=message)
    db.add(entry)
    db.flush()


def create_job(
    db: Session,
    md5: str,
    original_filename: str,
    stored_path: Path,
    extracted_path: Optional[Path] = None,
    status: JobStatus = JobStatus.pending,
    is_duplicate: bool = False,
    message: Optional[str] = None,
) -> Job:
    job = Job(
        md5=md5,
        original_filename=original_filename,
        stored_path=str(stored_path),
        extracted_path=str(extracted_path) if extracted_path else None,
        status=status,
        is_duplicate=is_duplicate,
        message=message,
    )
    db.add(job)
    db.flush()
    log_job(db, job, message or f"Job created with status {status.value}")
    return job


def get_job_by_md5(db: Session, md5: str) -> Optional[Job]:
    return db.query(Job).filter(Job.md5 == md5).order_by(Job.created_at.desc()).first()


def update_status(db: Session, job: Job, status: JobStatus, message: Optional[str] = None) -> Job:
    job.status = status
    job.updated_at = datetime.utcnow()
    if message:
        job.message = message
        log_job(db, job, message)
    db.add(job)
    db.flush()
    return job


def set_extracted_path(db: Session, job: Job, extracted_path: Path) -> Job:
    job.extracted_path = str(extracted_path)
    job.updated_at = datetime.utcnow()
    db.add(job)
    db.flush()
    return job


def add_component(
    db: Session,
    job: Job,
    name: str,
    candidates: Optional[Tuple] = None,
) -> Component:
    component = Component(job_id=job.id, name=name)
    db.add(component)
    db.flush()
    if candidates:
        for candidate in candidates:
            db.add(candidate)
    db.flush()
    return component


def select_candidate(
    db: Session,
    component: Component,
    symbol_id: Optional[int],
    footprint_id: Optional[int],
    model_id: Optional[int],
) -> Component:
    component.selected_symbol_id = symbol_id
    component.selected_footprint_id = footprint_id
    component.selected_model_id = model_id
    db.add(component)
    db.flush()
    return component


def reset_job_selection(db: Session, job: Job) -> None:
    for comp in job.components:
        comp.selected_symbol_id = None
        comp.selected_footprint_id = None
        comp.selected_model_id = None
        db.add(comp)
    db.flush()


def mark_duplicate(job: Job, existing: Job) -> None:
    job.is_duplicate = True
    job.status = JobStatus.duplicate
    job.message = f"Duplicate of job {existing.id}"
