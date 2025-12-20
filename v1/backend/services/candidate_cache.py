from __future__ import annotations

import shutil
from pathlib import Path

from ..config import AppConfig


_CACHE_SUBDIR = "candidate_cache"


def cache_root(cfg: AppConfig, job_id: int) -> Path:
    return Path(cfg.data_dir) / _CACHE_SUBDIR / f"job_{job_id}"


def _safe_rel_path(rel_path: str | None, fallback_name: str) -> Path:
    if rel_path:
        candidate = Path(rel_path)
        if not candidate.is_absolute() and ".." not in candidate.parts:
            return candidate
    return Path(fallback_name)


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def cache_candidate_file(
    source_path: Path,
    cache_dir: Path,
    rel_path: str | None,
    name_hint: str,
) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    if _is_within(source_path, cache_dir):
        return source_path
    rel = _safe_rel_path(rel_path, source_path.name or name_hint or "candidate")
    target = cache_dir / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copy2(source_path, target)
    return target
