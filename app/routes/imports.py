from __future__ import annotations

import difflib
import hashlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional
import urllib.request
import urllib.error
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, Request
from pydantic import BaseModel, HttpUrl

from ..analysis import analyze_job as run_analysis
from ..config import AppConfig
from ..lib_organizer import organize_job_assets
from ..models import ImportJob, ImportJobStatus, ReviewStatus, PlanCandidate
from ..ollama_client import OllamaClient
from ..previews import preview_library_tables
from ..kicad_tables import apply_to_tables, backup_tables, restore_from_backups, restore_from_paths
from ..security import require_ui_token
from .. import audit, storage


router = APIRouter(prefix="/imports", tags=["imports"])


def get_config(request: Request) -> AppConfig:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="Configuration missing")
    return config


def get_ollama_client(request: Request) -> Optional[OllamaClient]:
    return getattr(request.app.state, "ollama_client", None)


def _require_action_token(request: Request, config: AppConfig = Depends(get_config)) -> None:
    require_ui_token(request, config)


def _select_candidate_by_path(job: ImportJob, kind: str, path: str) -> Optional[PlanCandidate]:
    if not job.plan or not path:
        return None
    for candidate in job.plan.candidates:
        if candidate.kind == kind and candidate.path == path:
            return candidate
    return None


def _select_highest_candidate(job: ImportJob, kind: str) -> Optional[PlanCandidate]:
    if not job.plan:
        return None
    filtered = [cand for cand in job.plan.candidates if cand.kind == kind]
    if not filtered:
        return None
    return max(filtered, key=lambda cand: cand.score if cand.score is not None else 0.0)


def _delete_stored_file(path: str) -> None:
    try:
        file_path = Path(path)
        if file_path.is_file():
            file_path.unlink(missing_ok=True)
        elif file_path.is_dir():
            for child in file_path.glob("*"):
                if child.is_file():
                    child.unlink()
    except Exception:
        # Best-effort cleanup; ignore failures.
        return


def _persist_job_from_bytes(
    contents: bytes,
    display_name: str,
    target_relative: Path,
    incoming_dir: Path,
) -> ImportJob:
    file_md5 = _compute_md5(contents)
    stored_path = incoming_dir / target_relative
    stored_path.parent.mkdir(parents=True, exist_ok=True)

    existing_job = next(
        (job for job in storage.list_jobs() if job.filename == str(target_relative) and job.md5 == file_md5),
        None,
    )
    if existing_job:
        existing_file = Path(existing_job.stored_path)
        if not existing_file.exists():
            existing_file.parent.mkdir(parents=True, exist_ok=True)
            existing_file.write_bytes(contents)
        audit.log_event(
            "upload_deduped",
            existing_job.id,
            {"filename": str(target_relative), "stored_path": existing_job.stored_path},
        )
        return existing_job

    if stored_path.exists():
        existing_md5 = _compute_md5(stored_path.read_bytes())
        if existing_md5 != file_md5:
            stored_path.write_bytes(contents)
    else:
        stored_path.write_bytes(contents)

    job = ImportJob(
        id=str(uuid4()),
        filename=display_name,
        stored_path=str(stored_path),
        md5=file_md5,
        status=ImportJobStatus.uploaded,
        created_at=datetime.now(timezone.utc),
    )
    storage.save_job(job)
    audit.log_event("upload", job.id, {"filename": display_name})
    return job


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


class ReviewPayload(BaseModel):
    approval_status: Optional[ReviewStatus] = None
    review_notes: Optional[str] = None


class ApplyPayload(BaseModel):
    symbol_path: Optional[str] = None
    footprint_path: Optional[str] = None
    model_paths: Optional[List[str]] = None


class DownloadPayload(BaseModel):
    urls: List[HttpUrl]


def _safe_relative_path(relative_path: Optional[str], fallback: str) -> Path:
    if not relative_path:
        return Path(fallback)
    candidate = Path(relative_path)
    cleaned_parts = [part for part in candidate.parts if part not in ("..", ".", "")]
    if not cleaned_parts:
        return Path(fallback)
    return Path(*cleaned_parts)


