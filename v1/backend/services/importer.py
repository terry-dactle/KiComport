from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from ..db.models import CandidateFile, CandidateType, Component, Job, JobStatus
from .ranking import apply_feedback
from .jobs import log_job, update_status

DEFAULT_SUBFOLDER = "kicomport"
SYMBOL_HEADER = "(kicad_symbol_lib (version 20211014) (generator kicomport)\n"


def import_job_selection(
    db: Session, job: Job, symbol_dir: Path, footprint_dir: Path, model_dir: Path, subfolder: str = DEFAULT_SUBFOLDER
) -> Tuple[Dict[str, int], List[str]]:
    if job.status not in {JobStatus.waiting_for_import, JobStatus.waiting_for_user}:
        log_job(db, job, f"Import triggered from status {job.status.value}", level="WARNING")
    copied = {"symbols": 0, "footprints": 0, "models": 0}
    destinations: list[str] = []

    safe_sub = _safe_segment(subfolder or DEFAULT_SUBFOLDER)
    symbol_dir = symbol_dir / safe_sub
    footprint_dir = footprint_dir / safe_sub / f"{safe_sub}.pretty"
    model_dir = model_dir / safe_sub

    for comp in job.components:
        count, dest = _copy_if_selected(
            db, comp, comp.selected_symbol_id, CandidateType.symbol, symbol_dir
        )
        copied["symbols"] += count
        if dest:
            destinations.append(str(dest))

        count, dest = _copy_if_selected(
            db, comp, comp.selected_footprint_id, CandidateType.footprint, footprint_dir
        )
        copied["footprints"] += count
        if dest:
            destinations.append(str(dest))

        count, dest = _copy_if_selected(
            db, comp, comp.selected_model_id, CandidateType.model, model_dir
        )
        copied["models"] += count
        if dest:
            destinations.append(str(dest))

    total_copied = copied["symbols"] + copied["footprints"] + copied["models"]
    if total_copied == 0:
        log_job(db, job, "Import skipped: no selections to copy", level="WARNING")
    elif destinations:
        log_job(db, job, f"Imported files: {', '.join(destinations)}")

    update_status(db, job, JobStatus.imported if total_copied else JobStatus.waiting_for_import, "Import completed" if total_copied else "No selections to import")
    return copied, destinations


def _copy_if_selected(
    db: Session, comp: Component, candidate_id: int | None, expected_type: CandidateType, target_root: Path
) -> Tuple[int, Optional[Path]]:
    if not candidate_id:
        return 0, None
    candidate: CandidateFile = next((c for c in comp.candidates if c.id == candidate_id), None)
    if not candidate or candidate.type != expected_type:
        log_job(db, comp.job, f"Candidate {candidate_id} missing or wrong type {expected_type.value}", level="WARNING")
        return 0, None
    src = Path(candidate.path)
    dest = _destination_for(candidate, target_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if candidate.type == CandidateType.symbol:
        merged = _merge_symbol_lib(src, dest)
        log_job(db, comp.job, f"Imported symbol {candidate.name} into {dest}")
        candidate.selected_count += 1
        apply_feedback(candidate)
        db.add(candidate)
        return merged, dest

    if dest.exists():
        dest = dest.with_name(dest.stem + "_copy" + dest.suffix)
    shutil.copy(src, dest)
    log_job(db, comp.job, f"Imported {expected_type.value} {candidate.name} to {dest}")
    candidate.selected_count += 1
    apply_feedback(candidate)
    db.add(candidate)
    return 1, dest


def _destination_for(candidate: CandidateFile, target_root: Path) -> Path:
    rel = Path(candidate.rel_path) if candidate.rel_path else Path("")
    fallback_filename = rel.name or Path(candidate.path).name or f"{candidate.name}.kicad_mod"

    # Fallback when relative path is missing/empty
    if not rel.name:
        if candidate.type == CandidateType.footprint:
            return target_root / fallback_filename
        if candidate.type == CandidateType.model:
            return target_root / fallback_filename
        if candidate.type == CandidateType.symbol:
            return target_root / (DEFAULT_SUBFOLDER + ".kicad_sym")

    # For footprints flatten into the destination .pretty library folder.
    if candidate.type == CandidateType.footprint:
        return target_root / fallback_filename
    if candidate.type == CandidateType.model:
        return target_root / rel
    # Preserve relative path for symbols to avoid flattening collisions
    if candidate.type == CandidateType.symbol:
        return target_root / (DEFAULT_SUBFOLDER + ".kicad_sym")
    return target_root / (candidate.name + ".kicad_sym")


def _safe_segment(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "-_").strip("-_")
    return cleaned or DEFAULT_SUBFOLDER


def _merge_symbol_lib(src: Path, dest: Path) -> int:
    """
    Merge symbols from src library into dest library file.
    Returns count of symbols added (duplicates by name are skipped).
    """
    new_symbols = _extract_symbols(src.read_text())
    if not dest.exists():
        content = SYMBOL_HEADER + "\n".join(new_symbols) + "\n)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content)
        return len(new_symbols)

    existing_text = dest.read_text()
    existing_symbols = _extract_symbols(existing_text)
    existing_names = {_symbol_name(s) for s in existing_symbols}
    added = []
    for sym in new_symbols:
        name = _symbol_name(sym)
        if name and name not in existing_names:
            added.append(sym)
            existing_names.add(name)
    merged_symbols = existing_symbols + added
    dest.write_text(SYMBOL_HEADER + "\n".join(merged_symbols) + "\n)")
    return len(added)


def _extract_symbols(text: str) -> List[str]:
    symbols: List[str] = []
    idx = 0
    while True:
        start = text.find("(symbol ", idx)
        if start == -1:
            break
        depth = 0
        end = start
        while end < len(text):
            ch = text[end]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    symbols.append(text[start : end + 1])
                    idx = end + 1
                    break
            end += 1
        else:
            break
    return symbols


def _symbol_name(symbol_block: str) -> str:
    first_line = symbol_block.strip().splitlines()[0]
    parts = first_line.strip("()").split()
    if len(parts) >= 2 and parts[0] == "symbol":
        return parts[1]
    return ""
