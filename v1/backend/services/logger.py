from __future__ import annotations

import json
import logging
from logging import Logger
from pathlib import Path
from typing import Optional


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def setup_logging(log_level: str = "INFO", log_file: Optional[Path] = None) -> Logger:
    level = getattr(logging, log_level.upper(), logging.INFO)
    logger = logging.getLogger("kicad-intake")
    logger.setLevel(level)

    # Console handler with JSON for easier debugging/aggregation
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(JsonFormatter())
    logger.addHandler(console)

    if log_file:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(JsonFormatter())
        logger.addHandler(file_handler)

    # Align uvicorn logging with app level
    logging.getLogger("uvicorn").setLevel(level)
    logging.getLogger("uvicorn.error").setLevel(level)
    logging.getLogger("uvicorn.access").setLevel(level)
    return logger
