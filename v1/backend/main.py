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
from .services.logger import setup_logging

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "frontend" / "templates"
STATIC_DIR = Path(__file__).resolve().parents[1] / "frontend" / "static"


def create_app() -> FastAPI:
    app = FastAPI(title="Global KiCad Library Import Server", version="0.1.0", docs_url="/docs")
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    @app.on_event("startup")
    async def load_app_config() -> None:
        app.state.config = load_config()
        app.state.logger = setup_logging(app.state.config.log_level, app.state.config.log_file)
        engine = get_engine(app.state.config)
        Base.metadata.create_all(engine)
        app.state.db_session_factory = get_session_factory(app.state.config)
        app.state.templates = templates

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
        max_scan = 500
        for label, path in [
            ("Symbols", cfg.kicad_symbol_dir),
            ("Footprints", cfg.kicad_footprint_dir),
            ("3D Models", cfg.kicad_3d_dir),
        ]:
            entry = {"label": label, "path": str(path), "exists": False, "count": 0, "sample": [], "truncated": False}
            try:
                p = Path(path)
                entry["exists"] = p.exists()
                if p.exists():
                    sample: list[str] = []
                    count = 0
                    for count, child in enumerate(p.iterdir(), start=1):
                        if len(sample) < 8:
                            sample.append(child.name)
                        if count >= max_scan:
                            entry["truncated"] = True
                            break
                    entry["count"] = count
                    entry["sample"] = sorted(sample)
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

    @app.get("/ui/jobs", response_class=HTMLResponse, include_in_schema=False)
    async def index(request: Request):
        config = getattr(request.app.state, "config", None)
        jobs, recent_logs = _fetch_jobs_and_logs()
        libraries = _library_snapshot(config)
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "app_name": config.app_name if config else "Global KiCad Library Import Server",
                "config": config,
                "jobs": jobs,
                "recent_logs": recent_logs,
                "libraries": libraries,
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

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    async def job_detail(job_id: int, request: Request):
        session = _get_session()
        if not session:
            raise HTTPException(status_code=500, detail="DB not initialized")
        try:
            job = session.get(Job, job_id)
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")
            session.refresh(job)
            # eager-load relationships
            for comp in job.components:
                _ = comp.candidates
            _ = job.logs
        finally:
            session.close()
        cfg = getattr(request.app.state, "config", None)
        import_paths = {
            "symbol_root": getattr(cfg, "kicad_symbol_dir", ""),
            "footprint_root": getattr(cfg, "kicad_footprint_dir", ""),
            "model_root": getattr(cfg, "kicad_3d_dir", ""),
            "symbol_subdirs": _list_subdirs(getattr(cfg, "kicad_symbol_dir", "")) if cfg else [],
            "footprint_subdirs": _list_subdirs(getattr(cfg, "kicad_footprint_dir", "")) if cfg else [],
            "model_subdirs": _list_subdirs(getattr(cfg, "kicad_3d_dir", "")) if cfg else [],
        }
        return templates.TemplateResponse(
            "job_detail.html",
            {
                "request": request,
                "job": job,
                "app_name": cfg.app_name if cfg else "Global KiCad Library Import Server",
                "import_paths": import_paths,
            },
        )

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
