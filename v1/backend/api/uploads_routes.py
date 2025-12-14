from __future__ import annotations

from pathlib import Path
from collections import Counter
import zipfile
from typing import Any, Dict

import hashlib
import ipaddress
import httpx
import os
import socket
import tempfile
from urllib.parse import urljoin, urlparse
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.deps import get_db
from ..db.models import CandidateFile, CandidateType, Component, JobStatus
from ..services import extract, jobs as job_service, ollama as ollama_service, ranking, scan as scan_service, uploads as upload_service

router = APIRouter(prefix="/api/uploads", tags=["uploads"])

ALLOWED_EXTS = {".zip", ".kicad_sym", ".kicad_mod", ".stp", ".step", ".wrl", ".obj"}
MAX_URL_DOWNLOAD_BYTES = int(os.getenv("KICOMPORT_MAX_URL_DOWNLOAD_BYTES", str(100 * 1024 * 1024)))  # 100 MB
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

    stored_path: Path | None = None
    try:
        stored_path, md5 = upload_service.save_upload(file.file, Path(config.uploads_dir), file.filename)
        _validate_zip_or_raise(stored_path, suffix)
        return await _process_upload(request, db, config, stored_path, file.filename, md5=md5)
    except ValueError as exc:
        if stored_path:
            try:
                Path(stored_path).unlink(missing_ok=True)
            except Exception:
                pass
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if stored_path:
            try:
                Path(stored_path).unlink(missing_ok=True)
            except Exception:
                pass
        # bubble up as 500 with context
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc


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
        extracted_dir = extract.extract_if_needed(
            stored_path,
            Path(config.temp_dir),
            original_filename=original_filename,
            target_dir=Path(config.temp_dir) / f"job_{job.id}",
        )
        job_service.set_extracted_path(db, job, extracted_dir)
    except ValueError as exc:
        job_service.update_status(db, job, JobStatus.error, f"Extraction rejected: {exc}")
        raise HTTPException(status_code=400, detail=f"Failed to extract upload: {exc}") from exc
    except Exception as exc:  # bad zip etc
        job_service.update_status(db, job, JobStatus.error, f"Extraction failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Failed to extract upload: {exc}") from exc

    try:
        candidates = scan_service.scan_candidates(Path(job.extracted_path))
    except Exception as exc:
        job_service.update_status(db, job, JobStatus.error, f"Scan failed: {exc}")
        raise HTTPException(status_code=500, detail=f"Scan failed: {exc}") from exc
    if not candidates:
        summary = _file_summary(Path(job.extracted_path))
        detail = "No candidates detected"
        if summary:
            detail = f"{detail}. Found {summary['files']} files; common extensions: {summary['exts']}; samples: {summary['samples']}"
        job_service.update_status(db, job, JobStatus.error, detail)
        return {"job_id": job.id, "status": job.status.value, "message": detail}

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
        seen: set[tuple[str, str]] = set()
        for cand in cand_list:
            key = (cand.type.value, str(cand.rel_path))
            if key in seen:
                continue
            seen.add(key)
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
    allow_private = os.getenv("KICOMPORT_ALLOW_PRIVATE_URL_FETCH", "").strip().lower() in {"1", "true", "yes", "on"}

    def _is_forbidden_ip(ip: ipaddress._BaseAddress) -> bool:
        if ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            return True
        if not allow_private and getattr(ip, "is_private", False):
            return True
        return False

    def _validate_fetch_url(candidate_url: str) -> None:
        parsed = urlparse(candidate_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("Only http/https URLs are supported")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in URL are not supported")
        host = parsed.hostname
        if not host:
            raise ValueError("URL must include a hostname")
        host_lower = host.lower()
        if host_lower == "localhost" or host_lower.endswith(".localhost"):
            raise ValueError("Refusing to fetch from localhost")
        try:
            ip = ipaddress.ip_address(host)
            if _is_forbidden_ip(ip):
                raise ValueError("Refusing to fetch from a local/invalid address")
            return
        except ValueError:
            pass
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except Exception as exc:
            raise ValueError(f"Could not resolve hostname {host}") from exc
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if _is_forbidden_ip(ip):
                raise ValueError("Refusing to fetch from a local/invalid address")

    async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
        next_url = url
        for _ in range(10):
            _validate_fetch_url(next_url)
            async with client.stream("GET", next_url) as resp:
                if resp.status_code in {301, 302, 303, 307, 308}:
                    location = resp.headers.get("location")
                    if not location:
                        raise ValueError(f"Redirect ({resp.status_code}) missing Location header")
                    next_url = urljoin(str(resp.url), location)
                    continue

                resp.raise_for_status()

                filename = _guess_filename(str(resp.url), resp)
                suffix = Path(filename).suffix.lower()
                content_type = (resp.headers.get("content-type") or "").split(";")[0].lower()

                if not suffix:
                    # default to zip when the server doesn't provide a filename
                    filename = f"{filename}.zip"
                    suffix = ".zip"

                if suffix not in ALLOWED_EXTS:
                    if content_type == "application/zip":
                        filename = f"{filename}.zip" if not filename.endswith(".zip") else filename
                        suffix = ".zip"
                    elif content_type in REJECT_CONTENT_TYPES:
                        raise ValueError(f"Rejected content type {content_type}")
                    else:
                        raise ValueError(f"Unsupported extension {suffix}")

                content_length = resp.headers.get("content-length")
                if content_length:
                    try:
                        length_int = int(content_length)
                    except (TypeError, ValueError):
                        length_int = None
                    if length_int is not None and length_int > MAX_URL_DOWNLOAD_BYTES:
                        raise ValueError("File too large to fetch")

                destination_dir.mkdir(parents=True, exist_ok=True)
                fd, tmp_path = tempfile.mkstemp(prefix="upload_url_", dir=destination_dir)
                written = 0
                md5 = hashlib.md5()
                try:
                    with os.fdopen(fd, "wb") as out_file:
                        async for chunk in resp.aiter_bytes(chunk_size=8192):
                            if not chunk:
                                continue
                            written += len(chunk)
                            if written > MAX_URL_DOWNLOAD_BYTES:
                                raise ValueError("File too large to fetch")
                            md5.update(chunk)
                            out_file.write(chunk)
                    stored_path = Path(destination_dir) / f"{Path(tmp_path).name}_{upload_service.sanitize_filename(filename)}"
                    Path(tmp_path).rename(stored_path)
                    _validate_zip_or_raise(stored_path, suffix, content_type, source_url=str(resp.url))
                    return stored_path, md5.hexdigest(), filename
                except Exception:
                    Path(tmp_path).unlink(missing_ok=True)
                    raise

        raise ValueError("Too many redirects while fetching URL")


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


def _file_summary(root: Path) -> Dict[str, Any]:
    if not root.exists():
        return {}
    count = 0
    ext_counter: Counter[str] = Counter()
    samples: list[str] = []
    for path in root.rglob("*"):
        if path.is_file():
            count += 1
            ext_counter[path.suffix.lower() or "<noext>"] += 1
            if len(samples) < 5:
                samples.append(str(path.relative_to(root)))
    top_exts = ", ".join(f"{ext}({n})" for ext, n in ext_counter.most_common(5))
    return {"files": count, "exts": top_exts or "none", "samples": "; ".join(samples)}


def _preview_text(path: Path, limit: int = 400) -> str:
    try:
        data = path.read_bytes()[:limit]
        if not data or b"\x00" in data:
            return ""
        return data.decode(errors="ignore")
    except Exception:
        return ""


def _validate_zip_or_raise(path: Path, suffix: str, content_type: str | None = None, source_url: str | None = None) -> None:
    if suffix != ".zip":
        return
    if not zipfile.is_zipfile(path):
        preview = ""
        size_note = ""
        try:
            size_note = f" size={path.stat().st_size}B"
        except Exception:
            size_note = ""
        if content_type and content_type.startswith("text"):
            preview = _preview_text(path)
        elif not content_type:
            preview = _preview_text(path)
        extra = f" (content-type {content_type})" if content_type else ""
        if source_url:
            extra = f"{extra} url={source_url}"
        guidance = " The URL responded with HTML, likely a login/expired link. Download the file manually and upload it here, or provide a direct zip link."
        if preview:
            extra = f"{extra}. Preview: {preview}"
        raise ValueError(f"Invalid zip archive{extra}{size_note}.{guidance}")
