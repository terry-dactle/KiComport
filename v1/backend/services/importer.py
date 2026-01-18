from __future__ import annotations

import os
import re
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
SYMBOL_NAME_STRATEGIES = {"component", "part_number", "source_symbol_name", "footprint", "mp", "value", "properties"}
SYMBOL_DEDUPE_STRATEGIES = {"auto", "skip", "replace"}
SYMBOL_PART_NUMBER_KEYS = [
    "MP",
    "MPN",
    "Mfr_PN",
    "MFR_PN",
    "Manufacturer_Part_Number",
    "Manufacturer Part Number",
    "DigiKey_Part_Number",
    "Digi-Key_Part_Number",
    "Mouser_Part_Number",
    "Arrow_Part_Number",
    "LCSC",
    "LCSC_Part",
    "JLCPCB Part",
    "JLCPCB_Part",
]
SYMBOL_PROPERTY_KEYS = SYMBOL_PART_NUMBER_KEYS + ["Value"]
_SYMBOL_PROPERTY_KEYS_LOWER = {key.lower() for key in SYMBOL_PROPERTY_KEYS}
_SYMBOL_PROPERTY_RE = re.compile(r'\(property\s+"([^"]+)"\s+"([^"]*)"', re.IGNORECASE)

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
    prior_mode = None
    try:
        prior_mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        prior_mode = None
    fd, tmp_path = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        if prior_mode in (None, 0o600):
            target_mode = 0o664
        else:
            target_mode = prior_mode
        try:
            os.chmod(path, target_mode)
        except Exception:
            pass
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
    symbol_name_strategy: str = "component",
    symbol_dedupe_strategy: str = "auto",
) -> Tuple[Dict[str, int], List[str]]:
    if job.status not in {JobStatus.waiting_for_import, JobStatus.waiting_for_user}:
        log_job(db, job, f"Import triggered from status {job.status.value}", level="WARNING")
    copied = {"symbols": 0, "footprints": 0, "models": 0}
    destinations: list[str] = []

    safe_sub = _safe_segment(subfolder or DEFAULT_SUBFOLDER)
    safe_rename = _safe_basename(_strip_known_ext(rename_to)) if rename_to else ""
    symbol_name_strategy = _normalize_symbol_name_strategy(symbol_name_strategy)
    symbol_dedupe_strategy = _normalize_symbol_dedupe_strategy(symbol_dedupe_strategy)
    # Keep a single stable library at the root of each KiCad library folder.
    # - symbols: <symbol_dir>/~KiComport.kicad_sym
    # - footprints: <footprint_dir>/~KiComport.pretty/<name>.kicad_mod
    # - 3d: <model_dir>/~KiComport/<file>
    footprint_dir = footprint_dir / f"{safe_sub}.pretty"
    model_dir = model_dir / safe_sub

    for comp in job.components:
        count, model_dest = _copy_if_selected(
            db, comp, comp.selected_model_id, CandidateType.model, model_dir, rename_to=safe_rename or None
        )
        copied["models"] += count
        if model_dest:
            destinations.append(str(model_dest))

        count, fp_dest = _copy_if_selected(
            db,
            comp,
            comp.selected_footprint_id,
            CandidateType.footprint,
            footprint_dir,
            rename_to=safe_rename or None,
            model_dest=model_dest,
        )
        copied["footprints"] += count
        if fp_dest:
            destinations.append(str(fp_dest))

        count, sym_dest = _copy_if_selected(
            db,
            comp,
            comp.selected_symbol_id,
            CandidateType.symbol,
            symbol_dir,
            rename_to=safe_rename or None,
            symbol_name_strategy=symbol_name_strategy,
            symbol_dedupe_strategy=symbol_dedupe_strategy,
        )
        copied["symbols"] += count
        if sym_dest:
            destinations.append(str(sym_dest))

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
    model_dest: Path | None = None,
    symbol_name_strategy: str = "component",
    symbol_dedupe_strategy: str = "auto",
) -> Tuple[int, Optional[Path]]:
    if not candidate_id:
        return 0, None
    candidate: CandidateFile = next((c for c in comp.candidates if c.id == candidate_id), None)
    if not candidate or candidate.type != expected_type:
        log_job(db, comp.job, f"Candidate {candidate_id} missing or wrong type {expected_type.value}", level="WARNING")
        return 0, None
    src = Path(candidate.path)
    symbol_rename = rename_to
    symbol_hint = candidate.name
    if candidate.type == CandidateType.symbol:
        symbol_hint = _symbol_source_name(candidate) or candidate.name
        symbol_rename = _symbol_rename_for_strategy(
            symbol_name_strategy,
            comp_name=comp.name,
            candidate_name=candidate.name,
            rename_to=rename_to,
            candidate=candidate,
            src=src,
            source_symbol_hint=symbol_hint,
        )
    dest = _destination_for(candidate, target_root, rename_to=symbol_rename)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if candidate.type == CandidateType.symbol:
        lock_path = dest.with_name(dest.name + ".lock")
        with _file_lock(lock_path):
            merged = _merge_symbol_lib(
                src,
                dest,
                rename_to=symbol_rename,
                source_symbol_hint=symbol_hint,
                conflict_policy=symbol_dedupe_strategy,
            )
        log_job(
            db,
            comp.job,
            f"Imported symbol {candidate.name}{f' as {symbol_rename}' if symbol_rename else ''} into {dest}",
        )
        candidate.selected_count += 1
        apply_feedback(candidate)
        db.add(candidate)
        return merged, dest

    lock_path = target_root / ".kicomport.lock"
    with _file_lock(lock_path):
        if not rename_to:
            dest = _next_available_copy(dest)
        if candidate.type == CandidateType.footprint:
            text = src.read_text(encoding="utf-8", errors="ignore")
            model_rel = None
            if model_dest:
                try:
                    model_rel = os.path.relpath(model_dest, start=dest.parent)
                except Exception:
                    model_rel = None
            rewritten = _rewrite_footprint(text, new_name=dest.stem, model_path=model_rel)
            _atomic_write(dest, rewritten)
        else:
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


