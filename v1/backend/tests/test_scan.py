from pathlib import Path

from v1.backend.services import scan


def test_scan_candidates_detects_symbol_footprint_and_model(tmp_path: Path):
    sym = tmp_path / "part.kicad_sym"
    sym.write_text('(symbol "part" (description "Test symbol") (pin 1))')

    pretty = tmp_path / "Foo.pretty"
    pretty.mkdir()
    fp = pretty / "foot.kicad_mod"
    fp.write_text('(module Foot (descr "footprint") (pad 1 thru_hole circle))')

    shapes = tmp_path / "foo.3dshapes"
    shapes.mkdir()
    step = shapes / "model.step"
    step.write_text("solid")

    candidates = scan.scan_candidates(tmp_path)
    types = [c.type.value for c in candidates]
    assert "symbol" in types
    assert "footprint" in types
    assert "model" in types


def test_scan_symbol_metadata_extracts_part_name(tmp_path: Path):
    sym = tmp_path / "part.kicad_sym"
    sym.write_text('(symbol "SOP65P640X120-29N" (property "MP" "LT8390AEFE_PBF") (property "Value" "LT8390AEFE"))')
    candidates = scan.scan_candidates(tmp_path)
    symbol = next(c for c in candidates if c.type.value == "symbol")
    assert symbol.metadata
    assert symbol.metadata.get("part_name") == "LT8390AEFE_PBF"
    assert symbol.metadata.get("symbol_name") == "SOP65P640X120-29N"
