from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path
from typing import Tuple


def manual_cleanup(upload_dir: Path, temp_dir: Path, older_than_days: int) -> Tuple[int, int]:
    """Delete files/dirs older than threshold. Returns (uploads_removed, temp_removed)."""
    cutoff = datetime.utcnow() - timedelta(days=older_than_days)
    uploads_removed = _purge_dir(upload_dir, cutoff)
    temp_removed = _purge_dir(temp_dir, cutoff)
    return uploads_removed, temp_removed


def _purge_dir(root: Path, cutoff: datetime) -> int:
    if not root.exists():
        return 0
    removed = 0
    for entry in root.iterdir():
        mtime = datetime.utcfromtimestamp(entry.stat().st_mtime)
        if mtime < cutoff:
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink(missing_ok=True)
            removed += 1
    return removed
