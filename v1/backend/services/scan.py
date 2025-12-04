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
MODEL_EXTS = {".step", ".stp", ".wrl", ".obj", ".3dshapes", ".dcm"}


def scan_candidates(root: Path) -> List[CandidateData]:
    candidates: List[CandidateData] = []
    for path in root.rglob("*"):
        if path.is_dir():
            if path.suffix == ".pretty":
                # include contained footprints
                for mod in path.glob("*.kicad_mod"):
                    candidates.append(_build_footprint(mod, root))
            elif path.name.endswith(".3dshapes"):
                for model in path.glob("*"):
                    if model.is_file() and model.suffix.lower() in {".step", ".stp", ".wrl", ".obj"}:
                        candidates.append(_build_model(model, root))
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
    score = _heuristic_score(name=path.stem, pin_or_pad=pin_count, description=description, path=path)
    return CandidateData(
        type=CandidateType.symbol,
        path=path,
        rel_path=path.relative_to(root),
        name=path.stem,
        description=description or "",
        pin_count=pin_count,
        heuristic_score=score,
        metadata={"size": path.stat().st_size},
    )


def _build_footprint(path: Path, root: Path) -> CandidateData:
    text = path.read_text(errors="ignore")
    pad_count = len(re.findall(r"\bpad\b", text, flags=re.IGNORECASE))
    description = _extract_first(text, r"\(descr|description)\s+\"([^\"]+)\"")
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
