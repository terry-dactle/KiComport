from __future__ import annotations

import shutil
from pathlib import Path
from typing import Dict, List

from sqlalchemy.orm import Session

from ..db.models import CandidateFile, CandidateType, Component, Job, JobStatus
from .ranking import apply_feedback
from .jobs import log_job, update_status


def import_job_selection(db: Session, job: Job, symbol_dir: Path, footprint_dir: Path, model_dir: Path) -> Dict[str, int]:
    if job.status not in {JobStatus.waiting_for_import, JobStatus.waiting_for_user}:
        log_job(db, job, f"Import triggered from status {job.status.value}", level="WARNING")
    copied = {"symbols": 0, "footprints": 0, "models": 0}

    for comp in job.components:
        copied["symbols"] += _copy_if_selected(
            db, comp, comp.selected_symbol_id, CandidateType.symbol, symbol_dir
        )
        copied["footprints"] += _copy_if_selected(
            db, comp, comp.selected_footprint_id, CandidateType.footprint, footprint_dir
        )
        copied["models"] += _copy_if_selected(
            db, comp, comp.selected_model_id, CandidateType.model, model_dir
        )

    update_status(db, job, JobStatus.imported, "Import completed")
    return copied


def _copy_if_selected(db: Session, comp: Component, candidate_id: int | None, expected_type: CandidateType, target_root: Path) -> int:
    if not candidate_id:
        return 0
    candidate: CandidateFile = next((c for c in comp.candidates if c.id == candidate_id), None)
    if not candidate or candidate.type != expected_type:
        log_job(db, comp.job, f"Candidate {candidate_id} missing or wrong type {expected_type.value}", level="WARNING")
        return 0
    src = Path(candidate.path)
    dest = _destination_for(candidate, target_root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        dest = dest.with_name(dest.stem + "_copy" + dest.suffix)
    shutil.copy(src, dest)
    log_job(db, comp.job, f"Imported {expected_type.value} {candidate.name} to {dest}")
    candidate.selected_count += 1
    apply_feedback(candidate)
    db.add(candidate)
    return 1


def _destination_for(candidate: CandidateFile, target_root: Path) -> Path:
    rel = Path(candidate.rel_path)
    # For footprints ensure .pretty directory preserved
    if candidate.type == CandidateType.footprint:
        if rel.suffix != ".kicad_mod":
            return target_root / rel
        pretty_dir = rel.parent if rel.parent.name.endswith(".pretty") else rel.parent.with_suffix(".pretty")
        return target_root / pretty_dir / rel.name
    if candidate.type == CandidateType.model:
        return target_root / rel
    # Preserve relative path for symbols to avoid flattening collisions
    if candidate.type == CandidateType.symbol:
        rel = Path(candidate.rel_path)
        return target_root / rel
    return target_root / (candidate.name + ".kicad_sym")
