from __future__ import annotations

import difflib
import shutil
from pathlib import Path
from typing import Dict, Optional

from .config import AppConfig
from .models import ImportJob, PlanCandidate

SYMBOL_HEADER = "# KiComport sym-lib-table\n(sym_lib_table)\n"
FOOTPRINT_HEADER = "# KiComport fp-lib-table\n(fp_lib_table)\n"


def _ensure_table(path: Path, header: str) -> Path:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")
    return path


def _append_entry(path: Path, snippet: str) -> str:
    before = path.read_text(encoding="utf-8")
    new_content = before.rstrip() + "\n\n" + snippet.strip() + "\n"
    path.write_text(new_content, encoding="utf-8")
    diff = "\n".join(
        difflib.unified_diff(
            before.splitlines(),
            new_content.splitlines(),
            fromfile=f"{path.name} (before)",
            tofile=f"{path.name} (after)",
            lineterm="",
        )
    )
    return diff


def _select_candidate(job: ImportJob, kind: str) -> Optional[PlanCandidate]:
    if not job.plan:
        return None
    for candidate in job.plan.candidates:
        if candidate.kind == kind:
            return candidate
    return None


def _build_entry(job: ImportJob, candidate: Optional[PlanCandidate], table_kind: str) -> Optional[str]:
    if candidate is None:
        return None
    description = f"Auto import from {job.filename}"
    uri = candidate.path
    comp_type = candidate.metadata.get("component_types") if candidate.metadata else ""
    name_suffix = comp_type.split(",", 1)[0] if comp_type else job.filename
    safe_name = name_suffix.replace(" ", "_").replace("/", "_")
    if table_kind == "symbol":
        return (
            f"# KiComport job {job.id}\n"
            f"(lib (name {safe_name}) (type KiComport) "
            f"(uri \"{uri}\") (description \"{description}\"))"
        )
    return (
        f"# KiComport job {job.id}\n"
        f"(lib (name {safe_name}) (type KiComport) "
        f"(uri \"{uri}\") (options (pcbnew_plugin KiComport)) "
        f"(description \"{description}\"))"
    )


def backup_tables(job: ImportJob, config: AppConfig, timestamp: str) -> Dict[str, str]:
    root = Path(config.paths.root)
    backups: Dict[str, str] = {}
    sym_path = _ensure_table(root / "sym-lib-table", SYMBOL_HEADER)
    fp_path = _ensure_table(root / "fp-lib-table", FOOTPRINT_HEADER)

    backup_dir = Path(config.paths.backup)
    backup_dir.mkdir(parents=True, exist_ok=True)

    sym_backup = backup_dir / f"{job.id}_sym-lib-table_{timestamp}.bak"
    fp_backup = backup_dir / f"{job.id}_fp-lib-table_{timestamp}.bak"

    shutil.copy2(sym_path, sym_backup)
    shutil.copy2(fp_path, fp_backup)

    backups["sym_lib_table"] = str(sym_backup)
    backups["fp_lib_table"] = str(fp_backup)
    return backups


def apply_to_tables(job: ImportJob, config: AppConfig) -> Dict[str, str]:
    root = Path(config.paths.root)
    table_diffs: Dict[str, str] = {}

    sym_path = _ensure_table(root / "sym-lib-table", SYMBOL_HEADER)
    fp_path = _ensure_table(root / "fp-lib-table", FOOTPRINT_HEADER)

    sym_candidate = _select_candidate(job, "symbol")
    fp_candidate = _select_candidate(job, "footprint")

    sym_entry = _build_entry(job, sym_candidate, "symbol")
    fp_entry = _build_entry(job, fp_candidate, "footprint")

    if sym_entry:
        table_diffs["sym_lib_table"] = _append_entry(sym_path, sym_entry)
    if fp_entry:
        table_diffs["fp_lib_table"] = _append_entry(fp_path, fp_entry)

    return table_diffs


def restore_from_backups(job: ImportJob, config: AppConfig) -> None:
    if job.backup_sym_lib_table or job.backup_fp_lib_table:
        restore_from_paths(job.backup_sym_lib_table, job.backup_fp_lib_table, config)


def restore_from_paths(sym_backup: Optional[str], fp_backup: Optional[str], config: AppConfig) -> None:
    root = Path(config.paths.root)
    if sym_backup:
        shutil.copy2(sym_backup, root / "sym-lib-table")
    if fp_backup:
        shutil.copy2(fp_backup, root / "fp-lib-table")
