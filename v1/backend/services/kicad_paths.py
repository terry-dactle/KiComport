from __future__ import annotations

from pathlib import Path

from ..config import AppConfig


def find_kicad_config_dir(cfg: AppConfig | None) -> Path | None:
    """
    Locate KiCad's config directory (the folder that contains version subfolders like `8.0/`).

    For LinuxServer KiCad this is usually:
    - `/config/.config/kicad`
    """
    candidates: list[Path] = []
    if cfg:
        candidates.extend(
            [
                Path(cfg.kicad_symbol_dir),
                Path(cfg.kicad_footprint_dir),
                Path(cfg.kicad_3d_dir),
            ]
        )
    candidates.extend([Path.home(), Path("/config"), Path("/KiCad/config")])

    seen: set[Path] = set()
    for base in candidates:
        if base in seen:
            continue
        for anc in [base, *base.parents]:
            if anc in seen:
                continue
            seen.add(anc)
            kicad_dir = anc / ".config" / "kicad"
            if kicad_dir.exists() and kicad_dir.is_dir():
                return kicad_dir
    return None


def kicad_root_hint(kicad_config_dir: Path | None) -> str | None:
    if not kicad_config_dir:
        return None
    raw = str(kicad_config_dir)
    if raw == "/KiCad/config" or raw.startswith("/KiCad/config/"):
        return "/config"
    if raw == "/config" or raw.startswith("/config/"):
        return "/config"
    return None


def map_kicad_visible_path(raw: str, root_hint: str | None = None) -> str:
    if not raw:
        return raw
    if raw == "/KiCad/config":
        return "/config"
    if raw.startswith("/KiCad/config/"):
        return "/config/" + raw[len("/KiCad/config/") :]
    if root_hint == "/config":
        if raw == "/kicad":
            return "/config"
        if raw.startswith("/kicad/"):
            return "/config/" + raw[len("/kicad/") :]
    return raw
