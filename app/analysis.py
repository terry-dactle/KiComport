from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Dict, List, Optional, Set

from .config import AppConfig
from .models import ImportJob, ImportPlan, PlanCandidate
from .ollama_client import OllamaClient

SYMBOL_EXTS = {".kicad_sym"}
FOOTPRINT_EXTS = {".kicad_mod"}
MODEL_EXTS = {".step", ".stp", ".wrl", ".obj"}
ARCHIVE_EXTS = {".zip"}


async def analyze_job(job: ImportJob, config: AppConfig, ollama: Optional[OllamaClient]) -> ImportPlan:
    path = Path(job.stored_path)
    heuristics = config.heuristics

    candidates: List[PlanCandidate] = []
    detected_types: Set[str] = set()
    quality_tags: Set[str] = set()

    def track_quality(name: str) -> None:
        lowered = name.lower()
        for tag, keywords in heuristics.model_quality_keywords.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                quality_tags.add(tag)

    def detect_types(name: str) -> List[str]:
        lowered = name.lower()
        matches: List[str] = []
        for type_name, keywords in heuristics.type_keywords.items():
            if any(keyword.lower() in lowered for keyword in keywords):
                matches.append(type_name)
        return matches

    def add_candidate(
        candidate_path: str,
        kind: str,
        score: float,
        name_for_detection: str,
        extra_metadata: Optional[Dict[str, str]] = None,
    ) -> None:
        types = detect_types(name_for_detection)
        if types:
            detected_types.update(types)
        metadata: Dict[str, str] = {}
        if extra_metadata:
            metadata.update(extra_metadata)
        if types:
            metadata["component_types"] = ",".join(types)
        metadata.setdefault("source_path", candidate_path)
        candidates.append(
            PlanCandidate(
                path=candidate_path,
                kind=kind,  # type: ignore[arg-type]
                score=round(score, 3),
                metadata=metadata,
            )
        )
        track_quality(name_for_detection)

    if path.is_dir():
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            kind = classify_path(child)
            if not kind:
                continue
            add_candidate(str(child), kind, 0.8, child.name)
    elif path.suffix.lower() in ARCHIVE_EXTS and zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, "r") as archive:
            for info in archive.infolist()[:200]:
                if info.is_dir():
                    continue
                kind = classify_name(info.filename)
                if not kind:
                    continue
                candidate_name = f"{path.name}:{info.filename}"
                metadata = {
                    "archive_source": str(path),
                    "archive_member": info.filename,
                }
                add_candidate(candidate_name, kind, 0.9, info.filename, metadata)
        add_candidate(str(path), "archive", 0.3, path.name)
    else:
        kind = classify_path(path)
        if kind:
            add_candidate(str(path), kind, 1.0, path.name)
        else:
            add_candidate(str(path), "archive", 0.2, path.name)

    notes = f"Detected {len(candidates)} candidate files"

    plan = ImportPlan(
        job_id=job.id,
        detected_types=sorted(detected_types),
        quality_tags=sorted(quality_tags),
        candidates=candidates,
        notes=notes,
    )

    if ollama:
        ai_payload = {
            "job_id": job.id,
            "filename": job.filename,
            "candidates": [cand.model_dump() for cand in candidates],
            "detected_types": plan.detected_types,
        }
        ai_result = await ollama.rank_candidates_async(ai_payload)
        if ai_result:
            plan.ai_annotations = {k: str(v) for k, v in ai_result.items()}

    return plan


def classify_path(path: Path) -> Optional[str]:
    return classify_name(path.name)


def classify_name(name: str) -> Optional[str]:
    lowered = name.lower()
    suffix = Path(name).suffix.lower()
    if suffix in SYMBOL_EXTS:
        return "symbol"
    if suffix in FOOTPRINT_EXTS or lowered.endswith(".kicad_mod"):
        return "footprint"
    if suffix in MODEL_EXTS:
        return "model"
    if lowered.endswith(".pretty"):
        return "footprint"
    return None
