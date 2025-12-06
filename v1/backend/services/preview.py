from __future__ import annotations

import base64
from pathlib import Path
from typing import Tuple

from ..db.models import CandidateFile, CandidateType


def _encode_svg(svg: str) -> str:
    data = svg.encode("utf-8")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _build_svg(text_lines: list[str], width: int = 480, height: int = 360) -> str:
    line_height = 18
    padding = 12
    svg_lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" style="background:#0f141b;color:#e9edf5;font-family:monospace;">',
        f'<rect x="0" y="0" width="{width}" height="{height}" fill="#0f141b" stroke="#243043" stroke-width="1"/>',
    ]
    y = padding + line_height
    for line in text_lines[: int((height - padding * 2) / line_height)]:
        safe_line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        svg_lines.append(f'<text x="{padding}" y="{y}" fill="#e9edf5" font-size="14">{safe_line}</text>')
        y += line_height
    svg_lines.append("</svg>")
    return "\n".join(svg_lines)


def _run_kicad_cli(cmd: list[str]) -> None:
    try:
        subprocess.check_output(cmd, stderr=subprocess.STDOUT, timeout=15)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"kicad-cli failed: {exc.output.decode(errors='ignore')}") from exc
    except FileNotFoundError as exc:
        raise RuntimeError("kicad-cli not found in PATH") from exc
    except Exception as exc:
        raise RuntimeError(f"kicad-cli failed: {exc}") from exc


def _render_symbol_or_footprint(cand: CandidateFile, kicad_cli: str = "kicad-cli") -> Tuple[str, str]:
    src = Path(cand.path)
    if not src.exists():
        raise FileNotFoundError(str(src))
    with tempfile.TemporaryDirectory() as tmpdir:
        out_svg = Path(tmpdir) / f"{cand.id}.svg"
        if cand.type == CandidateType.footprint:
            cmd = [kicad_cli, "pcb", "export", "svg", "--footprint", str(src), "-o", str(out_svg)]
        else:
            cmd = [kicad_cli, "sch", "export", "svg", "--symbol", str(src), "-o", str(out_svg)]
        _run_kicad_cli(cmd)
        if not out_svg.exists():
            raise RuntimeError("kicad-cli did not produce an output file")
        data = out_svg.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        note = "Rendered via kicad-cli."
        return f"data:image/svg+xml;base64,{b64}", note


def _render_3d(cand: CandidateFile) -> Tuple[str, str]:
    try:
        import trimesh  # type: ignore
        import pyrender  # type: ignore
    except Exception as exc:
        raise RuntimeError("3D rendering libs (trimesh/pyrender) not installed") from exc

    src = Path(cand.path)
    if not src.exists():
        raise FileNotFoundError(str(src))
    try:
        mesh = trimesh.load(src, force="mesh")
        scene = pyrender.Scene()
        scene.add(pyrender.Mesh.from_trimesh(mesh))
        camera = pyrender.PerspectiveCamera(yfov=1.0)
        light = pyrender.DirectionalLight(color=[1.0, 1.0, 1.0], intensity=2.0)
        scene.add(light)
        scene.add(camera, pose=[[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 2], [0, 0, 0, 1]])
        r = pyrender.OffscreenRenderer(viewport_width=800, viewport_height=600)
        color, _ = r.render(scene)
        r.delete()
        import PIL.Image  # type: ignore

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            PIL.Image.fromarray(color).save(tmp.name)
            data = Path(tmp.name).read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        note = "Rendered via trimesh/pyrender."
        return f"data:image/png;base64,{b64}", note
    except Exception as exc:
        raise RuntimeError(f"3D render failed: {exc}") from exc


