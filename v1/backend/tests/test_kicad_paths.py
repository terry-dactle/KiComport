from pathlib import Path

from v1.backend.services.kicad_paths import kicad_root_hint, map_kicad_visible_path


def test_kicad_root_hint():
    assert kicad_root_hint(Path("/config/.config/kicad")) == "/config"
    assert kicad_root_hint(Path("/KiCad/config/.config/kicad")) == "/config"
    assert kicad_root_hint(Path("/kicad/.config/kicad")) is None


def test_map_kicad_visible_path():
    assert (
        map_kicad_visible_path("/KiCad/config/data/kicad/symbols")
        == "/config/data/kicad/symbols"
    )
    assert (
        map_kicad_visible_path("/kicad/data/kicad/symbols")
        == "/kicad/data/kicad/symbols"
    )
    assert (
        map_kicad_visible_path("/kicad/data/kicad/symbols", "/config")
        == "/config/data/kicad/symbols"
    )