def _normalize_symbol_name_strategy(value: str | None) -> str:
    if not value:
        return "component"
    cleaned = str(value).strip().lower()
    if cleaned == "part_number":
        return "component"
    return cleaned if cleaned in SYMBOL_NAME_STRATEGIES else "component"


def _normalize_symbol_dedupe_strategy(value: str | None) -> str:
    if not value:
        return "auto"
    cleaned = str(value).strip().lower()
    return cleaned if cleaned in SYMBOL_DEDUPE_STRATEGIES else "auto"


def _clean_symbol_rename(value: str | None) -> str | None:
    cleaned = _safe_basename(_strip_known_ext(value)) if value else ""
    return cleaned or None


def _clean_property_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned or cleaned == "~":
        return None
    return cleaned


def _candidate_metadata(candidate: CandidateFile | None) -> dict:
    if not candidate or not isinstance(candidate.metadata_json, dict):
        return {}
    return candidate.metadata_json


def _candidate_part_name(candidate: CandidateFile | None) -> str | None:
    meta = _candidate_metadata(candidate)
    return _clean_property_value(meta.get("part_name"))


def _candidate_symbol_properties(candidate: CandidateFile | None) -> dict[str, str]:
    meta = _candidate_metadata(candidate)
    props = meta.get("symbol_properties")
    if not isinstance(props, dict):
        return {}
    cleaned: dict[str, str] = {}
    for key, value in props.items():
        clean_val = _clean_property_value(value)
        if clean_val:
            cleaned[str(key)] = clean_val
    return cleaned


def _symbol_source_name(candidate: CandidateFile | None) -> str | None:
    meta = _candidate_metadata(candidate)
    name = meta.get("symbol_name")
    cleaned = _clean_property_value(name)
    return cleaned or None