def render_candidate_preview(cand: CandidateFile) -> Tuple[str, str]:
    """
    Return a data URL plus a note. Prefers real renders when tools are available,
    otherwise falls back to a text-based SVG.
    """
    path = Path(cand.path)
    if not path.exists():
        raise FileNotFoundError(str(path))
    if cand.type in {CandidateType.symbol, CandidateType.footprint}:
        try:
            return _render_symbol_or_footprint(cand)
        except Exception as exc:
            fallback_note = f"Rendering via kicad-cli failed: {exc}. Trying lightweight parser."
            try:
                if cand.type == CandidateType.footprint:
                    return _render_footprint_svg(path), "Rendered via lightweight footprint parser."
                else:
                    return _render_symbol_svg(path), "Rendered via lightweight symbol parser."
            except Exception as exc2:
                fallback_note = f"Render parsers failed: {exc2}. Showing text preview."
    elif cand.type == CandidateType.model:
        try:
            return _render_3d(cand)
        except Exception as exc:
            fallback_note = f"3D rendering failed: {exc}. Showing text preview."
    else:
        fallback_note = "Showing text preview."

    # Fallback to text preview
    lines = [
        f"{cand.type.value.upper()} PREVIEW",
        f"Name: {cand.name}",
        f"File: {path.name}",
        f"Path: {path}",
        "",
    ]
    try:
        text = path.read_text(errors="ignore")
        snippet = text.splitlines()[:40]
        lines.extend(snippet)
    except Exception:
        lines.append("Content unavailable.")
    svg = _build_svg(lines)
    return _encode_svg(svg), fallback_note
import shutil
import subprocess
import tempfile
def _render_footprint_svg(path: Path) -> str:
    import math
    text = path.read_text(errors="ignore").splitlines()
    pads = []
    for line in text:
        line = line.strip()
        if line.startswith("(pad "):
            # crude parse: pad "num" type shape (at x y rot?) (size sx sy)
            try:
                parts = line.replace("(", " ").replace(")", " ").split()
                idx_at = parts.index("at") + 1
                x = float(parts[idx_at])
                y = float(parts[idx_at + 1])
                rot = float(parts[idx_at + 2]) if parts[idx_at + 2].replace(".", "", 1).lstrip("-").isdigit() else 0.0
                idx_size = parts.index("size") + 1
                sx = float(parts[idx_size])
                sy = float(parts[idx_size + 1])
                pads.append((x, y, sx, sy, rot))
            except Exception:
                continue
    if not pads:
        raise RuntimeError("No pads parsed")
    xs = []
    ys = []
    for x, y, sx, sy, _ in pads:
        xs.extend([x - sx / 2, x + sx / 2])
        ys.extend([y - sy / 2, y + sy / 2])
    minx, maxx = min(xs) - 1, max(xs) + 1
    miny, maxy = min(ys) - 1, max(ys) + 1
    width = maxx - minx
    height = maxy - miny
    # scale to viewBox ~ 400x400
    scale = 400.0 / max(width, height)
    def tx(x): return (x - minx) * scale
    def ty(y): return (maxy - y) * scale  # flip y
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="420" viewBox="0 0 420 420" style="background:#0f141b;">']
    svg.append('<rect x="0" y="0" width="420" height="420" fill="#0f141b" stroke="#243043" />')
    for x, y, sx, sy, rot in pads:
        cx = tx(x)
        cy = ty(y)
        rw = sx * scale
        rh = sy * scale
        svg.append(f'<g transform="translate({cx},{cy}) rotate({-rot})">')
        svg.append(f'<rect x="{-rw/2}" y="{-rh/2}" width="{rw}" height="{rh}" fill="#36c574" fill-opacity="0.5" stroke="#2ea043" />')
        svg.append('</g>')
    svg.append('</svg>')
    data = "\n".join(svg).encode("utf-8")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _render_symbol_svg(path: Path) -> str:
    lines = path.read_text(errors="ignore").splitlines()
    pins = []
    for ln in lines:
        ln = ln.strip()
        if ln.startswith("(pin "):
            try:
                parts = ln.replace("(", " ").replace(")", " ").split()
                idx_at = parts.index("at") + 1
                x = float(parts[idx_at])
                y = float(parts[idx_at + 1])
                pins.append((x, y))
            except Exception:
                continue
    if not pins:
        raise RuntimeError("No pins parsed")
    xs = [p[0] for p in pins]
    ys = [p[1] for p in pins]
    minx, maxx = min(xs) - 2, max(xs) + 2
    miny, maxy = min(ys) - 2, max(ys) + 2
    width = maxx - minx
    height = maxy - miny
    scale = 400.0 / max(width, height)
    def tx(x): return (x - minx) * scale
    def ty(y): return (maxy - y) * scale
    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="420" height="420" viewBox="0 0 420 420" style="background:#0f141b;">']
    svg.append('<rect x="0" y="0" width="420" height="420" fill="#0f141b" stroke="#243043" />')
    for x, y in pins:
        svg.append(f'<circle cx="{tx(x)}" cy="{ty(y)}" r="4" fill="#36c574" stroke="#2ea043" />')
    svg.append('</svg>')
    data = "\n".join(svg).encode("utf-8")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"
