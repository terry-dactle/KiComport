from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)


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


class AppConfig(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    heuristics: HeuristicsConfig = Field(default_factory=HeuristicsConfig)
    ui: UIConfig = Field(default_factory=UIConfig)


def _ensure_directories(paths: PathsConfig) -> None:
    for attr in ("root", "incoming", "logs", "backup", "jobs"):
        directory = Path(getattr(paths, attr))
        if attr == "root":
            directory.mkdir(parents=True, exist_ok=True)
            continue
        directory.mkdir(parents=True, exist_ok=True)


def load_config() -> AppConfig:
    config_env_path = os.getenv("KICOMPORT_CONFIG_PATH")
    config_path = Path(config_env_path) if config_env_path else Path("dev-config.yaml")

    if config_path.is_file():
        with config_path.open("r", encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
        logger.info("Loaded config from %s", config_path)
    else:
        data = {}
        logger.warning("Config file %s not found. Using defaults.", config_path)

    config = AppConfig(**data)  # type: ignore[arg-type]
    _ensure_directories(config.paths)
    return config
