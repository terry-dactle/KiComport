from pathlib import Path

from v1.backend.db.models import CandidateType
from v1.backend.services.importer import _destination_for


class DummyCandidate:
    def __init__(self, type_, rel_path, name):
        self.type = type_
        self.rel_path = rel_path
        self.name = name


def test_destination_preserves_symbol_relpath():
    target = Path("/target/symbols")
    cand = DummyCandidate(CandidateType.symbol, Path("lib/part.kicad_sym"), "part")
    dest = _destination_for(cand, target)
    assert dest == target / "lib/part.kicad_sym"


def test_destination_for_footprint():
    target = Path("/target/fps")
    cand = DummyCandidate(CandidateType.footprint, Path("Foo.pretty/foot.kicad_mod"), "foot")
    dest = _destination_for(cand, target)
    assert dest == target / "Foo.pretty/foot.kicad_mod"
