from __future__ import annotations

import os
import shutil
import zipfile
from pathlib import Path
try:
    import rarfile  # type: ignore
    HAS_RAR = True
except Exception:
    rarfile = None
    HAS_RAR = False
from typing import Iterable


MAX_EXTRACT_FILES = int(os.getenv("KICOMPORT_MAX_EXTRACT_FILES", str(20_000)))
MAX_EXTRACT_BYTES = int(os.getenv("KICOMPORT_MAX_EXTRACT_BYTES", str(2 * 1024 * 1024 * 1024)))  # 2GB
MAX_EXTRACT_FILE_BYTES = int(os.getenv("KICOMPORT_MAX_EXTRACT_FILE_BYTES", str(512 * 1024 * 1024)))  # 512MB


def extract_if_needed(
    stored_path: Path,
    temp_root: Path,
    *,
    original_filename: str | None = None,
    target_dir: Path | None = None,
) -> Path:
    temp_root.mkdir(parents=True, exist_ok=True)
    target = Path(target_dir) if target_dir else (temp_root / stored_path.stem)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    try:
        suffix = stored_path.suffix.lower()
        if suffix == ".zip" and not zipfile.is_zipfile(stored_path):
            raise ValueError("Invalid zip archive")
        if suffix == ".rar":
            if not HAS_RAR:
                raise ValueError("RAR support not available; install rarfile/unrar")
            if not rarfile.is_rarfile(stored_path):
                raise ValueError("Invalid rar archive")
            with rarfile.RarFile(stored_path) as rf:
                _safe_extract_rar(rf, target)
        elif zipfile.is_zipfile(stored_path):
            _safe_extract_zip(stored_path, target)
        else:
            # Non-zip uploads are placed in their own folder for scanning
            name = _safe_filename(original_filename) if original_filename else stored_path.name
            dest = target / name
            if dest.exists():
                dest = target / f"{dest.stem}_copy{dest.suffix}"
            shutil.copy(stored_path, dest)
        return target
    except Exception:
        shutil.rmtree(target, ignore_errors=True)
        raise


def _safe_filename(name: str | None) -> str:
    if not name:
        return "upload"
    base = Path(str(name)).name
    cleaned = "".join(ch for ch in base if ch.isalnum() or ch in {"-", "_", ".", " "}).strip()
    return cleaned or "upload"


def _enforce_extract_limits(*, file_count: int, total_bytes: int, max_file_bytes: int) -> None:
    if MAX_EXTRACT_FILES and file_count > MAX_EXTRACT_FILES:
        raise ValueError(f"Archive contains too many files ({file_count} > {MAX_EXTRACT_FILES})")
    if MAX_EXTRACT_BYTES and total_bytes > MAX_EXTRACT_BYTES:
        raise ValueError("Archive uncompressed size too large to extract")
    if MAX_EXTRACT_FILE_BYTES and max_file_bytes > MAX_EXTRACT_FILE_BYTES:
        raise ValueError("Archive contains a file too large to extract")


def _safe_zip_member(name: str, target_dir: Path) -> Path:
    normalized = Path(str(name)).as_posix().lstrip("/")
    candidate = Path(normalized)
    resolved = (target_dir / candidate).resolve()
    base = target_dir.resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise ValueError(f"Unsafe path in archive: {name}")
    return candidate


def _safe_extract_rar(rf, target_dir: Path) -> None:
    if not hasattr(rf, "infolist"):
        rf.extractall(str(target_dir))
        return

    infos = [info for info in rf.infolist() if not getattr(info, "isdir", lambda: False)()]
    if not infos:
        raise ValueError("Archive contains no files")

    total_bytes = sum(max(0, int(getattr(info, "file_size", 0) or 0)) for info in infos)
    max_file = max((int(getattr(info, "file_size", 0) or 0) for info in infos), default=0)
    _enforce_extract_limits(file_count=len(infos), total_bytes=total_bytes, max_file_bytes=max_file)

    for info in infos:
        _safe_zip_member(getattr(info, "filename", ""), target_dir)

    rf.extractall(str(target_dir))


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> None:
    """Extract a zip while preventing path traversal and limiting extraction size."""
    with zipfile.ZipFile(zip_path, "r") as zf:
        infos = [
            info
            for info in zf.infolist()
            if not getattr(info, "is_dir", lambda: False)() and not info.filename.endswith("/")
        ]
        if not infos:
            raise ValueError("Archive contains no files")

        total_bytes = sum(max(0, int(getattr(info, "file_size", 0) or 0)) for info in infos)
        max_file = max((int(getattr(info, "file_size", 0) or 0) for info in infos), default=0)
        _enforce_extract_limits(file_count=len(infos), total_bytes=total_bytes, max_file_bytes=max_file)

        written_total = 0
        for info in infos:
            member = _safe_zip_member(info.filename, target_dir)
            dest = target_dir / member
            dest.parent.mkdir(parents=True, exist_ok=True)
            written_file = 0
            with zf.open(info, "r") as src, open(dest, "wb") as dst:
                while True:
                    chunk = src.read(8192)
                    if not chunk:
                        break
                    written_file += len(chunk)
                    written_total += len(chunk)
                    if MAX_EXTRACT_FILE_BYTES and written_file > MAX_EXTRACT_FILE_BYTES:
                        raise ValueError("Archive contains a file too large to extract")
                    if MAX_EXTRACT_BYTES and written_total > MAX_EXTRACT_BYTES:
                        raise ValueError("Archive uncompressed size too large to extract")
                    dst.write(chunk)


def _safe_members(names: Iterable[str], target_dir: Path) -> list[Path]:
    cleaned: list[Path] = []
    for name in names:
        # drop directory entries
        if name.endswith("/"):
            continue
        cleaned.append(_safe_zip_member(name, target_dir))
    return cleaned
