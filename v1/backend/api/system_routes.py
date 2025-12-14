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


def _ensure_kicad_config_dir(cfg: AppConfig) -> Path:
    """
    Ensure KiCad's config directory exists and return it.

    If KiCad hasn't been launched yet (so the folder doesn't exist), this will create it under the
    shared config volume (typically `/config` or `/KiCad/config`).
    """
    existing = _find_kicad_config_dir(cfg)
    if existing:
        return existing

    roots: list[Path] = []
    for raw in [str(cfg.kicad_symbol_dir), str(cfg.kicad_footprint_dir), str(cfg.kicad_3d_dir)]:
        if raw.startswith("/KiCad/config/"):
            roots.append(Path("/KiCad/config"))
        elif raw.startswith("/config/"):
            roots.append(Path("/config"))

    roots.extend([Path("/config"), Path("/KiCad/config")])
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        kicad_dir = root / ".config" / "kicad"
        try:
            kicad_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            continue
        return kicad_dir

    raise HTTPException(
        status_code=400,
        detail="KiCad config directory not found and could not be created. Ensure KiComport shares the KiCad /config volume (mounted as /config or /KiCad/config).",
    )


_KICAD_VERSION_DIR_RE = re.compile(r"^\d+\.\d+$")


def _candidate_kicad_table_dirs(kicad_cfg: Path) -> list[Path]:
    """Return directories to write KiCad library tables into (versioned + fallback)."""
    version_dirs = []
    try:
        for p in kicad_cfg.iterdir():
            if p.is_dir() and _KICAD_VERSION_DIR_RE.match(p.name):
                version_dirs.append(p)
    except Exception:
        version_dirs = []

    if version_dirs:
        version_dirs.sort(key=lambda p: p.name)
        return [kicad_cfg, *version_dirs]

    # If KiCad hasn't been launched yet, pre-create common version dirs.
    return [kicad_cfg, *(kicad_cfg / v for v in ("9.0", "8.0", "7.0", "6.0"))]


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
_LIB_URI_RE = re.compile(r'\(uri\s+"([^"]+)"')


def _lib_name(lib_block: str) -> str:
    m = _LIB_NAME_RE.search(lib_block)
    return (m.group(1) if m else "").strip()


def _lib_uri(lib_block: str) -> str:
    m = _LIB_URI_RE.search(lib_block)
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


@router.get("/api/system/kicad-library-tables-status")
def kicad_library_tables_status(request: Request) -> Dict[str, Any]:
    """
    Check whether KiCad's global library tables include the KiComport entries.

    This is used by the UI to hide the "Install into KiCad" helper when not needed.
    """
    cfg = get_config(request)
    lib_name = "KiComport"
    lib_folder = importer.DEFAULT_SUBFOLDER
    expected_sym_uri = _kicad_visible_path(str(Path(cfg.kicad_symbol_dir) / f"{lib_folder}.kicad_sym"))
    expected_fp_uri = _kicad_visible_path(str(Path(cfg.kicad_footprint_dir) / f"{lib_folder}.pretty"))

    kicad_cfg = _find_kicad_config_dir(cfg)
    if not kicad_cfg:
        return {
            "installed": False,
            "reason": "kicad_config_dir_not_found",
            "expected": {"symbol_uri": expected_sym_uri, "footprint_uri": expected_fp_uri},
        }

    dirs = _candidate_kicad_table_dirs(kicad_cfg)
    sym_tables = [p for p in (d / "sym-lib-table" for d in dirs) if p.exists() and p.is_file()]
    fp_tables = [p for p in (d / "fp-lib-table" for d in dirs) if p.exists() and p.is_file()]

    sym_hits: list[dict[str, str]] = []
    fp_hits: list[dict[str, str]] = []
    for table in sym_tables:
        try:
            text = table.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for lib in _extract_lib_blocks(text):
            if _lib_name(lib) != lib_name:
                continue
            sym_hits.append({"path": str(table), "uri": _lib_uri(lib)})

    for table in fp_tables:
        try:
            text = table.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for lib in _extract_lib_blocks(text):
            if _lib_name(lib) != lib_name:
                continue
            fp_hits.append({"path": str(table), "uri": _lib_uri(lib)})

    sym_ok = any(h.get("uri") == expected_sym_uri for h in sym_hits)
    fp_ok = any(h.get("uri") == expected_fp_uri for h in fp_hits)

    return {
        "installed": bool(sym_ok and fp_ok),
        "kicad_config_dir": str(kicad_cfg),
        "expected": {"symbol_uri": expected_sym_uri, "footprint_uri": expected_fp_uri},
        "found": {"symbols": sym_hits, "footprints": fp_hits},
        "ok": {"symbols": sym_ok, "footprints": fp_ok},
    }


@router.post("/api/system/install-kicad-library-tables")
def install_kicad_library_tables(request: Request) -> Dict[str, Any]:
    """
    One-time helper: add/update KiComport libraries in KiCad's global library tables.

    Updates:
    - `sym-lib-table` with a `KiComport` entry pointing at `~KiComport.kicad_sym`
    - `fp-lib-table` with a `KiComport` entry pointing at `~KiComport.pretty`
    """
    cfg = get_config(request)
    lib_name = "KiComport"
    lib_folder = importer.DEFAULT_SUBFOLDER
    sym_uri = _kicad_visible_path(str(Path(cfg.kicad_symbol_dir) / f"{lib_folder}.kicad_sym"))
    fp_uri = _kicad_visible_path(str(Path(cfg.kicad_footprint_dir) / f"{lib_folder}.pretty"))
    descr = "KiComport imports"

    kicad_cfg: Path | None = None
    try:
        repair = repair_kicad_library(request)
        kicad_cfg = _ensure_kicad_config_dir(cfg)

        target_dirs = _candidate_kicad_table_dirs(kicad_cfg)
        sym_tables = sorted({d / "sym-lib-table" for d in target_dirs})
        fp_tables = sorted({d / "fp-lib-table" for d in target_dirs})

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
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "error": str(exc),
                "kicad_config_dir": str(kicad_cfg) if kicad_cfg else None,
                "kicad_symbol_dir": str(cfg.kicad_symbol_dir),
                "kicad_footprint_dir": str(cfg.kicad_footprint_dir),
                "kicad_3d_dir": str(cfg.kicad_3d_dir),
                "expected": {"symbol_uri": sym_uri, "footprint_uri": fp_uri},
            },
        ) from exc
