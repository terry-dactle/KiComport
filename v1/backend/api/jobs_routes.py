from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status, File, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.deps import get_db
from ..db.models import CandidateFile, CandidateType, Component, Job, JobStatus
from ..services import importer, jobs as job_service, ranking
from ..services import extract, scan as scan_service
from ..services import preview as preview_service
from ..services import uploads as upload_service
from ..services import candidate_cache
from .uploads_routes import ALLOWED_EXTS, _persist_components, _validate_zip_or_raise

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def get_config(request: Request) -> AppConfig:
    cfg = getattr(request.app.state, "config", None)
    if not cfg:
        raise HTTPException(status_code=500, detail="Config not loaded")
    return cfg


def _find_candidate_by_name(root: Path, name: str) -> Path | None:
    if not name:
        return None
    try:
        for match in root.rglob(name):
            if match.is_file():
                return match
    except Exception:
        return None
    return None


def _latest_job_dir(temp_dir: Path, job_id: int) -> Path | None:
    try:
        matches = [
            p
            for p in temp_dir.iterdir()
            if p.is_dir() and p.name.startswith(f"job_{job_id}")
        ]
    except Exception:
        return None
    if not matches:
        return None
    try:
        matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        matches.sort()
    return matches[0]


def _resolve_extracted_root(job: Job, cfg: AppConfig, db: Session) -> Path | None:
    extracted_root = Path(job.extracted_path) if job.extracted_path else None
    if extracted_root and extracted_root.exists() and extracted_root.is_dir():
        return extracted_root

    temp_dir = Path(cfg.temp_dir)
    candidate = temp_dir / f"job_{job.id}"
    if candidate.exists() and candidate.is_dir():
        job.extracted_path = str(candidate)
        db.add(job)
        db.flush()
        return candidate

    latest = _latest_job_dir(temp_dir, job.id)
    if latest:
        job.extracted_path = str(latest)
        db.add(job)
        db.flush()
        return latest

    return extracted_root


def _resolve_stored_path(job: Job, cfg: AppConfig, db: Session) -> Path | None:
    stored_path = Path(job.stored_path) if job.stored_path else None
    if stored_path and stored_path.exists():
        return stored_path

    uploads_dir = Path(cfg.uploads_dir)
    if not uploads_dir.exists() or not job.original_filename:
        return stored_path

    safe_name = upload_service.sanitize_filename(job.original_filename)
    direct = uploads_dir / (stored_path.name if stored_path else "")
    if direct and direct.exists():
        job.stored_path = str(direct)
        db.add(job)
        db.flush()
        return direct

    matches = [
        p
        for p in uploads_dir.iterdir()
        if p.is_file() and p.name.endswith(f"_{safe_name}")
    ]
    if not matches:
        return stored_path
    if len(matches) == 1:
        job.stored_path = str(matches[0])
        db.add(job)
        db.flush()
        return matches[0]

    md5 = job.md5
    for candidate in matches:
        try:
            if upload_service.compute_md5(candidate) == md5:
                job.stored_path = str(candidate)
                db.add(job)
                db.flush()
                return candidate
        except Exception:
            continue
    return stored_path


def _has_attach_dirs(root: Path) -> bool:
    try:
        return any(p.is_dir() and p.name.startswith("attach_") for p in root.iterdir())
    except Exception:
        return False


def _ensure_candidate_path(
    cand: CandidateFile,
    cfg: AppConfig,
    db: Session,
) -> Path | None:
    path = Path(cand.path)
    if path.exists():
        return path

    job = cand.component.job if cand.component else None
    if not job:
        return None

    rel_raw = (cand.rel_path or "").strip()
    rel_path = Path(rel_raw) if rel_raw and rel_raw != "." else None
    if rel_path and rel_path.is_absolute():
        rel_path = None

    def _cache_path(source: Path) -> Path | None:
        try:
            cache_dir = candidate_cache.cache_root(cfg, job.id)
            cached = candidate_cache.cache_candidate_file(
                source,
                cache_dir,
                cand.rel_path,
                cand.name,
            )
        except Exception:
            return None
        if str(cached) != cand.path:
            cand.path = str(cached)
            db.add(cand)
            db.flush()
        return cached

    def _resolve_in_root(root: Path) -> Path | None:
        if rel_path:
            candidate_path = root / rel_path
            if candidate_path.exists():
                cached = _cache_path(candidate_path)
                return cached or candidate_path
        fallback_name = path.name or (rel_path.name if rel_path else "") or cand.name
        candidate_path = _find_candidate_by_name(root, fallback_name)
        if candidate_path:
            try:
                cand.rel_path = str(candidate_path.relative_to(root))
            except Exception:
                pass
            cached = _cache_path(candidate_path)
            return cached or candidate_path
        return None

    extracted_root = _resolve_extracted_root(job, cfg, db)
    if extracted_root and extracted_root.exists() and extracted_root.is_dir():
        found = _resolve_in_root(extracted_root)
        if found:
            return found
        if rel_path and "attach_" in rel_path.parts:
            return None

    stored_path = _resolve_stored_path(job, cfg, db)
    if not stored_path or not stored_path.exists():
        return None

    if extracted_root and extracted_root.exists() and _has_attach_dirs(extracted_root):
        target_dir = Path(cfg.temp_dir) / f"job_{job.id}_rebuild_{uuid.uuid4().hex}"
    else:
        target_dir = extracted_root if extracted_root else (Path(cfg.temp_dir) / f"job_{job.id}")

    try:
        extracted_dir = extract.extract_if_needed(
            stored_path,
            Path(cfg.temp_dir),
            original_filename=job.original_filename,
            target_dir=target_dir,
        )
    except Exception:
        return None

    if not job.extracted_path or not Path(job.extracted_path).exists():
        job.extracted_path = str(extracted_dir)
        db.add(job)

    return _resolve_in_root(extracted_dir)


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


