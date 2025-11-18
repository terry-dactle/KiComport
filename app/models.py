from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class ImportJobStatus(str, Enum):
    uploaded = "uploaded"
    analyzed = "analyzed"
    applied = "applied"
    failed = "failed"


class ImportJob(BaseModel):
    id: str
    filename: str
    stored_path: str
    md5: str
    status: ImportJobStatus
    created_at: datetime
    backup_sym_lib_table: Optional[str] = None
    backup_fp_lib_table: Optional[str] = None
    plan: Optional["ImportPlan"] = None
    table_diffs: Optional[Dict[str, str]] = None


class PlanCandidate(BaseModel):
    path: str
    kind: Literal["symbol", "footprint", "model", "archive"]
    score: float = 0.0
    source: Literal["heuristic", "ai"] = "heuristic"
    metadata: Dict[str, str] = Field(default_factory=dict)


class ImportPlan(BaseModel):
    job_id: str
    detected_types: List[str] = Field(default_factory=list)
    quality_tags: List[str] = Field(default_factory=list)
    candidates: List[PlanCandidate] = Field(default_factory=list)
    ai_annotations: Optional[Dict[str, str]] = None
    notes: Optional[str] = None


ImportJob.model_rebuild()
