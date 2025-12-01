from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..config import AppConfig
from ..services.ollama import OllamaClient

router = APIRouter(prefix="/api/ollama", tags=["ollama"])


def get_config(request: Request) -> AppConfig:
    cfg = getattr(request.app.state, "config", None)
    if not cfg:
        raise HTTPException(status_code=500, detail="Config not loaded")
    return cfg


@router.get("/test")
async def ollama_test(request: Request):
    cfg = get_config(request)
    if not cfg.ollama_enabled:
        return {"enabled": False, "message": "Ollama disabled in config"}
    client = OllamaClient(cfg.ollama_base_url, cfg.ollama_model, cfg.ollama_timeout_sec, cfg.ollama_max_retries)
    try:
        result = await client.health()
        return {"enabled": True, "ok": True, "result": result}
    except Exception as exc:
        return {"enabled": True, "ok": False, "error": str(exc)}
