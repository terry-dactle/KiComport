from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .api import config_routes
from .api.health import healthcheck, router as health_router
from .api.jobs_routes import router as jobs_router
from .api.ollama_routes import router as ollama_router
from .api.system_routes import router as system_router
from .api.uploads_routes import router as uploads_router
from .config import load_config
from .db.models import CandidateFile, Component, Job, JobLog, JobStatus
from .db.models import Base
from .db.session import get_engine, get_session_factory
from .services import importer
from .services import cleanup as cleanup_service
from .services.kicad_paths import find_kicad_config_dir, kicad_root_hint, map_kicad_visible_path
from .services.logger import setup_logging

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "frontend" / "templates"
STATIC_DIR = Path(__file__).resolve().parents[1] / "frontend" / "static"

MODEL_EXTS = {".step", ".stp", ".wrl", ".obj"}


def _count_kicad_symbols(sym_path: Path, sample_limit: int = 8) -> tuple[int, list[str]]:
    if not sym_path.exists():
        return 0, []
    try:
        text = sym_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return 0, []

    names: list[str] = []
    depth = 0
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == "(":
            depth_before = depth
            depth += 1
            i += 1
            # Only count top-level (kicad_symbol_lib ... (symbol "...") ...) entries.
            if depth_before != 1:
                continue

            while i < len(text) and text[i].isspace():
                i += 1
            atom_start = i
            while i < len(text) and (not text[i].isspace()) and text[i] not in "()":
                i += 1
            atom = text[atom_start:i]
            if atom != "symbol":
                continue

            while i < len(text) and text[i].isspace():
                i += 1
            name = ""
            if i < len(text) and text[i] == '"':
                i += 1
                buf: list[str] = []
                esc2 = False
                while i < len(text):
                    ch2 = text[i]
                    if esc2:
                        buf.append(ch2)
                        esc2 = False
                    elif ch2 == "\\":
                        esc2 = True
                    elif ch2 == '"':
                        break
                    else:
                        buf.append(ch2)
                    i += 1
                if i < len(text) and text[i] == '"':
                    i += 1
                name = "".join(buf).strip()
            else:
                name_start = i
                while i < len(text) and (not text[i].isspace()) and text[i] not in "()":
                    i += 1
                name = text[name_start:i].strip()

            if name:
                names.append(name)
            continue

        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue

        i += 1

    sample = sorted(set(names), key=lambda s: s.casefold())[:sample_limit]
    return len(set(names)), sample


def _count_files(root: Path, *, suffixes: set[str] | None = None, pattern: str | None = None, sample_limit: int = 8) -> tuple[int, list[str]]:
    if not root.exists():
        return 0, []
    if not root.is_dir():
        return 0, []

    count = 0
    sample: list[str] = []
    try:
        if pattern is not None:
            iterator = root.rglob(pattern)
        else:
            iterator = root.rglob("*")

        for path in iterator:
            if not path.is_file():
                continue
            if suffixes is not None and path.suffix.lower() not in suffixes:
                continue
            count += 1
            if len(sample) < sample_limit:
                try:
                    sample.append(str(path.relative_to(root)))
                except Exception:
                    sample.append(path.name)
    except Exception:
        return 0, []

    sample.sort(key=lambda s: s.casefold())
    return count, sample


