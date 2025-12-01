from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

import httpx

from ..db.models import CandidateFile, Component


class OllamaClient:
    def __init__(self, base_url: str, model: str, timeout: int = 30, max_retries: int = 2):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    async def score_candidates(self, job_id: int, components: List[Component]) -> Dict[int, Tuple[float, str]]:
        payload = _build_payload(job_id, components, self.model)
        url = f"{self.base_url}/api/chat"
        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    resp = await client.post(url, json=payload)
                    resp.raise_for_status()
                    data = resp.json()
                    return _parse_scores(data)
            except Exception:
                if attempt >= self.max_retries:
                    raise
        return {}

    async def health(self) -> dict:
        url = f"{self.base_url}/api/tags"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json()


def _build_payload(job_id: int, components: List[Component], model: str) -> Dict[str, Any]:
    items = []
    for comp in components:
        items.append(
            {
                "component_id": comp.id,
                "name": comp.name,
                "candidates": [
                    {
                        "id": cand.id,
                        "type": cand.type.value,
                        "name": cand.name,
                        "description": cand.description,
                        "heuristic_score": cand.heuristic_score,
                        "metadata": cand.metadata,
                    }
                    for cand in comp.candidates
                ],
            }
        )
    prompt = (
        "You are assisting with ranking KiCad library assets. "
        "Return JSON with scores and reasons: {\"scores\": [{\"id\": <cand_id>, \"ai_score\": <0-1>, \"ai_reason\": \"...\"}]}. "
        "Use heuristics: name relevance, description clarity, pad/pin counts. Keep answers concise."
    )
    return {"model": model, "messages": [{"role": "user", "content": prompt + "\n" + json.dumps(items)}], "stream": False}


def _parse_scores(response: Dict[str, Any]) -> Dict[int, Tuple[float, str]]:
    try:
        content = response.get("message", {}).get("content", "{}")
        data = json.loads(content)
        raw_scores = data.get("scores", {})
        out: Dict[int, Tuple[float, str]] = {}
        if isinstance(raw_scores, list):
            for item in raw_scores:
                cid = int(item.get("id"))
                out[cid] = (float(item.get("ai_score", 0)), item.get("ai_reason", ""))
        elif isinstance(raw_scores, dict):
            for k, v in raw_scores.items():
                out[int(k)] = (float(v), "")
        return out
    except Exception:
        return {}
