from __future__ import annotations

import re
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


def _kicad_visible_path(raw: str) -> str:
    if raw.startswith("/KiCad/config/"):
        return "/config/" + raw[len("/KiCad/config/") :]
    return raw


def _find_kicad_config_dir(cfg: AppConfig) -> Path | None:
    """
    Locate KiCad's config directory (the folder that contains version subfolders like `8.0/`).

    For LinuxServer KiCad this is usually:
    - `/config/.config/kicad`
    """
    candidates = [
        Path(cfg.kicad_symbol_dir),
        Path(cfg.kicad_footprint_dir),
        Path(cfg.kicad_3d_dir),
        Path.home(),
        Path("/config"),
        Path("/KiCad/config"),
    ]
    seen: set[Path] = set()
    for base in candidates:
        if not base:
            continue
        for anc in [base, *base.parents]:
            if anc in seen:
                continue
            seen.add(anc)
            kicad_dir = anc / ".config" / "kicad"
            if kicad_dir.exists() and kicad_dir.is_dir():
                return kicad_dir
    return None


def _table_atom(text: str) -> str | None:
    idx = text.find("(")
    if idx < 0:
        return None
    i = idx + 1
    while i < len(text) and text[i].isspace():
        i += 1
    start = i
    while i < len(text) and (not text[i].isspace()) and text[i] not in "()":
        i += 1
    atom = text[start:i].strip()
    return atom or None


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


def _extract_lib_blocks(text: str) -> list[str]:
    libs: list[str] = []
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
                if atom == "lib":
                    end = _find_matching_paren(text, i)
                    if end != -1:
                        libs.append(text[i : end + 1])
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
    return libs


_LIB_NAME_RE = re.compile(r'\(name\s+"([^"]+)"')


def _lib_name(lib_block: str) -> str:
    m = _LIB_NAME_RE.search(lib_block)
    return (m.group(1) if m else "").strip()


def _render_lib_block(*, name: str, uri: str, descr: str) -> str:
    safe_uri = uri.replace('"', '\\"')
    safe_name = name.replace('"', '\\"')
    safe_descr = descr.replace('"', '\\"')
    return f'  (lib (name "{safe_name}") (type "KiCad") (uri "{safe_uri}") (options "") (descr "{safe_descr}"))'


def _upsert_library_table(path: Path, *, expected_atom: str, name: str, uri: str, descr: str) -> Dict[str, Any]:
    text = ""
    if path.exists():
        text = path.read_text(encoding="utf-8", errors="ignore")
    atom = _table_atom(text) or expected_atom
    existing_libs = _extract_lib_blocks(text) if text else []

    kept: list[str] = []
    replaced = False
    for lib in existing_libs:
        if _lib_name(lib) == name:
            replaced = True
            continue
        kept.append(lib.rstrip())

    kept.append(_render_lib_block(name=name, uri=uri, descr=descr))
    new_text = f"({atom}\n" + "\n".join(kept) + "\n)\n"
    if new_text != text:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new_text, encoding="utf-8")

    return {"path": str(path), "replaced": replaced, "table": atom}


@router.post("/api/system/install-kicad-library-tables")
def install_kicad_library_tables(request: Request) -> Dict[str, Any]:
    """
    One-time helper: add/update KiComport libraries in KiCad's global library tables.

    Updates:
    - `sym-lib-table` with a `KiComport` entry pointing at `~KiComport.kicad_sym`
    - `fp-lib-table` with a `KiComport` entry pointing at `~KiComport.pretty`
    """
    cfg = get_config(request)
    repair = repair_kicad_library(request)

    lib_name = "KiComport"
    lib_folder = importer.DEFAULT_SUBFOLDER
    sym_uri = _kicad_visible_path(str(Path(cfg.kicad_symbol_dir) / f"{lib_folder}.kicad_sym"))
    fp_uri = _kicad_visible_path(str(Path(cfg.kicad_footprint_dir) / f"{lib_folder}.pretty"))
    descr = "KiComport imports"

    kicad_cfg = _find_kicad_config_dir(cfg)
    if not kicad_cfg:
        raise HTTPException(status_code=400, detail="KiCad config directory not found (expected something like /config/.config/kicad). Open KiCad once, then retry.")

    sym_tables = sorted({p for p in kicad_cfg.rglob("sym-lib-table") if p.is_file()})
    fp_tables = sorted({p for p in kicad_cfg.rglob("fp-lib-table") if p.is_file()})

    if not sym_tables and not fp_tables:
        raise HTTPException(status_code=400, detail=f"No KiCad library tables found under {kicad_cfg}. Open KiCad once, then retry.")

    updated: Dict[str, Any] = {"sym-lib-table": [], "fp-lib-table": []}
    for table in sym_tables:
        updated["sym-lib-table"].append(
            _upsert_library_table(table, expected_atom="sym_lib_table", name=lib_name, uri=sym_uri, descr=descr)
        )
    for table in fp_tables:
        updated["fp-lib-table"].append(
            _upsert_library_table(table, expected_atom="fp_lib_table", name=lib_name, uri=fp_uri, descr=descr)
        )

    return {
        "repair": repair,
        "kicad_config_dir": str(kicad_cfg),
        "symbol_uri": sym_uri,
        "footprint_uri": fp_uri,
        "updated": updated,
    }
