from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Iterable


def extract_if_needed(stored_path: Path, temp_root: Path) -> Path:
    temp_root.mkdir(parents=True, exist_ok=True)
    target_dir = temp_root / stored_path.stem
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(stored_path):
        _safe_extract_zip(stored_path, target_dir)
    else:
        # Non-zip uploads are placed in their own folder for scanning
        shutil.copy(stored_path, target_dir / stored_path.name)
    return target_dir


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> None:
    """Extract a zip while preventing path traversal."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        members = _safe_members(zf.namelist(), target_dir)
        if not members:
            raise ValueError("Archive contains no files")
        for member in members:
            member_str = member.as_posix()
            dest = target_dir / member
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member_str, "r") as src, open(dest, "wb") as dst:
                shutil.copyfileobj(src, dst)


def _safe_members(names: Iterable[str], target_dir: Path) -> list[Path]:
    cleaned: list[Path] = []
    for name in names:
        # drop directory entries
        if name.endswith("/"):
            continue
        normalized = Path(name).as_posix().lstrip("/")
        candidate = Path(normalized)
        resolved = (target_dir / candidate).resolve()
        if not str(resolved).startswith(str(target_dir.resolve())):
            raise ValueError(f"Unsafe path in archive: {name}")
        cleaned.append(candidate)
    return cleaned
