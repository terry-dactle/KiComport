from __future__ import annotations

import difflib
import hashlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, Request
from pydantic import BaseModel

from ..analysis import analyze_job as run_analysis
from ..config import AppConfig
from ..models import ImportJob, ImportJobStatus
from ..ollama_client import OllamaClient
from ..previews import preview_library_tables
from ..kicad_tables import apply_to_tables, backup_tables, restore_from_backups, restore_from_paths
from .. import audit, storage


router = APIRouter(prefix="/imports", tags=["imports"])


def get_config(request: Request) -> AppConfig:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="Configuration missing")
    return config


def get_ollama_client(request: Request) -> Optional[OllamaClient]:
    return getattr(request.app.state, "ollama_client", None)


def _compute_md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def _diff_paths(before: Path, after: Path) -> str:
    if not before.exists() or not after.exists():
        return ""
    before_text = before.read_text(encoding="utf-8")
    after_text = after.read_text(encoding="utf-8")
    return "\n".join(
        difflib.unified_diff(
            before_text.splitlines(),
            after_text.splitlines(),
            fromfile=str(before),
            tofile=str(after),
            lineterm="",
        )
    )


class UndoRequest(BaseModel):
    event_id: Optional[str] = None
    sym_backup: Optional[str] = None
    fp_backup: Optional[str] = None


@router.post("/upload", response_model=ImportJob)
async def upload_import(file: UploadFile = File(...), config: AppConfig = Depends(get_config)) -> ImportJob:
    incoming_dir = Path(config.paths.incoming)
    incoming_dir.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    filename = file.filename or "upload.bin"
    file_md5 = _compute_md5(contents)
    stored_path = incoming_dir / filename

    if stored_path.exists():
        existing_md5 = _compute_md5(stored_path.read_bytes())
        if existing_md5 != file_md5:
            stored_path.write_bytes(contents)
    else:
        stored_path.write_bytes(contents)

    job = ImportJob(
        id=str(uuid4()),
        filename=filename,
        stored_path=str(stored_path),
        md5=file_md5,
        status=ImportJobStatus.uploaded,
        created_at=datetime.now(timezone.utc),
    )
    storage.save_job(job)
    audit.log_event("upload", job.id, {"filename": filename})
    return job


@router.post("/{job_id}/analyze", response_model=ImportJob)
async def analyze_import(
    job_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    plan = run_analysis(job, config, get_ollama_client(request))
    job.plan = plan
    job.status = ImportJobStatus.analyzed
    storage.save_job(job)
    audit.log_event(
        "analyze",
        job.id,
        {
            "detected_types": plan.detected_types,
            "candidates": len(plan.candidates),
        },
    )
    return job


@router.post("/{job_id}/apply", response_model=ImportJob)
async def apply_import(job_id: str, config: AppConfig = Depends(get_config)) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.plan:
        raise HTTPException(status_code=400, detail="Job must be analyzed before apply.")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    backups = backup_tables(job, config, timestamp)
    job.backup_sym_lib_table = backups.get("sym_lib_table")
    job.backup_fp_lib_table = backups.get("fp_lib_table")
    diffs = apply_to_tables(job, config)
    job.table_diffs = diffs
    job.status = ImportJobStatus.applied
    storage.save_job(job)
    audit.log_event(
        "apply",
        job.id,
        {
            "sym_backup": job.backup_sym_lib_table,
            "fp_backup": job.backup_fp_lib_table,
            "diffs": diffs,
        },
    )
    return job


@router.post("/{job_id}/rollback", response_model=ImportJob)
async def rollback_import(job_id: str, config: AppConfig = Depends(get_config)) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.backup_sym_lib_table or not job.backup_fp_lib_table:
        raise HTTPException(status_code=400, detail="No backups recorded for this job.")

    restore_from_backups(job, config)
    job.status = ImportJobStatus.uploaded
    job.table_diffs = None
    storage.save_job(job)
    audit.log_event(
        "rollback",
        job.id,
        {
            "sym_backup": job.backup_sym_lib_table,
            "fp_backup": job.backup_fp_lib_table,
            "restored": True,
        },
    )
    return job


@router.post("/{job_id}/undo", response_model=ImportJob)
async def undo_to_backup(
    job_id: str,
    payload: UndoRequest,
    config: AppConfig = Depends(get_config),
) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    sym_backup = payload.sym_backup or job.backup_sym_lib_table
    fp_backup = payload.fp_backup or job.backup_fp_lib_table

    if payload.event_id:
        event = audit.get_event(payload.event_id)
        if not event:
            raise HTTPException(status_code=404, detail="Audit event not found")
        details = event.get("details") or {}
        sym_backup = details.get("sym_backup") or sym_backup
        fp_backup = details.get("fp_backup") or fp_backup

    if not sym_backup and not fp_backup:
        raise HTTPException(status_code=400, detail="No backup paths provided or recorded for undo")

    restore_from_paths(sym_backup, fp_backup, config)
    job.status = ImportJobStatus.uploaded
    job.table_diffs = None
    storage.save_job(job)
    audit.log_event(
        "undo",
        job.id,
        {
            "sym_backup": sym_backup,
            "fp_backup": fp_backup,
            "event_source": payload.event_id,
        },
    )
    return job


@router.get("/{job_id}", response_model=ImportJob)
async def get_job(job_id: str) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("", response_model=List[ImportJob])
async def list_import_jobs() -> List[ImportJob]:
    return storage.list_jobs()


@router.get("/{job_id}/diff")
async def job_diff(job_id: str, config: AppConfig = Depends(get_config)) -> Dict[str, str]:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    diffs = job.table_diffs or {}
    if diffs:
        return diffs
    results: Dict[str, str] = {}
    root = Path(config.paths.root)
    if job.backup_sym_lib_table:
        results["sym_lib_table"] = _diff_paths(Path(job.backup_sym_lib_table), root / "sym-lib-table")
    if job.backup_fp_lib_table:
        results["fp_lib_table"] = _diff_paths(Path(job.backup_fp_lib_table), root / "fp-lib-table")
    return results


@router.get("/{job_id}/preview")
async def preview_job(job_id: str) -> dict:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    path = Path(job.stored_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Stored file missing on disk.")

    preview: dict = {
        "job_id": job.id,
        "path": job.stored_path,
        "is_dir": path.is_dir(),
        "size_bytes": path.stat().st_size if path.is_file() else None,
    }

    if path.is_dir():
        preview["entries"] = [child.name for child in list(path.iterdir())[:10]]
    elif path.suffix.lower() == ".zip" and zipfile.is_zipfile(path):
        try:
            with zipfile.ZipFile(path, "r") as archive:
                preview["entries"] = [info.filename for info in archive.infolist()[:10]]
        except zipfile.BadZipFile:
            preview["zip_error"] = "Invalid zip file"

    return preview


@router.get("/{job_id}/preview/tables")
async def preview_tables(job_id: str, config: AppConfig = Depends(get_config)) -> dict:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return preview_library_tables(config, job)
