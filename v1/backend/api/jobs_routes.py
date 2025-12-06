from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.deps import get_db
from ..db.models import CandidateFile, CandidateType, Component, Job, JobStatus
from ..services import importer, jobs as job_service, ranking
from ..services import extract, scan as scan_service
from ..services import preview as preview_service

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


def _safe_target(base: Path, sub_path: str | None) -> Path:
    if not sub_path:
        return base
    sub = Path(sub_path)
    # prevent escaping the root
    clean = (base / sub).resolve()
    try:
        clean.relative_to(base.resolve())
    except Exception:
        return base
    return clean


@router.post("/{job_id}/import")
async def import_job(job_id: int, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    config = get_config(request)
    symbol_root = Path(config.kicad_symbol_dir)
    footprint_root = Path(config.kicad_footprint_dir)
    model_root = Path(config.kicad_3d_dir)
    counts = importer.import_job_selection(
        db,
        job,
        symbol_dir=_safe_target(symbol_root, payload.get("symbol_subdir")),
        footprint_dir=_safe_target(footprint_root, payload.get("footprint_subdir")),
        model_dir=_safe_target(model_root, payload.get("model_subdir")),
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


@router.post("/{job_id}/retry", status_code=status.HTTP_202_ACCEPTED)
def retry_job(job_id: int, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    config = get_config(request)
    if not Path(job.stored_path).exists():
        raise HTTPException(status_code=400, detail="Stored file not found; cannot retry")
    # reset components/logs state
    for comp in list(job.components):
        db.delete(comp)
    job_service.reset_job_selection(db, job)
    job_service.update_status(db, job, JobStatus.analyzing, "Retry triggered")
    try:
        extracted_dir = extract.extract_if_needed(Path(job.stored_path), Path(config.temp_dir))
        job_service.set_extracted_path(db, job, extracted_dir)
    except Exception as exc:
        job_service.update_status(db, job, JobStatus.error, f"Retry extraction failed: {exc}")
        raise HTTPException(status_code=400, detail=f"Retry failed: {exc}") from exc

    candidates = scan_service.scan_candidates(Path(job.extracted_path))
    if not candidates:
        job_service.update_status(db, job, JobStatus.error, "No candidates detected on retry")
        return {"job_id": job.id, "status": job.status.value, "message": job.message}

    comp_objs, _ = _persist_components(db, job.id, candidates)
    job_service.update_status(db, job, JobStatus.waiting_for_user, "Scan complete after retry")
    return {"job_id": job.id, "status": job.status.value}


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(job_id: int, request: Request, db: Session = Depends(get_db)) -> None:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    cfg = get_config(request)
    # delete extracted/stored files
    for path_str in [job.stored_path, job.extracted_path]:
        if path_str:
            try:
                path = Path(path_str)
                if path.is_dir():
                    import shutil
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    path.unlink(missing_ok=True)
            except Exception:
                pass
    db.delete(job)


@router.get("/candidates/{candidate_id}/preview")
def candidate_preview(candidate_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    cand = db.get(CandidateFile, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        path_obj = Path(cand.path)
        if not path_obj.exists():
            raise HTTPException(status_code=404, detail=f"File not found at {cand.path}")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to access candidate path: {exc}") from exc
    try:
        with open(cand.path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(2000)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read candidate: {exc}") from exc
    return {
        "id": cand.id,
        "type": cand.type.value,
        "name": cand.name,
        "path": cand.path,
        "rel_path": cand.rel_path,
        "content_preview": content,
    }


@router.get("/candidates/{candidate_id}/render")
def candidate_render(candidate_id: int, db: Session = Depends(get_db)) -> Dict[str, Any]:
    cand = db.get(CandidateFile, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    try:
        image_data, note = preview_service.render_candidate_preview(cand)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Candidate file not found")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Render failed: {exc}") from exc
    return {"id": cand.id, "image_data": image_data, "note": note, "type": cand.type.value}
