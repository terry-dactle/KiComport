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


def save_upload(temp_file, destination_dir: Path, original_filename: str) -> Tuple[Path, str]:
    destination_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _sanitize_filename(original_filename)
    fd, tmp_path = tempfile.mkstemp(prefix="upload_", dir=destination_dir)
    os.close(fd)
    with open(tmp_path, "wb") as out_file:
        shutil.copyfileobj(temp_file, out_file)
    stored_path = Path(destination_dir) / f"{Path(tmp_path).name}_{safe_name}"
    Path(tmp_path).rename(stored_path)
    md5 = compute_md5(stored_path)
    return stored_path, md5


def _sanitize_filename(name: str) -> str:
    keep = [c for c in name if c.isalnum() or c in {"-", "_", ".", " "} ]
    sanitized = "".join(keep).strip()
    return sanitized or "upload"