def _normalize_symbol_properties(props: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (props or {}).items():
        clean_val = _clean_property_value(value)
        if not clean_val:
            continue
        normalized[str(key).strip().lower()] = clean_val
    return normalized


def _pick_property_value(props_norm: dict[str, str], keys: list[str]) -> str | None:
    if not props_norm:
        return None
    for key in keys:
        value = props_norm.get(key.lower())
        if value:
            return value
    return None


def _pick_part_name_from_properties(props_norm: dict[str, str], *, include_value: bool) -> str | None:
    value = _pick_property_value(props_norm, SYMBOL_PART_NUMBER_KEYS)
    if value:
        return value
    if include_value:
        return props_norm.get("value")
    return None


def _extract_symbol_properties(symbol_block: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for match in _SYMBOL_PROPERTY_RE.finditer(symbol_block):
        key = match.group(1).strip()
        if not key or key.lower() not in _SYMBOL_PROPERTY_KEYS_LOWER:
            continue
        value = _clean_property_value(match.group(2))
        if value:
            props[key] = value
    return props


def _extract_symbol_blocks_from_text(text: str) -> list[str]:
    symbols = _extract_symbols(text)
    if symbols:
        return symbols
    stripped = text.lstrip()
    if stripped.startswith("(symbol"):
        start = text.find("(symbol", len(text) - len(stripped))
        end = _find_matching_paren(text, start)
        if end != -1:
            return [text[start : end + 1]]
    return []


def _symbol_metadata_from_source(
    src: Path | None,
    source_symbol_hint: str | None = None,
) -> tuple[dict[str, str], str | None]:
    if not src:
        return {}, None
    try:
        text = src.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return {}, None
    blocks = _extract_symbol_blocks_from_text(text)
    if not blocks:
        return {}, None
    chosen = None
    if source_symbol_hint:
        for block in blocks:
            if _symbol_name(block) == source_symbol_hint:
                chosen = block
                break
    if not chosen and len(blocks) == 1:
        chosen = blocks[0]
    if not chosen:
        return {}, None
    return _extract_symbol_properties(chosen), _symbol_name(chosen) or None


def _symbol_rename_for_strategy(
    strategy: str,
    *,
    comp_name: str,
    candidate_name: str,
    rename_to: str | None,
    candidate: CandidateFile | None = None,
    src: Path | None = None,
    source_symbol_hint: str | None = None,
) -> str | None:
    props = _candidate_symbol_properties(candidate)
    symbol_name = _symbol_source_name(candidate)
    part_name = _candidate_part_name(candidate)
    if (not props or not symbol_name or not part_name) and src:
        src_props, src_symbol_name = _symbol_metadata_from_source(src, source_symbol_hint=source_symbol_hint)
        if src_props:
            if not props:
                props = src_props
            else:
                for key, value in src_props.items():
                    if key not in props:
                        props[key] = value
        if not symbol_name:
            symbol_name = src_symbol_name
        if not part_name:
            part_name = _pick_part_name_from_properties(_normalize_symbol_properties(props), include_value=True)
    props_norm = _normalize_symbol_properties(props)
    if strategy in {"component", "part_number"}:
        return _clean_symbol_rename(part_name or comp_name)
    if strategy == "mp":
        return _clean_symbol_rename(_pick_part_name_from_properties(props_norm, include_value=False))
    if strategy == "value":
        return _clean_symbol_rename(props_norm.get("value"))
    if strategy == "properties":
        return _clean_symbol_rename(_pick_part_name_from_properties(props_norm, include_value=True) or part_name)
    if strategy == "source_symbol_name":
        return _clean_symbol_rename(symbol_name or candidate_name)
    if strategy == "footprint":
        return rename_to or None
    return rename_to or None


_FOOTPRINT_NAME_RE = re.compile(r'\(footprint\s+"([^"]+)"')
_MODULE_NAME_RE = re.compile(r"\(module\s+([^\s()]+)")
_MODEL_PATH_RE = re.compile(r'\(model\s+"([^"]+)"')


def _rewrite_footprint(text: str, *, new_name: str, model_path: str | None = None) -> str:
    """Rewrite a `.kicad_mod` footprint to match the destination name and optional 3D model path."""
    if new_name:
        m = _FOOTPRINT_NAME_RE.search(text)
        if m:
            text = text[: m.start(1)] + new_name + text[m.end(1) :]
        else:
            m = _MODULE_NAME_RE.search(text)
            if m:
                text = text[: m.start(1)] + new_name + text[m.end(1) :]

    if model_path:
        normalized = model_path.replace("\\", "/")

        def _replace_model(match: re.Match[str]) -> str:
            return match.group(0).replace(match.group(1), normalized, 1)

        text = _MODEL_PATH_RE.sub(_replace_model, text)
    return text


def _rename_symbol_block(symbol_block: str, new_name: str) -> str:
    """Rename a KiCad symbol block (top-level and nested units) to a new base name."""
    old_name = _symbol_name(symbol_block)
    if not old_name or not new_name or old_name == new_name:
        return symbol_block

    def _quoted(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == old_name:
            updated = new_name
        elif name.startswith(old_name + "_"):
            updated = new_name + name[len(old_name) :]
        else:
            updated = name
        return match.group(0).replace(name, updated, 1)

    def _bare(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == old_name:
            updated = new_name
        elif name.startswith(old_name + "_"):
            updated = new_name + name[len(old_name) :]
        else:
            updated = name
        return match.group(0).replace(name, updated, 1)

    out = re.sub(r'\(symbol\s+"([^"]+)"', _quoted, symbol_block)
    out = re.sub(r'\(symbol\s+([^\s()"]+)', _bare, out)
    return out


def _merge_symbol_lib(
    src: Path,
    dest: Path,
    *,
    rename_to: str | None = None,
    source_symbol_hint: str | None = None,
    conflict_policy: str = "auto",
) -> int:
    """
    Merge symbols from src library into dest library file.
    Returns count of symbols added (conflicts handled by policy).
    """
    source_symbols = _extract_symbols(src.read_text(encoding="utf-8", errors="ignore"))
    old_name_to_remove: str | None = None
    did_rename = False
    if rename_to:
        chosen: str | None = None
        if source_symbol_hint:
            for sym in source_symbols:
                if _symbol_name(sym) == source_symbol_hint:
                    chosen = sym
                    break
        if not chosen and len(source_symbols) == 1:
            chosen = source_symbols[0]
        if chosen:
            old_name_to_remove = _symbol_name(chosen)
            source_symbols = [_rename_symbol_block(chosen, rename_to)]
            did_rename = True
    new_symbols = source_symbols
    if not dest.exists():
        content = SYMBOL_HEADER + "\n".join(new_symbols) + "\n)"
        dest.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(dest, content)
        return len(new_symbols)

    existing_text = dest.read_text(encoding="utf-8", errors="ignore")
    existing_symbols = _extract_symbols(existing_text)
    policy = _normalize_symbol_dedupe_strategy(conflict_policy)
    if policy == "auto":
        policy = "replace" if did_rename else "skip"
    if policy == "replace":
        removed_names = set()
        if did_rename and rename_to:
            removed_names.add(rename_to)
            if old_name_to_remove and old_name_to_remove != rename_to:
                removed_names.add(old_name_to_remove)
        else:
            removed_names.update(_symbol_name(s) for s in new_symbols if _symbol_name(s))
        existing_symbols = [s for s in existing_symbols if _symbol_name(s) not in removed_names]
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
