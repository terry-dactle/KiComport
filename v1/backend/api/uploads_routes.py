from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.deps import get_db
from ..db.models import CandidateFile, CandidateType, Component, JobStatus
from ..services import extract, jobs as job_service, ollama as ollama_service, ranking, scan as scan_service, uploads as upload_service

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_EXTS = {".zip", ".kicad_sym", ".kicad_mod", ".stp", ".step", ".wrl", ".obj"}


def get_config(request: Request) -> AppConfig:
    cfg = getattr(request.app.state, "config", None)
    if not cfg:
        raise HTTPException(status_code=500, detail="Config not loaded")
    return cfg


@router.post("", status_code=status.HTTP_201_CREATED)
async def upload_file(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    config = get_config(request)
    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXTS:
        raise HTTPException(status_code=400, detail=f"Unsupported extension {suffix}")

    stored_path, md5 = upload_service.save_upload(file.file, Path(config.uploads_dir), file.filename)
    existing = job_service.get_job_by_md5(db, md5)
    if existing:
        # Do not retain the new copy; reuse original job
        try:
            Path(stored_path).unlink(missing_ok=True)
        except Exception:
            pass
        job_service.log_job(db, existing, f"Duplicate upload ignored for {file.filename}")
        return {"duplicate": True, "job_id": existing.id, "status": existing.status.value, "request_id": getattr(request.state, "request_id", None)}

    job = job_service.create_job(db, md5=md5, original_filename=file.filename, stored_path=stored_path, status=JobStatus.analyzing)

    try:
        extracted_dir = extract.extract_if_needed(stored_path, Path(config.temp_dir))
        job_service.set_extracted_path(db, job, extracted_dir)
    except Exception as exc:  # bad zip etc
        job_service.update_status(db, job, JobStatus.error, f"Extraction failed: {exc}")
        raise HTTPException(status_code=400, detail="Failed to extract upload") from exc

    candidates = scan_service.scan_candidates(Path(job.extracted_path))
    if not candidates:
        job_service.update_status(db, job, JobStatus.error, "No candidates detected")
        return {"job_id": job.id, "status": job.status.value, "message": "No candidates detected"}

    comp_objs, response_components = _persist_components(db, job.id, candidates)

    if config.ollama_enabled:
        try:
            client = ollama_service.OllamaClient(
                config.ollama_base_url,
                config.ollama_model,
                config.ollama_timeout_sec,
                config.ollama_max_retries,
            )
            scores = await client.score_candidates(job.id, comp_objs)
            if scores:
                for comp in comp_objs:
                    for cand in comp.candidates:
                        if cand.id in scores:
                            score_entry = scores[cand.id]
                            cand.ai_score = float(score_entry[0]) if isinstance(score_entry, (list, tuple)) else float(score_entry)
                            if isinstance(score_entry, (list, tuple)) and len(score_entry) > 1:
                                cand.ai_reason = score_entry[1]
                            cand.combined_score = ranking.calc_combined(cand)
                            db.add(cand)
                job_service.log_job(db, job, "Ollama scoring applied")
            else:
                job.ai_failed = True
                job_service.log_job(db, job, "Ollama returned no scores", level="WARNING")
        except Exception as exc:
            job.ai_failed = True
            job_service.log_job(db, job, f"Ollama scoring failed: {exc}", level="ERROR")

    job_service.update_status(db, job, JobStatus.waiting_for_user, "Scan complete; awaiting selection")
    return {"job_id": job.id, "status": job.status.value, "components": response_components, "request_id": getattr(request.state, "request_id", None)}


def _persist_components(db: Session, job_id: int, candidates: list[scan_service.CandidateData]) -> tuple[list[Component], list[Dict[str, Any]]]:
    grouped: dict[str, list[scan_service.CandidateData]] = {}
    for cand in candidates:
        grouped.setdefault(cand.name, []).append(cand)

    comp_objs: list[Component] = []
    response_components: list[Dict[str, Any]] = []
    for name, cand_list in grouped.items():
        comp = Component(job_id=job_id, name=name)
        db.add(comp)
        db.flush()
        response_candidates = []
        for cand in cand_list:
            cf = CandidateFile(
                component_id=comp.id,
                type=cand.type,
                path=str(cand.path),
                rel_path=str(cand.rel_path),
                name=cand.name,
                description=cand.description,
                pin_count=cand.pin_count,
                pad_count=cand.pad_count,
                heuristic_score=cand.heuristic_score,
                metadata_json=cand.metadata or {},
            )
            cf.quality_score = ranking.quality_score_for_candidate(cf)
            cf.combined_score = ranking.calc_combined(cf)
            db.add(cf)
            db.flush()
            response_candidates.append(_serialize_candidate(cf))
        comp_objs.append(comp)
        response_components.append({"id": comp.id, "name": comp.name, "candidates": response_candidates})
    # Apply consistency bonuses (pin vs pad match) and recompute combined
    for comp in comp_objs:
        ranking.consistency_adjustment(comp)
        ranking.update_combined_for_candidates(comp.candidates)
        for c in comp.candidates:
            db.add(c)
    return comp_objs, response_components


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
        "quality_score": cf.quality_score,
        "feedback_score": cf.feedback_score,
        "ai_reason": cf.ai_reason,
        "rel_path": cf.rel_path,
        "metadata": cf.metadata_json,
    }