def create_app() -> FastAPI:
    app = FastAPI(title="Global KiCad Library Import Server", version="0.1.0", docs_url="/docs")
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    try:
        templates.env.globals["static_rev"] = int((STATIC_DIR / "styles.css").stat().st_mtime)
    except Exception:
        templates.env.globals["static_rev"] = int(time.time())
    templates.env.filters["basename"] = lambda value: Path(str(value)).name if value else ""

    @app.on_event("startup")
    async def load_app_config() -> None:
        app.state.config = load_config()
        app.state.logger = setup_logging(app.state.config.log_level, app.state.config.log_file)
        engine = get_engine(app.state.config)
        Base.metadata.create_all(engine)
        app.state.db_session_factory = get_session_factory(app.state.config)
        app.state.templates = templates
        # Housekeeping: purge old jobs and clean orphaned files.
        session = None
        try:
            session = app.state.db_session_factory()
            purged = cleanup_service.purge_expired_jobs(session, app.state.config)
            orphans = cleanup_service.cleanup_orphans(session, app.state.config)
            session.commit()
            logger = getattr(app.state, "logger", None)
            if logger:
                if purged:
                    logger.info(f"startup.cleanup purged_jobs={purged}")
                if orphans.get("removed_uploads") or orphans.get("removed_temp_dirs"):
                    logger.info(
                        "startup.cleanup orphans "
                        f"uploads={orphans.get('removed_uploads')} temp_dirs={orphans.get('removed_temp_dirs')}"
                    )
        except Exception:
            if session:
                try:
                    session.rollback()
                except Exception:
                    pass
            logger = getattr(app.state, "logger", None)
            if logger:
                logger.exception("startup.cleanup_failed")
        finally:
            if session:
                try:
                    session.close()
                except Exception:
                    pass

    app.include_router(health_router)
    app.include_router(config_routes.router)
    app.include_router(uploads_router)
    app.include_router(jobs_router)
    app.include_router(ollama_router)
    app.include_router(system_router)

    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _get_session():
        factory = getattr(app.state, "db_session_factory", None)
        if not factory:
            return None
        return factory()

    def _fetch_jobs_and_logs():
        jobs = []
        recent_logs = []
        session = _get_session()
        if not session:
            return jobs, recent_logs
        try:
            jobs = session.query(Job).order_by(Job.created_at.desc()).limit(20).all()
            recent_logs = session.query(JobLog).order_by(JobLog.created_at.desc()).limit(20).all()
        except Exception:
            logger = getattr(app.state, "logger", None)
            if logger:
                logger.exception("home.load_failed while loading dashboard data")
        finally:
            session.close()
        return jobs, recent_logs

    def _library_snapshot(cfg):
        libraries = []
        if not cfg:
            return libraries
        logger = getattr(app.state, "logger", None)
        lib_name = importer.DEFAULT_SUBFOLDER
        symbol_path = Path(cfg.kicad_symbol_dir) / f"{lib_name}.kicad_sym"
        footprint_path = Path(cfg.kicad_footprint_dir) / f"{lib_name}.pretty"
        model_path = Path(cfg.kicad_3d_dir) / lib_name
        kicad_cfg = find_kicad_config_dir(cfg)
        root_hint = kicad_root_hint(kicad_cfg)

        for label, path in [
            ("Symbols", symbol_path),
            ("Footprints", footprint_path),
            ("3D Models", model_path),
        ]:
            raw_path = str(path)
            kicad_path = map_kicad_visible_path(raw_path, root_hint)
            entry = {
                "label": label,
                "path": raw_path,
                "kicad_path": kicad_path,
                "kicad_path_exists": False,
                "path_issue": False,
                "issue_reason": None,
                "exists": False,
                "count": 0,
                "sample": [],
                "truncated": False,
            }
            kicad_root_exists = True
            if kicad_path.startswith("/config/") or kicad_path == "/config":
                kicad_root_exists = Path("/config").exists()
            elif kicad_path.startswith("/kicad/") or kicad_path == "/kicad":
                kicad_root_exists = Path("/kicad").exists()
            if kicad_root_exists:
                entry["kicad_path_exists"] = Path(kicad_path).exists()
            try:
                p = Path(path)
                entry["exists"] = p.exists()
                if root_hint == "/config" and raw_path.startswith("/kicad/"):
                    entry["path_issue"] = True
                    entry["issue_reason"] = "kicad_root_mismatch"
                if not p.exists():
                    libraries.append(entry)
                    continue

                if label == "Symbols":
                    count, sample = _count_kicad_symbols(p)
                elif label == "Footprints":
                    count, sample = _count_files(p, pattern="*.kicad_mod")
                else:
                    count, sample = _count_files(p, suffixes=MODEL_EXTS)

                entry["count"] = count
                entry["sample"] = sample
                if (
                    kicad_path
                    and kicad_path != raw_path
                    and kicad_root_exists
                    and not entry["kicad_path_exists"]
                ):
                    entry["path_issue"] = True
                    entry["issue_reason"] = "kicad_path_missing"
            except Exception:
                if logger:
                    logger.exception("home.library_snapshot_failed", extra={"label": label, "path": str(path)})
            libraries.append(entry)
        return libraries

    @app.middleware("http")
    async def add_request_id_and_log(request: Request, call_next):
        request_id = str(uuid.uuid4())
        request.state.request_id = request_id
        logger = getattr(app.state, "logger", None)
        start = time.time()
        if logger:
            logger.info(f"request.start path={request.url.path} rid={request_id}")
        response = await call_next(request)
        duration_ms = int((time.time() - start) * 1000)
        response.headers["X-Request-ID"] = request_id
        if logger:
            logger.info(f"request.end path={request.url.path} rid={request_id} status={response.status_code} dur_ms={duration_ms}")
        return response

    @app.get("/", include_in_schema=False)
    async def root():
        return RedirectResponse(url="/ui/jobs", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    def _fetch_job_detail(job_id: int) -> Job | None:
        session = _get_session()
        if not session:
            return None
        try:
            job = session.get(Job, job_id)
            if not job:
                return None
            session.refresh(job)
            for comp in job.components:
                _ = comp.candidates
            _ = job.logs
            return job
        finally:
            session.close()

    @app.get("/ui/jobs", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request, job_id: int | None = None):
        config = getattr(request.app.state, "config", None)
        jobs, recent_logs = _fetch_jobs_and_logs()
        libraries = _library_snapshot(config)
        selected_job = _fetch_job_detail(job_id) if job_id else None
        kicad_cfg = find_kicad_config_dir(config) if config else None
        root_hint = kicad_root_hint(kicad_cfg)
        def _kicad_visible_path(value: object) -> str:
            raw = str(value or "")
            return map_kicad_visible_path(raw, root_hint)

        import_paths = {
            "symbol_root": str(getattr(config, "kicad_symbol_dir", "")) if config else "",
            "footprint_root": str(getattr(config, "kicad_footprint_dir", "")) if config else "",
            "model_root": str(getattr(config, "kicad_3d_dir", "")) if config else "",
        }
        kicad_import_paths = {
            "symbol_root": _kicad_visible_path(getattr(config, "kicad_symbol_dir", "")) if config else "",
            "footprint_root": _kicad_visible_path(getattr(config, "kicad_footprint_dir", "")) if config else "",
            "model_root": _kicad_visible_path(getattr(config, "kicad_3d_dir", "")) if config else "",
        }
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": config.app_name if config else "Global KiCad Library Import Server",
                "config": config,
                "jobs": jobs,
                "recent_logs": recent_logs,
                "libraries": libraries,
                "selected_job": selected_job,
                "import_paths": import_paths,
                "kicad_import_paths": kicad_import_paths,
            },
        )

    def _list_subdirs(root: str) -> list[str]:
        try:
            base = Path(root)
            if not base.exists():
                return []
            subs = [p.name for p in base.iterdir() if p.is_dir()]
            subs.sort(key=lambda s: s.casefold())
            return subs
        except Exception:
            return []

    @app.get("/jobs/{job_id}", include_in_schema=False)
    async def job_detail(job_id: int):
        return RedirectResponse(url=f"/ui/jobs?job_id={job_id}", status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    @app.get("/api-help", response_class=HTMLResponse)
    async def api_help(request: Request):
        config = getattr(request.app.state, "config", None)
        return templates.TemplateResponse(
            "api_help.html",
            {
                "request": request,
                "app_name": config.app_name if config else "Global KiCad Library Import Server",
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        config = getattr(request.app.state, "config", None)
        return templates.TemplateResponse(
            "settings.html",
            {
                "request": request,
                "app_name": config.app_name if config else "Global KiCad Library Import Server",
                "config": config,
            },
        )

    @app.get("/ui/health", response_class=HTMLResponse, include_in_schema=False)
    async def health_page(request: Request):
        config = getattr(request.app.state, "config", None)
        try:
            health = healthcheck()
        except Exception as exc:
            health = {"status": "error", "error": str(exc)}
        return templates.TemplateResponse(
            "health.html",
            {
                "request": request,
                "app_name": config.app_name if config else "Global KiCad Library Import Server",
                "health": health,
            },
        )

    @app.get("/ui/config", response_class=HTMLResponse, include_in_schema=False)
    async def config_page(request: Request):
        config = getattr(request.app.state, "config", None)
        safe_config = config.to_safe_dict() if config else {}
        config_path = str(config.config_path) if config and config.config_path else None
        return templates.TemplateResponse(
            "config.html",
            {
                "request": request,
                "app_name": config.app_name if config else "Global KiCad Library Import Server",
                "config": config,
                "safe_config": safe_config,
                "config_path": config_path,
            },
        )

    @app.get("/ui/diagnostics", response_class=HTMLResponse, include_in_schema=False)
    async def diagnostics_page(request: Request):
        config = getattr(request.app.state, "config", None)
        safe_config = config.to_safe_dict() if config else {}
        config_path = str(config.config_path) if config and config.config_path else None
        stats = {"job_count": 0, "status_counts": {}, "component_count": 0, "candidate_count": 0}
        session = _get_session()
        if session:
            try:
                stats["job_count"] = session.query(Job).count()
                stats["component_count"] = session.query(Component).count()
                stats["candidate_count"] = session.query(CandidateFile).count()
                stats["status_counts"] = {
                    status.value: session.query(Job).filter(Job.status == status).count() for status in JobStatus
                }
            except Exception:
                logger = getattr(request.app.state, "logger", None)
                if logger:
                    logger.exception("diagnostics.load_failed")
            finally:
                session.close()
        return templates.TemplateResponse(
            "diagnostics.html",
            {
                "request": request,
                "app_name": config.app_name if config else "Global KiCad Library Import Server",
                "config": config,
                "safe_config": safe_config,
                "config_path": config_path,
                "stats": stats,
            },
        )

    return app


app = create_app()


if __name__ == "__main__":
    import os
    import uvicorn

    port = int(os.getenv("KICOMPORT_PORT", "8000"))
    uvicorn.run("v1.backend.main:app", host="0.0.0.0", port=port, reload=True)
