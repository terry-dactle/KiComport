from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..config import AppConfig
from ..db.deps import get_db
from ..db.models import Job
from ..services import importer

router = APIRouter(tags=["system"])


def get_config(request: Request) -> AppConfig:
    cfg = getattr(request.app.state, "config", None)
    if not cfg:
        raise HTTPException(status_code=500, detail="Config not loaded")
    return cfg


@router.get("/api/diagnostics")
def diagnostics(request: Request, db: Session = Depends(get_db)) -> Dict[str, Any]:
    cfg = get_config(request)
    total_jobs = db.query(Job).count()
    return {
        "app_name": cfg.app_name,
        "config": cfg.to_safe_dict(),
        "db_path": str(cfg.database_path),
        "job_count": total_jobs,
    }


@router.post("/api/system/repair-kicad-library")
def repair_kicad_library(request: Request) -> Dict[str, Any]:
    """
    Ensure the shared KiCad library paths exist for the single global library (~KiComport).

    Creates (if missing):
    - <kicad_symbol_dir>/~KiComport.kicad_sym
    - <kicad_footprint_dir>/~KiComport.pretty/
    - <kicad_3d_dir>/~KiComport/
    """
    cfg = get_config(request)
    lib = importer.DEFAULT_SUBFOLDER

    symbol_dir = Path(cfg.kicad_symbol_dir)
    footprint_dir = Path(cfg.kicad_footprint_dir)
    model_dir = Path(cfg.kicad_3d_dir)

    created: list[str] = []
    try:
        symbol_dir.mkdir(parents=True, exist_ok=True)
        footprint_dir.mkdir(parents=True, exist_ok=True)
        model_dir.mkdir(parents=True, exist_ok=True)

        sym_path = symbol_dir / f"{lib}.kicad_sym"
        if not sym_path.exists():
            sym_path.write_text(importer.SYMBOL_HEADER + "\n)", encoding="utf-8")
            created.append(str(sym_path))

        fp_path = footprint_dir / f"{lib}.pretty"
        if not fp_path.exists():
            fp_path.mkdir(parents=True, exist_ok=True)
            created.append(str(fp_path))

        model_path = model_dir / lib
        if not model_path.exists():
            model_path.mkdir(parents=True, exist_ok=True)
            created.append(str(model_path))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Repair failed: {exc}") from exc

    return {"created": created}
