from pathlib import Path

from v1.backend.services.uploads import compute_md5


def test_compute_md5(tmp_path: Path):
    f = tmp_path / "file.txt"
    f.write_text("hello world")
    md5 = compute_md5(f)
    assert md5 == "5eb63bbbe01eeed093cb22bb8f5acdc3"
