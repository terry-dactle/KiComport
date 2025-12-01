from pathlib import Path

from v1.backend.config import AppConfig, normalize_paths


def test_normalize_paths_resolves_relative(tmp_path: Path):
    base = tmp_path
    cfg = AppConfig(
        uploads_dir=Path("uploads"),
        temp_dir=Path("tmp"),
        data_dir=Path("data"),
        database_path=Path("data/app.db"),
        kicad_symbol_dir=Path("kicad/symbols"),
        kicad_footprint_dir=Path("kicad/fp"),
        kicad_3d_dir=Path("kicad/3d"),
        log_file=Path("logs/app.log"),
    )
    normalized = normalize_paths(cfg, base)
    assert normalized.uploads_dir == base / "uploads"
    assert normalized.database_path == (base / "data/app.db").resolve()
    assert normalized.log_file == (base / "logs/app.log").resolve()
