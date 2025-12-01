from __future__ import annotations

import shutil
import zipfile
from pathlib import Path


def extract_if_needed(stored_path: Path, temp_root: Path) -> Path:
    temp_root.mkdir(parents=True, exist_ok=True)
    target_dir = temp_root / stored_path.stem
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    if zipfile.is_zipfile(stored_path):
        with zipfile.ZipFile(stored_path, "r") as zf:
            zf.extractall(target_dir)
    else:
        # Non-zip uploads are placed in their own folder for scanning
        shutil.copy(stored_path, target_dir / stored_path.name)
    return target_dir
