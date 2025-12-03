from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "app_settings.yaml"
_PATH_FIELDS: Iterable[str] = (
    "uploads_dir",
    "temp_dir",
    "data_dir",
    "database_path",
    "kicad_symbol_dir",
    "kicad_footprint_dir",
    "kicad_3d_dir",
)


class AppConfig(BaseModel):
    """Runtime configuration backed by YAML/JSON on disk."""

    app_name: str = "Global KiCad Library Intake Server"
    host: str = "0.0.0.0"
    port: int = 8000
    uploads_dir: Path = Path("./uploads")
    temp_dir: Path = Path("./data/tmp")
    data_dir: Path = Path("./data")
    database_path: Path = Path("./data/app.db")
    kicad_symbol_dir: Path = Path("./data/kicad/symbols")
    kicad_footprint_dir: Path = Path("./data/kicad/footprints")
    kicad_3d_dir: Path = Path("./data/kicad/3d")
    ollama_enabled: bool = False
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5:7b"
    ollama_timeout_sec: int = 30
    ollama_max_retries: int = 2
    retention_days: int = 30
    admin_password: str = ""
    log_level: str = "INFO"
    log_file: Optional[Path] = Field(default=None, description="Optional log file path")
    log_file: Optional[Path] = Field(default=None, description="Optional log file path")
    config_path: Optional[Path] = Field(default=None, exclude=True)

    model_config = ConfigDict(extra="ignore")

    @field_validator(
        "uploads_dir",
        "temp_dir",
        "data_dir",
        "database_path",
        "kicad_symbol_dir",
        "kicad_footprint_dir",
        "kicad_3d_dir",
        "log_file",
        mode="before",
    )
    @classmethod
    def _coerce_path(cls, value: Any) -> Optional[Path]:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value))

    def to_safe_dict(self) -> Dict[str, Any]:
        """Serialize config for responses (drops secrets, stringifies paths)."""
        data = self.model_dump(exclude={"admin_password", "config_path"})
        for key, val in data.items():
            if isinstance(val, Path):
                data[key] = str(val)
        return data


class AppConfigUpdate(BaseModel):
    """Partial update payload; unset fields are ignored."""

    app_name: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    uploads_dir: Optional[Path] = None
    temp_dir: Optional[Path] = None
    data_dir: Optional[Path] = None
    database_path: Optional[Path] = None
    kicad_symbol_dir: Optional[Path] = None
    kicad_footprint_dir: Optional[Path] = None
    kicad_3d_dir: Optional[Path] = None
    ollama_enabled: Optional[bool] = None
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    ollama_timeout_sec: Optional[int] = None
    ollama_max_retries: Optional[int] = None
    retention_days: Optional[int] = None
    admin_password: Optional[str] = None
    log_level: Optional[str] = None

    model_config = ConfigDict(extra="ignore")

    @field_validator(
        "uploads_dir",
        "temp_dir",
        "data_dir",
        "database_path",
        "kicad_symbol_dir",
        "kicad_footprint_dir",
        "kicad_3d_dir",
        mode="before",
    )
    @classmethod
    def _coerce_path(cls, value: Any) -> Optional[Path]:
        if value is None or isinstance(value, Path):
            return value
        return Path(str(value))


def _resolve_path(path_value: Path, base: Path) -> Path:
    return (path_value if path_value.is_absolute() else (base / path_value)).expanduser().resolve()


def normalize_paths(config: AppConfig, base: Path) -> AppConfig:
    data = config.model_dump()
    for field_name in _PATH_FIELDS:
        path_val = data.get(field_name)
        if path_val is None:
            continue
        data[field_name] = _resolve_path(Path(path_val), base)
    if data.get("log_file"):
        data["log_file"] = _resolve_path(Path(data["log_file"]), base)
    normalized = AppConfig.model_validate(data)
    normalized.config_path = config.config_path or DEFAULT_CONFIG_PATH
    return normalized


def ensure_directories(config: AppConfig) -> None:
    paths = [
        config.uploads_dir,
        config.temp_dir,
        config.data_dir,
        config.database_path.parent,
        config.kicad_symbol_dir,
        config.kicad_footprint_dir,
        config.kicad_3d_dir,
    ]
    for path in paths:
        path.mkdir(parents=True, exist_ok=True)


def _read_config_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text()
    if path.suffix.lower() in {".json"}:
        return json.loads(text)
    # default to YAML
    loaded = yaml.safe_load(text)
    return loaded or {}


def save_config(config: AppConfig, path: Optional[Path] = None) -> None:
    target = path or config.config_path or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump(exclude={"config_path"})
    for field_name in _PATH_FIELDS:
        if field_name in data and data[field_name] is not None:
            data[field_name] = str(data[field_name])
    if data.get("log_file"):
        data["log_file"] = str(data["log_file"])
    with target.open("w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False)


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    env_path = os.getenv("KICOMPORT_CONFIG_PATH")
    target = Path(config_path or env_path or DEFAULT_CONFIG_PATH)
    raw = _read_config_file(target)
    env_port = os.getenv("KICOMPORT_PORT")
    if env_port:
        try:
            raw["port"] = int(env_port)
        except (TypeError, ValueError):
            pass
    config = AppConfig.model_validate(raw)
    config.config_path = target
    config = normalize_paths(config, target.parent)
    ensure_directories(config)
    if not target.exists():
        save_config(config, target)
    return config


def apply_update(existing: AppConfig, update: AppConfigUpdate, config_path: Optional[Path] = None) -> AppConfig:
    merged = existing.model_dump()
    merged.update({k: v for k, v in update.model_dump(exclude_unset=True).items() if v is not None})
    new_config = AppConfig.model_validate(merged)
    new_config.config_path = config_path or existing.config_path or DEFAULT_CONFIG_PATH
    new_config = normalize_paths(new_config, new_config.config_path.parent)
    ensure_directories(new_config)
    save_config(new_config, new_config.config_path)
    return new_config
