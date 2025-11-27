from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)
CONFIG_PATH: Optional[Path] = None


class PathsConfig(BaseModel):
    root: str = "."
    incoming: str = "./incoming"
    logs: str = "./logs"
    backup: str = "./backup"
    jobs: str = "./data/jobs"


class AIConfig(BaseModel):
    enabled: bool = False
    base_url: Optional[str] = None
    model: Optional[str] = None
    timeout_seconds: int = 8


class HeuristicsConfig(BaseModel):
    model_quality_keywords: Dict[str, List[str]] = Field(default_factory=dict)
    type_keywords: Dict[str, List[str]] = Field(default_factory=dict)


class UIConfig(BaseModel):
    require_token: bool = False
    token: Optional[str] = None


class IntegrationConfig(BaseModel):
    kicad_docker_port: int = 3000
    ollama_port: int = 11434


class AppConfig(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    heuristics: HeuristicsConfig = Field(default_factory=HeuristicsConfig)
    ui: UIConfig = Field(default_factory=UIConfig)
    integration: IntegrationConfig = Field(default_factory=IntegrationConfig)


def ensure_directories(paths: PathsConfig) -> None:
    for attr in ("root", "incoming", "logs", "backup", "jobs"):
        directory = Path(getattr(paths, attr))
        if attr == "root":
            directory.mkdir(parents=True, exist_ok=True)
            continue
        directory.mkdir(parents=True, exist_ok=True)


def _resolve_config_path() -> Path:
    config_env_path = os.getenv("KICOMPORT_CONFIG_PATH")
    return Path(config_env_path) if config_env_path else Path("dev-config.yaml")


def load_config() -> AppConfig:
    global CONFIG_PATH
    config_path = _resolve_config_path()
    CONFIG_PATH = config_path

    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        logger.info("Loaded config from %s", config_path)
    else:
        data = {}
        logger.warning("Config file %s not found. Using defaults.", config_path)

    config = AppConfig(**data)  # type: ignore[arg-type]
    # Wire integration defaults into AI base URL if none is provided.
    if not config.ai.base_url and config.integration.ollama_port:
        config.ai.base_url = f"http://localhost:{config.integration.ollama_port}"
    ensure_directories(config.paths)
    return config


def save_config(config: AppConfig) -> None:
    """Persist the current config back to disk so UI changes survive restarts."""
    global CONFIG_PATH
    config_path = CONFIG_PATH or _resolve_config_path()
    CONFIG_PATH = config_path
    config_path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(mode="python")
    with config_path.open("w", encoding="utf-8") as handle:
        yaml.safe_dump(data, handle, sort_keys=False)
    logger.info("Wrote config to %s", config_path)
