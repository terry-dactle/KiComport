from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

try:
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None

from ..db.models import CandidateFile, CandidateType, Component, Job, JobStatus
from .ranking import apply_feedback
from .jobs import log_job, update_status

DEFAULT_SUBFOLDER = "~KiComport"
SYMBOL_HEADER = "(kicad_symbol_lib (version 20211014) (generator kicomport)\n"
KNOWN_RENAME_EXTS = (".kicad_mod", ".step", ".stp", ".wrl", ".obj", ".kicad_sym")

@contextmanager
def _file_lock(lock_path: Path):
    if fcntl is None:
        yield
        return
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _atomic_copy(src: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=dest.name + ".", dir=str(dest.parent))
    try:
        os.close(fd)
        shutil.copy2(src, tmp_path)
        os.replace(tmp_path, dest)
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass


def _next_available_copy(dest: Path) -> Path:
    if not dest.exists():
        return dest
    base = dest.stem
    suffix = dest.suffix
    parent = dest.parent
    for i in range(1, 10_000):
        candidate = parent / f"{base}_copy{i}{suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Could not find available destination for {dest}")


def import_job_selection(
    db: Session,
    job: Job,
    symbol_dir: Path,
    footprint_dir: Path,
    model_dir: Path,
    subfolder: str = DEFAULT_SUBFOLDER,
    rename_to: str | None = None,
) -> Tuple[Dict[str, int], List[str]]:
    if job.status not in {JobStatus.waiting_for_import, JobStatus.waiting_for_user}:
        log_job(db, job, f"Import triggered from status {job.status.value}", level="WARNING")
    copied = {"symbols": 0, "footprints": 0, "models": 0}
    destinations: list[str] = []

    safe_sub = _safe_segment(subfolder or DEFAULT_SUBFOLDER)
    safe_rename = _safe_basename(_strip_known_ext(rename_to)) if rename_to else ""
    # Keep a single stable library at the root of each KiCad library folder.
    # - symbols: <symbol_dir>/~KiComport.kicad_sym
    # - footprints: <footprint_dir>/~KiComport.pretty/<name>.kicad_mod
    # - 3d: <model_dir>/~KiComport/<file>
    footprint_dir = footprint_dir / f"{safe_sub}.pretty"
    model_dir = model_dir / safe_sub

    for comp in job.components:
        count, dest = _copy_if_selected(
            db, comp, comp.selected_symbol_id, CandidateType.symbol, symbol_dir, rename_to=None
        )
        copied["symbols"] += count
        if dest:
            destinations.append(str(dest))

        count, dest = _copy_if_selected(
            db, comp, comp.selected_footprint_id, CandidateType.footprint, footprint_dir, rename_to=safe_rename or None
        )
        copied["footprints"] += count
        if dest:
            destinations.append(str(dest))

        count, dest = _copy_if_selected(
            db, comp, comp.selected_model_id, CandidateType.model, model_dir, rename_to=safe_rename or None
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
    db: Session,
    comp: Component,
    candidate_id: int | None,
    expected_type: CandidateType,
    target_root: Path,
    rename_to: str | None = None,
) -> Tuple[int, Optional[Path]]:
    if not candidate_id:
        return 0, None
    candidate: CandidateFile = next((c for c in comp.candidates if c.id == candidate_id), None)
    if not candidate or candidate.type != expected_type:
        log_job(db, comp.job, f"Candidate {candidate_id} missing or wrong type {expected_type.value}", level="WARNING")
        return 0, None
    src = Path(candidate.path)
    dest = _destination_for(candidate, target_root, rename_to=rename_to)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if candidate.type == CandidateType.symbol:
        lock_path = dest.with_name(dest.name + ".lock")
        with _file_lock(lock_path):
            merged = _merge_symbol_lib(src, dest)
        log_job(db, comp.job, f"Imported symbol {candidate.name} into {dest}")
        candidate.selected_count += 1
        apply_feedback(candidate)
        db.add(candidate)
        return merged, dest

    lock_path = target_root / ".kicomport.lock"
    with _file_lock(lock_path):
        dest = _next_available_copy(dest)
        _atomic_copy(src, dest)
    log_job(db, comp.job, f"Imported {expected_type.value} {candidate.name} to {dest}")
    candidate.selected_count += 1
    apply_feedback(candidate)
    db.add(candidate)
    return 1, dest


