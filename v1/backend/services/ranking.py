from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional

from ..db.models import CandidateFile, CandidateType, Component


HIGH_TRUST_SEGMENTS = {"kicad", "library", "libs", "official", "vendor", "verified", "prod", "production"}
LOW_TRUST_SEGMENTS = {"temp", "tmp", "old", "backup", "legacy", "imported", "converted", "test"}


def path_trust_bonus(path: Path | None) -> float:
    if not path:
        return 0.0
    parts = {p.lower() for p in path.parts}
    bonus = 0.0
    if parts & HIGH_TRUST_SEGMENTS:
        bonus += 0.05
    if parts & LOW_TRUST_SEGMENTS:
        bonus -= 0.05
    return bonus


def looks_like_part_number(name: str) -> bool:
    return bool(re.match(r"^[a-zA-Z]{1,5}\d{2,}[a-zA-Z0-9-]*$", name))


def quality_score_for_candidate(cf: CandidateFile) -> float:
    score = 0.0
    size = cf.metadata_json.get("size", 0) if isinstance(cf.metadata_json, dict) else 0
    if size:
        # Favor files with some substance; cap bonus
        score += min(0.05, size / 1_000_000 * 0.01)
    if cf.description:
        score += 0.05
    if cf.type == CandidateType.symbol:
        if cf.pin_count and cf.pin_count > 0:
            score += 0.05
    elif cf.type == CandidateType.footprint:
        if cf.pad_count and cf.pad_count > 0:
            score += 0.05
    elif cf.type == CandidateType.model:
        if size > 0:
            score += 0.05
        if Path(cf.path).suffix.lower() in {".step", ".stp"}:
            score += 0.05
    score += path_trust_bonus(Path(cf.path))
    return round(min(max(score, 0.0), 0.3), 3)


def consistency_adjustment(component: Component) -> None:
    symbols = [c for c in component.candidates if c.type == CandidateType.symbol]
    footprints = [c for c in component.candidates if c.type == CandidateType.footprint]
    if not symbols or not footprints:
        return
    symbol_pins = [s.pin_count for s in symbols if s.pin_count]
    for fp in footprints:
        if fp.pad_count and symbol_pins:
            best_diff = min(abs(fp.pad_count - pins) for pins in symbol_pins)
            if best_diff <= 1:
                fp.combined_score = min(1.0, fp.combined_score + 0.1)
            elif best_diff >= 4:
                fp.combined_score = max(0.0, fp.combined_score - 0.05)


def apply_feedback(candidate: CandidateFile) -> None:
    candidate.feedback_score = min(0.2, candidate.selected_count * 0.02)
    candidate.combined_score = calc_combined(candidate)


def calc_combined(cf: CandidateFile) -> float:
    h = cf.heuristic_score or 0.0
    a = cf.ai_score or 0.0
    q = cf.quality_score or 0.0
    f = cf.feedback_score or 0.0
    score = (h * 0.6) + (a * 0.3) + q + f
    return round(min(max(score, 0.0), 1.0), 3)


def update_combined_for_candidates(candidates: Iterable[CandidateFile]) -> None:
    for c in candidates:
        c.combined_score = calc_combined(c)
