from pathlib import Path

from v1.backend.db.models import CandidateType
from v1.backend.services.importer import (
    SYMBOL_HEADER,
    _destination_for,
    _extract_symbols,
    _merge_symbol_lib,
    _symbol_name,
    _symbol_rename_for_strategy,
)


class DummyCandidate:
    def __init__(self, type_, rel_path, name, path=""):
        self.type = type_
        self.rel_path = rel_path
        self.name = name
        self.path = path


def _symbol_block(name: str, marker: str) -> str:
    return f'(symbol "{name}" (property "Reference" "U") (property "KiComportMarker" "{marker}"))'


def _write_symbol_lib(path: Path, blocks: list[str]) -> None:
    content = SYMBOL_HEADER + "\n".join(blocks) + "\n)"
    path.write_text(content, encoding="utf-8")


def test_destination_for_symbol_uses_single_library_file():
    target = Path("/target/symbols")
    cand = DummyCandidate(CandidateType.symbol, Path("lib/part.kicad_sym"), "part", path="lib/part.kicad_sym")
    dest = _destination_for(cand, target)
    assert dest == target / "~KiComport.kicad_sym"


def test_destination_for_footprint():
    target = Path("/target/fps/~KiComport.pretty")
    cand = DummyCandidate(CandidateType.footprint, Path("Foo.pretty/foot.kicad_mod"), "foot", path="Foo.pretty/foot.kicad_mod")
    dest = _destination_for(cand, target)
    assert dest == target / "foot.kicad_mod"


def test_destination_for_footprint_rename_uses_base_name():
    target = Path("/target/fps/~KiComport.pretty")
    cand = DummyCandidate(CandidateType.footprint, Path("Foo.pretty/foot.kicad_mod"), "foot", path="Foo.pretty/foot.kicad_mod")
    dest = _destination_for(cand, target, rename_to="MyPart")
    assert dest == target / "MyPart.kicad_mod"


def test_destination_for_model_rename_preserves_extension():
    target = Path("/target/3d/~KiComport")
    cand = DummyCandidate(CandidateType.model, Path("OldName.step"), "OldName", path="OldName.step")
    dest = _destination_for(cand, target, rename_to="MyPart")
    assert dest == target / "MyPart.step"


def test_destination_for_model_rename_strips_known_extension_from_input():
    target = Path("/target/3d/~KiComport")
    cand = DummyCandidate(CandidateType.model, Path("OldName.step"), "OldName", path="OldName.step")
    dest = _destination_for(cand, target, rename_to="MyPart.step")
    assert dest == target / "MyPart.step"


def test_symbol_rename_strategy_component():
    rename = _symbol_rename_for_strategy(
        "component",
        comp_name="LT8390A",
        candidate_name="SYM",
        rename_to="SOP65P",
    )
    assert rename == "LT8390A"


def test_symbol_rename_strategy_part_number_alias():
    rename = _symbol_rename_for_strategy(
        "part_number",
        comp_name="LT8390A",
        candidate_name="SYM",
        rename_to="SOP65P",
    )
    assert rename == "LT8390A"


def test_merge_symbol_lib_conflict_skip_keeps_existing(tmp_path: Path):
    src = tmp_path / "src.kicad_sym"
    dest = tmp_path / "dest.kicad_sym"
    _write_symbol_lib(src, [_symbol_block("PART", "new")])
    _write_symbol_lib(dest, [_symbol_block("PART", "old")])
    added = _merge_symbol_lib(src, dest, rename_to="PART", source_symbol_hint="PART", conflict_policy="skip")
    assert added == 0
    symbols = _extract_symbols(dest.read_text(encoding="utf-8"))
    names = [_symbol_name(sym) for sym in symbols]
    assert names == ["PART"]
    assert "KiComportMarker\" \"old" in dest.read_text(encoding="utf-8")
    assert "KiComportMarker\" \"new" not in dest.read_text(encoding="utf-8")


def test_merge_symbol_lib_conflict_replace_overwrites(tmp_path: Path):
    src = tmp_path / "src.kicad_sym"
    dest = tmp_path / "dest.kicad_sym"
    _write_symbol_lib(src, [_symbol_block("PART", "new")])
    _write_symbol_lib(dest, [_symbol_block("PART", "old")])
    added = _merge_symbol_lib(src, dest, rename_to="PART", source_symbol_hint="PART", conflict_policy="replace")
    assert added == 1
    symbols = _extract_symbols(dest.read_text(encoding="utf-8"))
    names = [_symbol_name(sym) for sym in symbols]
    assert names == ["PART"]
    assert "KiComportMarker\" \"new" in dest.read_text(encoding="utf-8")
    assert "KiComportMarker\" \"old" not in dest.read_text(encoding="utf-8")
