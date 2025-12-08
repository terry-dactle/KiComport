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
            img, _ = _render_symbol_or_footprint(cand)
            return img, ""
        except Exception as exc:
            fallback_note = f"Rendering via kicad-cli failed: {exc}. Trying lightweight parser."
            try:
                if cand.type == CandidateType.footprint:
                    return _render_footprint_svg(path), ""
                else:
                    return _render_symbol_svg(path), ""
            except Exception as exc2:
                fallback_note = f"Render parsers failed: {exc2}. Showing text preview."
    elif cand.type == CandidateType.model:
        try:
            img, _ = _render_3d(cand)
            return img, ""
        except Exception as exc:
            # Server-side 3D libs missing; let the browser viewer handle it without showing an error note.
            return "", ""
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
                pad_id = parts[1].strip('"') if len(parts) > 1 else ""
                idx_at = parts.index("at") + 1
                x = float(parts[idx_at])
                y = float(parts[idx_at + 1])
                rot = float(parts[idx_at + 2]) if parts[idx_at + 2].replace(".", "", 1).lstrip("-").isdigit() else 0.0
                idx_size = parts.index("size") + 1
                sx = float(parts[idx_size])
                sy = float(parts[idx_size + 1])
                pads.append((x, y, sx, sy, rot, pad_id))
            except Exception:
                continue
    if not pads:
        raise RuntimeError("No pads parsed")
    xs = []
    ys = []
    for x, y, sx, sy, *_ in pads:
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
    for x, y, sx, sy, rot, pad_id in pads:
        cx = tx(x)
        cy = ty(y)
        rw = sx * scale
        rh = sy * scale
        svg.append(f'<g transform="translate({cx},{cy}) rotate({-rot})">')
        svg.append(f'<rect x="{-rw/2}" y="{-rh/2}" width="{rw}" height="{rh}" fill="#36c574" fill-opacity="0.5" stroke="#2ea043" />')
        if pad_id:
            svg.append(f'<text x="0" y="4" fill="#e9edf5" font-size="12" text-anchor="middle">{pad_id}</text>')
        svg.append('</g>')
    svg.append('</svg>')
    data = "\n".join(svg).encode("utf-8")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


