from fastapi import APIRouter, Depends, HTTPException, Request

from ..config import AppConfig, AppConfigUpdate, apply_update

router = APIRouter(prefix="/api/config", tags=["config"])


def get_app_config(request: Request) -> AppConfig:
    config = getattr(request.app.state, "config", None)
    if config is None:
        raise HTTPException(status_code=500, detail="Config not loaded")
    return config


@router.get("")
def read_config(config: AppConfig = Depends(get_app_config)) -> dict:
    return config.to_safe_dict()


@router.put("")
def update_config(
    payload: AppConfigUpdate,
    request: Request,
    current: AppConfig = Depends(get_app_config),
) -> dict:
    new_config = apply_update(current, payload, config_path=current.config_path)
    request.app.state.config = new_config
    return new_config.to_safe_dict()
