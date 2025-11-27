from __future__ import annotations

import asyncio
import logging
import json
import urllib.error
import urllib.request
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

        candidates = payload.get("candidates") or []
        prompt = (
            "You are ranking KiCad import candidates (symbols, footprints, models). "
            "Return concise JSON with keys 'top_candidate' (path string) and 'notes' "
            "explaining why, based on score and kind."
        )
        request_body = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": f"Candidates: {json.dumps(candidates)[:4000]}",
                },
            ],
            "stream": False,
        }

        url = f"{self.config.base_url.rstrip('/')}/api/chat"
        attempts = 2
        for attempt in range(attempts):
            try:
                req = urllib.request.Request(
                    url,
                    data=json.dumps(request_body).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.config.timeout_seconds) as response:
                    data = response.read().decode("utf-8")
                parsed = json.loads(data)
                content = (
                    parsed.get("message", {}).get("content")
                    or parsed.get("response")
                    or ""
                )
                if content:
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        return {"notes": content.strip()}
            except (urllib.error.URLError, TimeoutError, ValueError) as exc:  # pragma: no cover - network
                logger.warning("Ollama ranking failed (attempt %s/%s): %s", attempt + 1, attempts, exc)
            except Exception as exc:  # pragma: no cover - unexpected
                logger.error("Unexpected Ollama error: %s", exc)
                break

        return self._local_fallback(candidates)

    async def rank_candidates_async(self, payload: dict) -> Optional[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.rank_candidates, payload)

    @staticmethod
    def _local_fallback(candidates: list) -> Optional[dict]:
        if not candidates:
            return None
        best = max(candidates, key=lambda cand: cand.get("score") or 0.0)
        return {
            "top_candidate": best.get("path"),
            "top_kind": best.get("kind"),
            "notes": "Local score fallback used; Ollama unavailable or returned no content.",
        }
