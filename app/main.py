from __future__ import annotations

import logging
from fastapi import FastAPI

from .config import AppConfig, load_config
from .ollama_client import OllamaClient
from .routes.audit import router as audit_router
from .routes.imports import router as imports_router
from .routes.ui import router as ui_router


logger = logging.getLogger(__name__)

app = FastAPI(title="KiComport")
app.include_router(imports_router)
app.include_router(audit_router)
app.include_router(ui_router)


@app.on_event("startup")
async def startup_event() -> None:
    config = load_config()
    app.state.config = config
    app.state.ollama_client = OllamaClient(config.ai)
    logger.info("Configuration loaded. Incoming path: %s", config.paths.incoming)


@app.get("/")
async def root() -> dict:
    config: AppConfig = getattr(app.state, "config", load_config())
    return {
        "app": "KiComport",
        "status": "ok",
        "config_loaded": config is not None,
        "incoming_path": config.paths.incoming,
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
