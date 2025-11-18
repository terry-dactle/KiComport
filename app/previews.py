from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional

from .config import AppConfig
from .models import ImportJob


def _read_snippet(path: Path, max_lines: int = 40) -> Dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    if not path.is_file():
        return {"path": str(path), "exists": False, "note": "Not a regular file"}
    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": path.stat().st_size,
        "lines": lines[:max_lines],
        "truncated": len(lines) > max_lines,
    }


def preview_library_tables(config: AppConfig, job: Optional[ImportJob] = None) -> Dict[str, object]:
    root = Path(config.paths.root)
    current = {
        "sym_lib_table": _read_snippet(root / "sym-lib-table"),
        "fp_lib_table": _read_snippet(root / "fp-lib-table"),
    }

    backups = {}
    if job:
        if job.backup_sym_lib_table:
            backups["sym_lib_table"] = _read_snippet(Path(job.backup_sym_lib_table))
        if job.backup_fp_lib_table:
            backups["fp_lib_table"] = _read_snippet(Path(job.backup_fp_lib_table))

    return {"current": current, "backups": backups}
