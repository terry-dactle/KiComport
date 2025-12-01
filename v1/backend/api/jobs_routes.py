from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.deps import get_db
from ..db.models import CandidateFile, CandidateType, Component, Job, JobStatus
from ..services import importer, jobs as job_service, ranking

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def get_config(request: Request) -> AppConfig:
    cfg = getattr(request.app.state, "config", None)
    if not cfg:
        raise HTTPException(status_code=500, detail="Config not loaded")
    return cfg


@router.get("")
def list_jobs(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    jobs = db.query(Job).order_by(Job.created_at.desc()).all()
    return [_serialize_job(j) for j in jobs]


@router.get("/{job_id}")
def job_detail(job_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return _serialize_job(job, include_components=True, include_logs=True)


@router.post("/{job_id}/select")
def save_selection(
    job_id: int,
    selection: Dict[str, Dict[str, Optional[int]]],
    request: Request,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    components_map = {c.id: c for c in job.components}
    for comp_id, picks in selection.items():
        comp = components_map.get(int(comp_id))
        if not comp:
            continue
        job_service.select_candidate(
            db,
            comp,
            symbol_id=picks.get("symbol_id"),
            footprint_id=picks.get("footprint_id"),
            model_id=picks.get("model_id"),
        )
    job_service.update_status(db, job, JobStatus.waiting_for_import, "Selections saved; ready for import")
    # recompute combined with feedback unchanged for now
    for comp in job.components:
        ranking.update_combined_for_candidates(comp.candidates)
    return {"job_id": job.id, "status": job.status.value, "request_id": getattr(request.state, "request_id", None)}


@router.post("/{job_id}/import")
def import_job(job_id: int, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    config = get_config(request)
    counts = importer.import_job_selection(
        db,
        job,
        symbol_dir=Path(config.kicad_symbol_dir),
        footprint_dir=Path(config.kicad_footprint_dir),
        model_dir=Path(config.kicad_3d_dir),
    )
    return {"job_id": job.id, "status": job.status.value, "imported": counts, "request_id": getattr(request.state, "request_id", None)}


def _serialize_job(job: Job, include_components: bool = False, include_logs: bool = False) -> Dict[str, Any]:
    data = {
        "id": job.id,
        "md5": job.md5,
        "original_filename": job.original_filename,
        "status": job.status.value,
        "is_duplicate": job.is_duplicate,
        "ai_failed": job.ai_failed,
        "message": job.message,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }
    if include_components:
        data["components"] = [_serialize_component(c) for c in job.components]
    if include_logs:
        data["logs"] = [{"id": l.id, "level": l.level, "message": l.message, "created_at": l.created_at.isoformat()} for l in job.logs]
    return data


def _serialize_component(comp: Component) -> Dict[str, Any]:
    return {
        "id": comp.id,
        "name": comp.name,
        "selected_symbol_id": comp.selected_symbol_id,
        "selected_footprint_id": comp.selected_footprint_id,
        "selected_model_id": comp.selected_model_id,
        "candidates": [_serialize_candidate(c) for c in comp.candidates],
    }


def _serialize_candidate(cf: CandidateFile) -> Dict[str, Any]:
    return {
        "id": cf.id,
        "type": cf.type.value,
        "name": cf.name,
        "description": cf.description,
        "pin_count": cf.pin_count,
        "pad_count": cf.pad_count,
        "heuristic_score": cf.heuristic_score,
        "ai_score": cf.ai_score,
        "combined_score": cf.combined_score,
        "rel_path": cf.rel_path,
    }