def _destination_for(candidate: CandidateFile, target_root: Path, rename_to: str | None = None) -> Path:
    rel = Path(candidate.rel_path) if candidate.rel_path else Path("")
    fallback_filename = rel.name or Path(candidate.path).name or f"{candidate.name}.kicad_mod"
    rename_clean = _safe_basename(_strip_known_ext(rename_to)) if rename_to else ""

    # Fallback when relative path is missing/empty
    if not rel.name:
        if candidate.type == CandidateType.footprint:
            if rename_clean:
                return target_root / f"{rename_clean}.kicad_mod"
            return target_root / fallback_filename
        if candidate.type == CandidateType.model:
            if rename_clean:
                ext = Path(candidate.path).suffix.lower()
                return target_root / f"{rename_clean}{ext}"
            return target_root / fallback_filename
        if candidate.type == CandidateType.symbol:
            return target_root / (DEFAULT_SUBFOLDER + ".kicad_sym")

    # For footprints flatten into the destination .pretty library folder.
    if candidate.type == CandidateType.footprint:
        if rename_clean:
            return target_root / f"{rename_clean}.kicad_mod"
        return target_root / fallback_filename
    if candidate.type == CandidateType.model:
        if rename_clean:
            ext = Path(candidate.path).suffix.lower() or rel.suffix.lower()
            return target_root / f"{rename_clean}{ext}"
        return target_root / rel
    # Preserve relative path for symbols to avoid flattening collisions
    if candidate.type == CandidateType.symbol:
        return target_root / (DEFAULT_SUBFOLDER + ".kicad_sym")
    return target_root / (candidate.name + ".kicad_sym")


def _safe_segment(name: str) -> str:
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in "-_~").strip("-_")
    return cleaned or DEFAULT_SUBFOLDER


def _safe_basename(name: str | None) -> str:
    if not name:
        return ""
    buf: list[str] = []
    for ch in str(name).strip():
        if ch.isalnum() or ch in "-_~.+":
            buf.append(ch)
        elif ch.isspace():
            buf.append("_")
    cleaned = "".join(buf).strip("-_")
    return cleaned


def _strip_known_ext(name: str | None) -> str:
    if not name:
        return ""
    txt = str(name).strip()
    lower = txt.lower()
    for ext in KNOWN_RENAME_EXTS:
        if lower.endswith(ext):
            return txt[: -len(ext)]
    return txt


def _merge_symbol_lib(src: Path, dest: Path) -> int:
    """
    Merge symbols from src library into dest library file.
    Returns count of symbols added (duplicates by name are skipped).
    """
    new_symbols = _extract_symbols(src.read_text(encoding="utf-8", errors="ignore"))
    if not dest.exists():
        content = SYMBOL_HEADER + "\n".join(new_symbols) + "\n)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest, content)
        return len(new_symbols)

    existing_text = dest.read_text(encoding="utf-8", errors="ignore")
    existing_symbols = _extract_symbols(existing_text)
    existing_names = {_symbol_name(s) for s in existing_symbols}
    added = []
    for sym in new_symbols:
        name = _symbol_name(sym)
        if name and name not in existing_names:
            added.append(sym)
            existing_names.add(name)
    merged_symbols = existing_symbols + added
    _atomic_write(dest, SYMBOL_HEADER + "\n".join(merged_symbols) + "\n)")
    return len(added)


def _extract_symbols(text: str) -> List[str]:
    symbols: List[str] = []
    depth = 0
    in_string = False
    escape = False
    i = 0
    while i < len(text):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            i += 1
            continue

        if ch == "(":
            depth_before = depth
            depth += 1
            if depth_before == 1:
                # candidate top-level entry in kicad_symbol_lib
                j = i + 1
                while j < len(text) and text[j].isspace():
                    j += 1
                atom_start = j
                while j < len(text) and (not text[j].isspace()) and text[j] not in "()":
                    j += 1
                atom = text[atom_start:j]
                if atom == "symbol":
                    end = _find_matching_paren(text, i)
                    if end != -1:
                        symbols.append(text[i : end + 1])
                        i = end + 1
                        depth = depth_before
                        continue
            i += 1
            continue

        if ch == ")":
            depth = max(0, depth - 1)
            i += 1
            continue

        i += 1
    return symbols


def _find_matching_paren(text: str, start: int) -> int:
    if start < 0 or start >= len(text) or text[start] != "(":
        return -1
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def _symbol_name(symbol_block: str) -> str:
    text = symbol_block.lstrip()
    if not text.startswith("(symbol"):
        return ""
    i = len("(symbol")
    while i < len(text) and text[i].isspace():
        i += 1
    if i >= len(text):
        return ""
    if text[i] == '"':
        i += 1
        buf: list[str] = []
        esc = False
        while i < len(text):
            ch = text[i]
            if esc:
                buf.append(ch)
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                break
            else:
                buf.append(ch)
            i += 1
        return "".join(buf).strip()
    start = i
    while i < len(text) and (not text[i].isspace()) and text[i] not in "()":
        i += 1
    return text[start:i].strip()
