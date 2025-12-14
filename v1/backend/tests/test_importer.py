from pathlib import Path

from v1.backend.db.models import CandidateType
from v1.backend.services.importer import _destination_for


class DummyCandidate:
    def __init__(self, type_, rel_path, name, path=""):
        self.type = type_
        self.rel_path = rel_path
        self.name = name
        self.path = path


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