@router.post("/{job_id}/attach-file")
async def attach_file_to_job(
    job_id: int,
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    config = get_config(request)
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported extension {suffix}")

    stored_path: Path | None = None
    try:
        stored_path, md5 = upload_service.save_upload(file.file, Path(config.uploads_dir), file.filename)
        _validate_zip_or_raise(stored_path, suffix)
        base_dir = Path(job.extracted_path) if job.extracted_path else (Path(config.temp_dir) / f"job_{job.id}")
        base_dir.mkdir(parents=True, exist_ok=True)
        if not job.extracted_path:
            job.extracted_path = str(base_dir)
            db.add(job)
            db.flush()

        attach_dir = base_dir / f"attach_{uuid.uuid4().hex}"
        extracted_dir = extract.extract_if_needed(
            stored_path,
            Path(config.temp_dir),
            original_filename=file.filename,
            target_dir=attach_dir,
        )
        candidates = scan_service.scan_candidates(Path(extracted_dir))
        if not candidates:
            raise HTTPException(status_code=400, detail="No candidates detected in attached file")
        comp_objs, response_components = _persist_components(
            db,
            job.id,
            candidates,
            cache_root=candidate_cache.cache_root(config, job.id),
        )
        job_service.log_job(db, job, f"Attached file {file.filename} to job")
        return {
          "job_id": job.id,
          "components_added": [c.id for c in comp_objs],
          "component_details": response_components,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Attach failed: {exc}") from exc
    finally:
        if stored_path:
            try:
                stored_path.unlink(missing_ok=True)
            except Exception:
                pass


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
    # Always import into a single stable library so KiCad only needs a one-time mapping.
    subfolder = importer.DEFAULT_SUBFOLDER
    rename_to = payload.get("rename_to") if isinstance(payload, dict) else None
    counts, destinations = importer.import_job_selection(
        db,
        job,
        symbol_dir=symbol_root,
        footprint_dir=footprint_root,
        model_dir=model_root,
        subfolder=subfolder,
        rename_to=rename_to,
    )
    return {
        "job_id": job.id,
        "status": job.status.value,
        "imported": counts,
        "destinations": destinations,
        "request_id": getattr(request.state, "request_id", None),
    }


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
        extracted_dir = extract.extract_if_needed(
            Path(job.stored_path),
            Path(config.temp_dir),
            original_filename=job.original_filename,
            target_dir=Path(config.temp_dir) / f"job_{job.id}",
        )
        job_service.set_extracted_path(db, job, extracted_dir)
    except Exception as exc:
        job_service.update_status(db, job, JobStatus.error, f"Retry extraction failed: {exc}")
        raise HTTPException(status_code=400, detail=f"Retry failed: {exc}") from exc

    candidates = scan_service.scan_candidates(Path(job.extracted_path))
    if not candidates:
        job_service.update_status(db, job, JobStatus.error, "No candidates detected on retry")
        return {"job_id": job.id, "status": job.status.value, "message": job.message}

    comp_objs, _ = _persist_components(
        db,
        job.id,
        candidates,
        cache_root=candidate_cache.cache_root(config, job.id),
    )
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
def candidate_preview(candidate_id: int, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    cand = db.get(CandidateFile, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cfg = get_config(request)
    try:
        path_obj = _ensure_candidate_path(cand, cfg, db)
        if not path_obj or not path_obj.exists():
            raise HTTPException(status_code=404, detail="Candidate file not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to access candidate path: {exc}") from exc
    try:
        with open(path_obj, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read(2000)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to read candidate: {exc}") from exc
    return {
        "id": cand.id,
        "type": cand.type.value,
        "name": cand.name,
        "path": str(path_obj),
        "rel_path": cand.rel_path,
        "content_preview": content,
    }


@router.get("/candidates/{candidate_id}/render")
def candidate_render(candidate_id: int, request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    cand = db.get(CandidateFile, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cfg = get_config(request)
    try:
        if not _ensure_candidate_path(cand, cfg, db):
            raise FileNotFoundError(cand.path)
        image_data, note = preview_service.render_candidate_preview(cand)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Candidate file not found")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Render failed: {exc}") from exc
    return {"id": cand.id, "image_data": image_data, "note": note, "type": cand.type.value}


@router.get("/candidates/{candidate_id}/download")
def candidate_download(candidate_id: int, request: Request, db: Session = Depends(get_db)):
    cand = db.get(CandidateFile, candidate_id)
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found")
    cfg = get_config(request)
    path = _ensure_candidate_path(cand, cfg, db)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Candidate file not found")
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")
