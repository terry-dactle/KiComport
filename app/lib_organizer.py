from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import Dict, List, Optional

from .config import AppConfig
from .models import ImportJob, PlanCandidate

LIB_FOLDERS = {
    "symbol": "symbols",
    "footprint": "footprints",
    "model": "models",
    "archive": "archives",
}


def _sanitize_name(name: str) -> str:
    cleaned = name.replace("..", "").replace("/", "_").replace("\\", "_")
    return cleaned or "component"


def _candidate_source(candidate: PlanCandidate, fallback_path: str) -> Path:
    metadata = candidate.metadata or {}
    archive_source = metadata.get("archive_source")
    if archive_source:
        return Path(archive_source)
    source_path = metadata.get("source_path")
    if source_path:
        return Path(source_path)
    return Path(fallback_path)


def _extract_from_zip(archive_path: Path, member: str, destination: Path) -> bool:
    if not archive_path.exists() or not archive_path.is_file():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "r") as archive:
        try:
            with archive.open(member) as source, destination.open("wb") as dest:
                shutil.copyfileobj(source, dest)
        except KeyError:
            return False
    return True


def _copy_file(source: Path, destination: Path) -> bool:
    if not source.exists():
        return False
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)
    return True


def organize_job_assets(
    job: ImportJob,
    config: AppConfig,
    allowed_models: Optional[List[str]] = None,
) -> Dict[str, List[str]]:
    """Copy candidate files into /kicad/libs layout and update candidate paths.

    If allowed_models is provided, only model candidates whose path matches will be copied.
    """
    installed: Dict[str, List[str]] = {}
    libs_root = Path(config.paths.root) / "libs"
    libs_root.mkdir(parents=True, exist_ok=True)

    if not job.plan:
        return installed

    allowed_model_set = set(allowed_models) if allowed_models else None

    for candidate in job.plan.candidates:
        metadata = candidate.metadata or {}
        installed_path = metadata.get("installed_path")
        if installed_path and Path(installed_path).exists():
            installed.setdefault(candidate.kind, []).append(installed_path)
            candidate.path = installed_path
            continue

        if candidate.kind == "model" and allowed_model_set is not None and candidate.path not in allowed_model_set:
            continue

        folder = LIB_FOLDERS.get(candidate.kind, "misc")
        target_dir = libs_root / folder
        target_dir.mkdir(parents=True, exist_ok=True)

        member_name = metadata.get("archive_member")
        source = _candidate_source(candidate, job.stored_path)
        display_name = member_name or Path(metadata.get("source_path") or candidate.path).name
        destination_name = f"{job.id}_{_sanitize_name(display_name)}"
        destination = target_dir / destination_name

        copied = False
        if member_name and metadata.get("archive_source"):
            copied = _extract_from_zip(Path(metadata["archive_source"]), member_name, destination)
        elif member_name:
            copied = _extract_from_zip(Path(job.stored_path), member_name, destination)
        else:
            copied = _copy_file(source, destination)

        if not copied:
            continue

        metadata["installed_path"] = str(destination)
        candidate.metadata = metadata
        candidate.path = str(destination)
        installed.setdefault(candidate.kind, []).append(str(destination))

    return installed
