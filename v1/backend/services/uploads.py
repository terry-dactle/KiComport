from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path
from typing import Tuple


def compute_md5(file_path: Path, chunk_size: int = 8192) -> str:
    md5 = hashlib.md5()
    with file_path.open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            md5.update(chunk)
    return md5.hexdigest()


DEFAULT_MAX_UPLOAD_BYTES = int(os.getenv("KICOMPORT_MAX_UPLOAD_BYTES", str(512 * 1024 * 1024)))  # 512MB


def save_upload(temp_file, destination_dir: Path, original_filename: str, max_bytes: int | None = None) -> Tuple[Path, str]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_name = sanitize_filename(original_filename)
    fd, tmp_path = tempfile.mkstemp(prefix="upload_", dir=destination_dir)
    limit = DEFAULT_MAX_UPLOAD_BYTES if max_bytes is None else max_bytes
    md5 = hashlib.md5()
    written = 0
    try:
        with os.fdopen(fd, "wb") as out_file:
            while True:
                chunk = temp_file.read(8192)
                if not chunk:
                    break
                written += len(chunk)
                if limit and written > limit:
                    raise ValueError("Upload too large")
                md5.update(chunk)
                out_file.write(chunk)
        stored_path = Path(destination_dir) / f"{Path(tmp_path).name}_{safe_name}"
        Path(tmp_path).rename(stored_path)
    except Exception:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return stored_path, md5.hexdigest()


def sanitize_filename(name: str) -> str:
    keep = [c for c in name if c.isalnum() or c in {"-", "_", ".", " "} ]
    sanitized = "".join(keep).strip()
    return sanitized or "upload"
