from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from urllib.parse import urlparse, urlunparse

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


def _is_ip(host: str | None) -> bool:
    if not host:
        return False
    parts = host.split(".")
    if len(parts) != 4:
        return False
    try:
        return all(0 <= int(p) <= 255 for p in parts)
    except ValueError:
        return False


def _with_host(base_url: str, new_host: str) -> str:
    parsed = urlparse(base_url)
    if not parsed.scheme:
        return base_url
    netloc = new_host
    if parsed.port:
        netloc = f"{new_host}:{parsed.port}"
    replaced = parsed._replace(netloc=netloc)
    return urlunparse(replaced)


def _maybe_fallback_host(base_url: str) -> str | None:
    parsed = urlparse(base_url)
    host = parsed.hostname
    if not host or _is_ip(host):
        return None
    if host == "host.docker.internal":
        return None
    return _with_host(base_url, "host.docker.internal")


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
    fallback_url = _maybe_fallback_host(target_url)
    tried_fallback = False
    try:
        result = await client.health()
        return {"enabled": True, "ok": True, "result": result, "base_url": target_url, "model": target_model}
    except Exception as exc:
        if fallback_url and "Name or service not known" in str(exc):
            tried_fallback = True
            try:
                fb_client = OllamaClient(fallback_url, target_model, cfg.ollama_timeout_sec, cfg.ollama_max_retries)
                result = await fb_client.health()
                return {
                    "enabled": True,
                    "ok": True,
                    "result": result,
                    "base_url": fallback_url,
                    "model": target_model,
                    "note": f"Original host '{target_url}' was not reachable; using host.docker.internal instead.",
                }
            except Exception as fb_exc:
                exc = fb_exc
        return {
            "enabled": True,
            "ok": False,
            "error": str(exc),
            "base_url": target_url,
            "model": target_model,
            "hint": "Verify the base URL is reachable from the app container and Ollama is listening on this address/port. If Ollama runs on the host, try http://192.168.20.3:11434 or http://host.docker.internal:11434.",
            "debug": {"timeout": cfg.ollama_timeout_sec, "retries": cfg.ollama_max_retries},
            "requested_url": f"{(fallback_url if tried_fallback else target_url)}/api/tags",
        }


@router.get("/models")
async def list_models(request: Request, base_url: str | None = None):
    cfg = get_config(request)
    target_url = _normalize_base_url(base_url or cfg.ollama_base_url)
    client = OllamaClient(target_url, cfg.ollama_model, cfg.ollama_timeout_sec, cfg.ollama_max_retries)
    fallback_url = _maybe_fallback_host(target_url)
    try:
        models = await client.list_models()
        return {"models": models, "base_url": target_url}
    except Exception as exc:
        if fallback_url and "Name or service not known" in str(exc):
            try:
                fb_client = OllamaClient(fallback_url, cfg.ollama_model, cfg.ollama_timeout_sec, cfg.ollama_max_retries)
                models = await fb_client.list_models()
                return {"models": models, "base_url": fallback_url, "note": f"Original host '{target_url}' not reachable; using host.docker.internal."}
            except Exception as fb_exc:
                exc = fb_exc
        hint = ""
        if "Name or service not known" in str(exc):
            hint = " Hostname is not reachable from the container. Use an IP (e.g., http://192.x.x.x:11434) or host.docker.internal if applicable."
        raise HTTPException(
            status_code=502,
            detail=f"Failed to list models via {target_url}/api/tags: {exc}.{hint}",
        ) from exc
