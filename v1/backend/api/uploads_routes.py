from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

import httpx
import os
import tempfile
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.deps import get_db
from ..db.models import CandidateFile, CandidateType, Component, JobStatus
from ..services import extract, jobs as job_service, ollama as ollama_service, ranking, scan as scan_service, uploads as upload_service

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_EXTS = {".zip", ".kicad_sym", ".kicad_mod", ".stp", ".step", ".wrl", ".obj"}
MAX_URL_DOWNLOAD_BYTES = 100 * 1024 * 1024  # 100 MB
REJECT_CONTENT_TYPES = {"text/html", "text/plain"}


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
    return await _process_upload(request, db, config, stored_path, file.filename, md5=md5)


@router.post("/from-url", status_code=status.HTTP_201_CREATED)
async def upload_from_url(
    request: Request,
    payload: Dict[str, str],
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    config = get_config(request)
    url = (payload or {}).get("url") if isinstance(payload, dict) else None
    if not url:
        raise HTTPException(status_code=400, detail="url is required")
    try:
        stored_path, md5, filename = await _download_url_to_uploads(url, Path(config.uploads_dir))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to fetch URL: {exc}") from exc
    return await _process_upload(request, db, config, stored_path, filename, md5=md5)


async def _process_upload(
    request: Request,
    db: Session,
    config: AppConfig,
    stored_path: Path,
    original_filename: str,
    md5: str | None = None,
) -> Dict[str, Any]:
    stored_path = Path(stored_path)
    md5 = md5 or upload_service.compute_md5(stored_path)
    existing = job_service.get_job_by_md5(db, md5)
    if existing:
        try:
            stored_path.unlink(missing_ok=True)
        except Exception:
            pass
        job_service.log_job(db, existing, f"Duplicate upload ignored for {original_filename}")
        return {
            "duplicate": True,
            "job_id": existing.id,
            "status": existing.status.value,
            "request_id": getattr(request.state, "request_id", None),
        }

    job = job_service.create_job(db, md5=md5, original_filename=original_filename, stored_path=stored_path, status=JobStatus.analyzing)

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


async def _download_url_to_uploads(url: str, destination_dir: Path) -> tuple[Path, str, str]:
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            filename = _guess_filename(url, resp)
            suffix = Path(filename).suffix.lower()
            if suffix not in ALLOWED_EXTS:
                raise ValueError(f"Unsupported extension {suffix}")
            content_length = resp.headers.get("content-length")
            if content_length and int(content_length) > MAX_URL_DOWNLOAD_BYTES:
                raise ValueError("File too large to fetch")
            content_type = (resp.headers.get("content-type") or "").split(";")[0].lower()
            if content_type in REJECT_CONTENT_TYPES:
                raise ValueError(f"Rejected content type {content_type}")
            destination_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(prefix="upload_url_", dir=destination_dir)
            written = 0
            try:
                with os.fdopen(fd, "wb") as out_file:
                    async for chunk in resp.aiter_bytes(chunk_size=8192):
                        if chunk:
                            written += len(chunk)
                            if written > MAX_URL_DOWNLOAD_BYTES:
                                raise ValueError("File too large to fetch")
                            out_file.write(chunk)
                stored_path = Path(destination_dir) / f"{Path(tmp_path).name}_{upload_service.sanitize_filename(filename)}"
                Path(tmp_path).rename(stored_path)
                md5 = upload_service.compute_md5(stored_path)
                return stored_path, md5, filename
            except Exception:
                Path(tmp_path).unlink(missing_ok=True)
                raise


def _guess_filename(url: str, response: httpx.Response) -> str:
    cd = response.headers.get("content-disposition", "")
    name = ""
    if "filename=" in cd:
        try:
            name = cd.split("filename=")[1].split(";")[0].strip('"; ')
        except Exception:
            name = ""
    if not name:
        name = Path(url.split("?")[0]).name
    if not name:
        name = "download"
    return upload_service.sanitize_filename(name)
