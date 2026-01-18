from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import List

from ..db.models import CandidateType


@dataclass
class CandidateData:
    type: CandidateType
    path: Path
    rel_path: Path
    name: str
    description: str
    pin_count: int | None = None
    pad_count: int | None = None
    heuristic_score: float = 0.0
    metadata: dict | None = None


SYMBOL_EXTS = {".kicad_sym"}
FOOTPRINT_EXTS = {".kicad_mod"}
MODEL_EXTS = {".step", ".stp", ".wrl", ".obj"}
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


def scan_candidates(root: Path) -> List[CandidateData]:
    candidates: List[CandidateData] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        ext = path.suffix.lower()
        if ext in SYMBOL_EXTS:
            candidates.append(_build_symbol(path, root))
        elif ext in FOOTPRINT_EXTS:
            candidates.append(_build_footprint(path, root))
        elif ext in MODEL_EXTS:
            candidates.append(_build_model(path, root))
    return candidates


def _build_symbol(path: Path, root: Path) -> CandidateData:
    text = path.read_text(errors="ignore")
    pin_count = len(re.findall(r"pin", text, flags=re.IGNORECASE))
    description = _extract_first(text, r"(?:description|descr)\s+\"([^\"]+)\"")
    part_name = None
    symbol_name = None
    symbol_props: dict[str, str] = {}
    symbol_blocks = _extract_symbol_blocks(text)
    if len(symbol_blocks) == 1:
        symbol_block = symbol_blocks[0]
        symbol_name = _symbol_block_name(symbol_block) or None
        symbol_props = _symbol_block_properties(symbol_block)
        part_name = _pick_part_name(symbol_props)
    score = _heuristic_score(name=path.stem, pin_or_pad=pin_count, description=description, path=path)
    metadata = {"size": path.stat().st_size}
    if symbol_name:
        metadata["symbol_name"] = symbol_name
    if symbol_props:
        metadata["symbol_properties"] = symbol_props
    if part_name:
        metadata["part_name"] = part_name
    return CandidateData(
        type=CandidateType.symbol,
        path=path,
        rel_path=path.relative_to(root),
        name=path.stem,
        description=description or "",
        pin_count=pin_count,
        heuristic_score=score,
        metadata=metadata,
    )


def _build_footprint(path: Path, root: Path) -> CandidateData:
    text = path.read_text(errors="ignore")
    pad_count = len(re.findall(r"\bpad\b", text, flags=re.IGNORECASE))
    description = _extract_first(text, r"\((?:descr|description)\s+\"([^\"]+)\"")
    score = _heuristic_score(name=path.stem, pin_or_pad=pad_count, description=description, path=path)
    return CandidateData(
        type=CandidateType.footprint,
        path=path,
        rel_path=path.relative_to(root),
        name=path.stem,
        description=description or "",
        pad_count=pad_count,
        heuristic_score=score,
        metadata={"size": path.stat().st_size},
    )


def _build_model(path: Path, root: Path) -> CandidateData:
    score = _model_score(path)
    return CandidateData(
        type=CandidateType.model,
        path=path,
        rel_path=path.relative_to(root),
        name=path.stem,
        description=path.suffix,
        heuristic_score=score,
        metadata={"size": path.stat().st_size},
    )


def _heuristic_score(name: str, pin_or_pad: int | None, description: str | None, path: Path | None = None) -> float:
    score = 0.4
    name_lower = name.lower()
    if pin_or_pad:
        score += min(0.2, pin_or_pad / 200)
    if any(tok in name_lower for tok in ["qfn", "tqfp", "soic", "bga", "lqfp", "tssop", "sot", "dip"]):
        score += 0.1
    if description:
        desc_lower = description.lower()
        if any(tok in desc_lower for tok in ["footprint", "symbol", "connector", "package", "soic", "qfn", "tqfp"]):
            score += 0.05
    else:
        score -= 0.1
    if _looks_like_part_number(name):
        score += 0.1
    score += _path_trust_bonus(path) if path else 0.0
    return round(min(max(score, 0.0), 1.0), 3)


def _extract_first(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    if not match:
        return None
    # description pattern might have group 2 when using (descr|description)
    return match.group(2) if match.lastindex and match.lastindex >= 2 else match.group(1)


def _path_trust_bonus(path: Path) -> float:
    if not path:
        return 0.0
    high = {"kicad", "library", "libs", "official", "vendor", "verified", "prod", "production"}
    low = {"temp", "tmp", "old", "backup", "legacy", "imported", "converted", "test"}
    parts = {p.lower() for p in path.parts}
    bonus = 0.0
    if parts & high:
        bonus += 0.05
    if parts & low:
        bonus -= 0.05
    return bonus


def _looks_like_part_number(name: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]{1,5}\d{2,}[a-zA-Z0-9-]*$", name))


def _model_score(path: Path) -> float:
    size_ok = path.stat().st_size > 0
    base = 0.3 if size_ok else 0.1
    ext = path.suffix.lower()
    if ext in {".step", ".stp"}:
        base += 0.2  # prefer STEP
    elif ext == ".wrl":
        base += 0.05
    base += _path_trust_bonus(path)
    return round(min(base, 1.0), 3)


def _extract_symbol_blocks(text: str) -> list[str]:
    stripped = text.lstrip()
    if stripped.startswith("(symbol"):
        start = text.find("(symbol", len(text) - len(stripped))
        end = _find_matching_paren(text, start)
        return [text[start : end + 1]] if end != -1 else []
    symbols: list[str] = []
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


def _symbol_block_name(symbol_block: str) -> str:
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


def _clean_property_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    if not cleaned or cleaned == "~":
        return None
    return cleaned


def _symbol_block_properties(symbol_block: str) -> dict[str, str]:
    props: dict[str, str] = {}
    for match in _SYMBOL_PROPERTY_RE.finditer(symbol_block):
        key = match.group(1).strip()
        if not key or key.lower() not in _SYMBOL_PROPERTY_KEYS_LOWER:
            continue
        value = _clean_property_value(match.group(2))
        if value:
            props[key] = value
    return props


def _pick_part_name(props: dict[str, str]) -> str | None:
    if not props:
        return None
    props_norm = {key.lower(): val for key, val in props.items()}
    for key in SYMBOL_PART_NUMBER_KEYS:
        value = props_norm.get(key.lower())
        if value:
            return value
    return props_norm.get("value")
