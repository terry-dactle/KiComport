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


def _normalize_base_url(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if url.endswith("/"):
        url = url.rstrip("/")
    url = url.replace("/:", ":")  # fix accidental "/:" before port
    if url.startswith("http://") or url.startswith("https://"):
        return url
    return f"http://{url}"


@router.get("/test")
async def ollama_test(
    request: Request,
    enabled: bool | None = None,
    base_url: str | None = None,
    model: str | None = None,
):
    cfg = get_config(request)
    effective_enabled = cfg.ollama_enabled if enabled is None else enabled
    target_url = _normalize_base_url(base_url or cfg.ollama_base_url)
    target_model = model or cfg.ollama_model
    if not effective_enabled and (base_url or model):
        effective_enabled = True  # allow ad-hoc test even if not yet saved
    if not effective_enabled:
        return {"enabled": False, "message": "Enable Ollama in settings to run the test"}
    client = OllamaClient(target_url, target_model, cfg.ollama_timeout_sec, cfg.ollama_max_retries)
    try:
        result = await client.health()
        return {"enabled": True, "ok": True, "result": result, "base_url": target_url, "model": target_model}
    except Exception as exc:
        return {
            "enabled": True,
            "ok": False,
            "error": str(exc),
            "base_url": target_url,
            "model": target_model,
            "hint": "Verify the base URL is reachable from the app container and Ollama is listening on this address/port. If Ollama runs on the host, try http://192.168.20.3:11434 or http://host.docker.internal:11434.",
            "debug": {"timeout": cfg.ollama_timeout_sec, "retries": cfg.ollama_max_retries},
            "requested_url": f"{target_url}/api/tags",
        }


@router.get("/models")
async def list_models(request: Request, base_url: str | None = None):
    cfg = get_config(request)
    target_url = _normalize_base_url(base_url or cfg.ollama_base_url)
    client = OllamaClient(target_url, cfg.ollama_model, cfg.ollama_timeout_sec, cfg.ollama_max_retries)
    try:
        models = await client.list_models()
        return {"models": models, "base_url": target_url}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Failed to list models: {exc}") from exc
