from __future__ import annotations

from fastapi import HTTPException, Request

from .config import AppConfig


def require_ui_token(request: Request, config: AppConfig) -> None:
    ui_config = config.ui
    if not ui_config.require_token:
        return
    provided = request.headers.get("X-KiComport-Token") or request.query_params.get("token")
    if not provided or provided != ui_config.token:
        raise HTTPException(status_code=401, detail="UI token required")