@router.post("/upload", response_model=ImportJob)
async def upload_import(
    file: UploadFile = File(...),
    relative_path: Optional[str] = Form(None),
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> ImportJob:
    incoming_dir = Path(config.paths.incoming)
    incoming_dir.mkdir(parents=True, exist_ok=True)

    contents = await file.read()
    filename = file.filename or "upload.bin"
    target_relative = _safe_relative_path(relative_path, filename)
    display_name = str(target_relative)
    return _persist_job_from_bytes(contents, display_name, target_relative, incoming_dir)


@router.post("/download", response_model=List[ImportJob])
async def download_imports(
    payload: DownloadPayload,
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> List[ImportJob]:
    incoming_dir = Path(config.paths.incoming)
    incoming_dir.mkdir(parents=True, exist_ok=True)

    jobs: List[ImportJob] = []
    for url in payload.urls:
        parsed = urlparse(str(url))
        name = Path(parsed.path).name or "download.bin"
        target_relative = _safe_relative_path(name, name)
        try:
            with urllib.request.urlopen(str(url), timeout=15) as resp:
                contents = resp.read()
            job = _persist_job_from_bytes(contents, str(target_relative), target_relative, incoming_dir)
            audit.log_event("download", job.id, {"url": str(url), "filename": str(target_relative)})
            jobs.append(job)
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Failed to download {url}: {exc}") from exc
    return jobs


@router.post("/{job_id}/analyze", response_model=ImportJob)
async def analyze_import(
    job_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    plan = await run_analysis(job, config, get_ollama_client(request))
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
async def apply_import(
    job_id: str,
    payload: ApplyPayload | None = None,
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.plan:
        raise HTTPException(status_code=400, detail="Job must be analyzed before apply.")
    if job.approval_status != ReviewStatus.approved:
        raise HTTPException(status_code=400, detail="Job must be approved before apply.")

    payload = payload or ApplyPayload()
    sym_candidate = _select_candidate_by_path(job, "symbol", payload.symbol_path or "")
    fp_candidate = _select_candidate_by_path(job, "footprint", payload.footprint_path or "")

    if not sym_candidate and not fp_candidate:
        # Fall back to highest-scoring candidates; if still missing, abort apply.
        sym_candidate = _select_highest_candidate(job, "symbol")
        fp_candidate = _select_highest_candidate(job, "footprint")
    if not sym_candidate and not fp_candidate:
        raise HTTPException(
            status_code=400,
            detail="No symbol or footprint candidates available to apply.",
        )

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")

    backups = backup_tables(job, config, timestamp)
    job.backup_sym_lib_table = backups.get("sym_lib_table")
    job.backup_fp_lib_table = backups.get("fp_lib_table")
    installed_assets = organize_job_assets(job, config, allowed_models=payload.model_paths)
    job.installed_assets = installed_assets
    diffs = apply_to_tables(job, config, sym_candidate=sym_candidate, fp_candidate=fp_candidate)
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
            "installed_assets": installed_assets,
        },
    )
    return job


@router.post("/{job_id}/rollback", response_model=ImportJob)
async def rollback_import(
    job_id: str,
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if not job.backup_sym_lib_table or not job.backup_fp_lib_table:
        raise HTTPException(status_code=400, detail="No backups recorded for this job.")

    restore_from_backups(job, config)
    job.status = ImportJobStatus.uploaded
    job.approval_status = ReviewStatus.pending
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
    _: None = Depends(_require_action_token),
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
    job.approval_status = ReviewStatus.pending
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
async def get_job(
    job_id: str,
    _: None = Depends(_require_action_token),
) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@router.get("", response_model=List[ImportJob])
async def list_import_jobs(_: None = Depends(_require_action_token)) -> List[ImportJob]:
    return storage.list_jobs()


@router.get("/{job_id}/diff")
async def job_diff(
    job_id: str,
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> Dict[str, str]:
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
async def preview_job(
    job_id: str,
    _: None = Depends(_require_action_token),
) -> dict:
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
async def preview_tables(
    job_id: str,
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> dict:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return preview_library_tables(config, job)


@router.post("/{job_id}/review", response_model=ImportJob)
async def review_job(
    job_id: str,
    payload: ReviewPayload,
    _: None = Depends(_require_action_token),
) -> ImportJob:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if payload.approval_status:
        job.approval_status = payload.approval_status
    if payload.review_notes is not None:
        note = payload.review_notes.strip()
        job.review_notes = note or None

    storage.save_job(job)
    audit.log_event(
        "review",
        job.id,
        {
            "approval_status": job.approval_status,
            "review_notes": job.review_notes or "",
        },
    )
    return job


@router.delete("/{job_id}")
async def delete_job(
    job_id: str,
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> dict:
    job = storage.load_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    storage.delete_job(job_id)
    _delete_stored_file(job.stored_path)
    audit.log_event("delete_job", job.id, {"stored_path_deleted": job.stored_path})
    return {"deleted": True}


@router.post("/purge_incoming")
async def purge_incoming(
    config: AppConfig = Depends(get_config),
    _: None = Depends(_require_action_token),
) -> dict:
    incoming_dir = Path(config.paths.incoming)
    removed = 0
    if incoming_dir.exists():
        for child in incoming_dir.glob("*"):
            if child.is_file():
                child.unlink()
                removed += 1
    audit.log_event("purge_incoming", "all", {"removed": removed})
    return {"removed": removed}
