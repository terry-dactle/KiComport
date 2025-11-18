from __future__ import annotations

import logging
from typing import Optional

from .config import AIConfig


logger = logging.getLogger(__name__)


class OllamaClient:
    def __init__(self, config: AIConfig):
        self.config = config

    def rank_candidates(self, payload: dict) -> Optional[dict]:
        if not self.config.enabled or not self.config.base_url or not self.config.model:
            logger.debug("Ollama disabled or misconfigured; skipping rank request")
            return None
        logger.info("OllamaClient.rank_candidates called with payload keys: %s", list(payload.keys()))
        return None