def _render_symbol_svg(path: Path) -> str:
    lines = path.read_text(errors="ignore").splitlines()
    pins = []
    polys = []
    poly_collect = False
    current_poly = []
    top_text = []
    for ln in lines:
        stripped = ln.strip()
        if stripped.startswith("(property \"Value\"") or stripped.startswith("(property \"Reference\""):
            try:
                parts = stripped.split("\"")
                if len(parts) >= 4:
                    label = parts[1]
                    value = parts[3]
                    top_text.append((label, value))
            except Exception:
                pass
        if stripped.startswith("(polyline"):
            poly_collect = True
            current_poly = []
            continue
        if poly_collect:
            if stripped.startswith(")"):
                if current_poly:
                    polys.append(current_poly)
                poly_collect = False
                continue
            if stripped.startswith("(xy"):
                try:
                    parts = stripped.replace("(", " ").replace(")", " ").split()
                    x = float(parts[1]); y = float(parts[2])
                    current_poly.append((x, y))
                except Exception:
                    continue
        if stripped.startswith("(pin "):
            try:
                parts = stripped.replace("(", " ").replace(")", " ").split()
                pin_type = parts[1] if len(parts) > 1 else ""
                idx_at = parts.index("at") + 1
                x = float(parts[idx_at])
                y = float(parts[idx_at + 1])
                rot = float(parts[idx_at + 2]) if parts[idx_at + 2].replace(".", "", 1).lstrip("-").isdigit() else 0.0
                length = 5.0
                if "length" in parts:
                    try:
                        length = float(parts[parts.index("length") + 1])
                    except Exception:
                        pass
                pins.append({"x": x, "y": y, "length": length, "rot": rot, "name": "", "number": "", "ptype": pin_type})
            except Exception:
                continue
        if pins:
            if stripped.startswith("(name "):
                try:
                    val = stripped.split("\"")[1]
                    pins[-1]["name"] = val
                except Exception:
                    pass
            if stripped.startswith("(number "):
                try:
                    val = stripped.split("\"")[1]
                    pins[-1]["number"] = val
                except Exception:
                    pass
    if not pins:
        raise RuntimeError("No pins parsed")

    # Scaling constants
    SCALE = 10.0  # px per mm
    INTERNAL_SIZE = 2000
    TEXT_OFFSET_MM = 2.0

    import math
    # Body bounds
    body_minx = body_maxx = None
    body_miny = body_maxy = None
    for poly in polys:
        for x, y in poly:
            body_minx = x if body_minx is None else min(body_minx, x)
            body_maxx = x if body_maxx is None else max(body_maxx, x)
            body_miny = y if body_miny is None else min(body_miny, y)
            body_maxy = y if body_maxy is None else max(body_maxy, y)

    # Collect bounds in mm
    xs = []
    ys = []
    if body_minx is not None:
        xs.extend([body_minx, body_maxx]); ys.extend([body_miny, body_maxy])
    for p in pins:
        x = p["x"]; y = p["y"]; length = p["length"]; rot = p["rot"]
        ang = math.radians(rot)
        dirx = math.cos(ang); diry = math.sin(ang)
        xs.append(x); ys.append(y)
        xs.append(x + dirx * length); ys.append(y + diry * length)
        xs.append(x + dirx * (length + TEXT_OFFSET_MM)); ys.append(y + diry * (length + TEXT_OFFSET_MM))
    if not xs:
        return _encode_svg(_build_svg(["Empty symbol"]))

    minx_mm, maxx_mm = min(xs) - 2, max(xs) + 2
    miny_mm, maxy_mm = min(ys) - 6, max(ys) + 12

    def tx_mm(x): return x * SCALE
    def ty_mm(y): return -y * SCALE

    # Centering
    cx_mm = (minx_mm + maxx_mm) / 2
    cy_mm = (miny_mm + maxy_mm) / 2
    def tx(x): return tx_mm(x - cx_mm) + INTERNAL_SIZE / 2
    def ty(y): return ty_mm(y - cy_mm) + INTERNAL_SIZE / 2

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{INTERNAL_SIZE}" height="{INTERNAL_SIZE}" viewBox="0 0 {INTERNAL_SIZE} {INTERNAL_SIZE}" style="background:#0f141b;">']
    svg.append(f'<rect x="0" y="0" width="{INTERNAL_SIZE}" height="{INTERNAL_SIZE}" fill="#0f141b" stroke="#243043" />')

    for poly in polys:
        pts = " ".join(f"{tx(x)},{ty(y)}" for x, y in poly)
        svg.append(f'<polyline points="{pts}" fill="none" stroke="#9aa6b7" stroke-width="2" />')

    # Titles
    if body_minx is not None:
        title_cx = (body_minx + body_maxx) / 2
        title_y_base = (body_maxy + 6)
    else:
        title_cx = cx_mm
        title_y_base = maxy_mm
    for i, (lbl, val) in enumerate(top_text[:2]):  # Reference then Value
        svg.append(f'<text x="{tx(title_cx)}" y="{ty(title_y_base) - i*16}" fill="#9fc4ff" font-size="16" text-anchor="middle">{val}</text>')

    # Pins
    for p in pins:
        x_mm = p["x"]; y_mm = p["y"]; length_mm = p["length"]; rot_deg = p["rot"]
        ang = math.radians(rot_deg)
        dirx = math.cos(ang); diry = math.sin(ang)
        x0 = tx(x_mm); y0 = ty(y_mm)
        x1 = x0 + dirx * length_mm * SCALE
        y1 = y0 - diry * length_mm * SCALE
        svg.append(f'<line x1="{x0}" y1="{y0}" x2="{x1}" y2="{y1}" stroke="#36c574" stroke-width="2" />')
        svg.append(f'<circle cx="{x0}" cy="{y0}" r="3" fill="#2ea043" />')
        name_txt = str(p.get("name") or "").strip()
        num_txt = str(p.get("number") or "").strip()
        text_dist = (length_mm + TEXT_OFFSET_MM) * SCALE
        name_x = x0 + dirx * text_dist
        name_y = y0 - diry * text_dist
        if name_txt:
            anchor = "start" if dirx >= 0 else "end"
            svg.append(f'<text x="{name_x}" y="{name_y}" fill="#e9edf5" font-size="12" text-anchor="{anchor}" dy="4">{name_txt}</text>')
        if num_txt:
            num_offset = 6
            num_x = x0 - diry * num_offset
            num_y = y0 - dirx * num_offset
            svg.append(f'<text x="{num_x}" y="{num_y}" fill="#e9edf5" font-size="12" font-weight="700" text-anchor="middle" dy="4">{num_txt}</text>')

    svg.append('</svg>')
    data = "\n".join(svg).encode("utf-8")
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"
