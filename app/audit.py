from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

AUDIT_LOG = Path("data/audit.log")
AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)


def log_event(action: str, job_id: str, details: Dict[str, Any] | None = None) -> None:
    entry = {
        "event_id": str(uuid4()),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "job_id": job_id,
        "details": details or {},
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def list_events(limit: int = 200, job_id: Optional[str] = None) -> List[Dict[str, Any]]:
    if not AUDIT_LOG.exists():
        return []
    lines = AUDIT_LOG.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines if line.strip()]
    if job_id:
        events = [event for event in events if event.get("job_id") == job_id]
    return events[-limit:]


def get_event(event_id: str) -> Optional[Dict[str, Any]]:
    if not AUDIT_LOG.exists():
        return None
    with AUDIT_LOG.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            event = json.loads(line)
            if event.get("event_id") == event_id:
                return event
    return None
