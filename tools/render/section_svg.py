"""Half-section of a render-input directory as light and dark SVGs, with numbered balloons.

The section plane holds the drive axis and the wave generator's major axis (parts.json azimuth_deg
minus 45 degrees), so a planet or roller and full tooth engagement show in cut. Balloons carry
numbers only, top to bottom; <render dir>/section-balloons.json lists the part named by each number.
"""
import json
from pathlib import Path

import numpy as np
import trimesh
from shapely.geometry import LineString, Polygon, box
from shapely.ops import unary_union

THEMES = {
    "light": {"ink": "#1f2328", "muted": "#59636e", "flex": "#d4332f", "steel": "#c7ccd1", "brass": "#d9c08a",
              "motor": "#3d444d", "printed": "#ffffff", "printed_accent": "#ffffff", "hatch": "#59636e"},
    "dark": {"ink": "#d1d9e0", "muted": "#9198a1", "flex": "#f0605a", "steel": "#656c76", "brass": "#8a7444",
             "motor": "#262c36", "printed": "#0d1117", "printed_accent": "#0d1117", "hatch": "#9198a1"},
}
NUMBERED = ("printed", "printed_accent", "flex")
SCALE = 4.0  # px per mm
PAD = 30
FONT = "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"


def section(mesh, angle_deg):
    """Polygons (x, z) of the mesh cut by the vertical plane at angle_deg through the Z axis."""
    m = mesh.copy()
    m.apply_transform(trimesh.transformations.rotation_matrix(np.radians(-angle_deg), (0, 0, 1)))
    seg = trimesh.intersections.mesh_plane(m, (0, 1, 0), (0, 0, 0))
    if len(seg) == 0:
        return Polygon()
    seg = np.round(seg[:, :, [0, 2]], 6)
    ends = {}
    for i, (a, b) in enumerate(seg):
        ends.setdefault(tuple(a), []).append(i)
        ends.setdefault(tuple(b), []).append(i)
    used = np.zeros(len(seg), bool)
    filled = Polygon()
    for start in range(len(seg)):
        if used[start]:
            continue
        used[start] = True
        loop = [tuple(seg[start][0]), tuple(seg[start][1])]
        while loop[-1] != loop[0]:
            nxt = [j for j in ends.get(loop[-1], []) if not used[j]]
            if not nxt:
                break
            used[nxt[0]] = True
            a, b = map(tuple, seg[nxt[0]])
            loop.append(b if a == loop[-1] else a)
        if len(loop) > 3:
            # even-odd fill: each closed loop toggles inside/outside
            filled = filled.symmetric_difference(Polygon(loop).buffer(0))
    return filled.simplify(0.01)


def path_d(poly, tf):
    parts = []
    for g in getattr(poly, "geoms", [poly]):
        if g.is_empty:
            continue
        for ring in [g.exterior, *g.interiors]:
            pts = [tf(x, z) for x, z in ring.coords]
            parts.append("M" + " L".join(f"{x:.2f},{y:.2f}" for x, y in pts) + " Z")
    return " ".join(parts)


def write_section(render_dir, out_dir, prefix):
    spec = json.loads((render_dir / "parts.json").read_text())
    angle = spec.get("azimuth_deg", -50.0) - 45.0
    keep = box(-3, -1e4, 1e4, 1e4)
    sections = []
    for part in spec["parts"]:
        poly = section(trimesh.load(render_dir / part["file"]), angle).intersection(keep)
        if not poly.is_empty and poly.area > 0.01:
            sections.append((part, poly))
    if not sections:
        raise SystemExit(f"{render_dir}: the section plane cuts no part")
    x0, z0, x1, z1 = unary_union([p for _, p in sections]).bounds
    width, height = (x1 - x0) * SCALE + 2 * PAD, (z1 - z0) * SCALE + 2 * PAD
    tf = lambda x, z: (PAD + (x - x0) * SCALE, PAD + (z1 - z) * SCALE)

    numbered, seen = [], set()
    for part, poly in sections:
        key = part["name"].rstrip(" _0123456789")
        if part["material"] not in NUMBERED or key in seen:
            continue
        seen.add(key)
        numbered.append((key, max(getattr(poly, "geoms", [poly]), key=lambda g: g.area)))
    # Balloons sit evenly down the right side, spanning the numbered parts' height (at least 40 px apart), not
    # the whole motor. Each leader meets its part at the height nearest its balloon, just inside the part's
    # right edge, so most leaders run straight across; leaders and balloons share one top-to-bottom order, so no
    # two leaders cross.
    numbered.sort(key=lambda kp: -kp[1].centroid.y)
    span_top = tf(0, max(p.bounds[3] for _, p in numbered))[1]
    span_bottom = tf(0, min(p.bounds[1] for _, p in numbered))[1]
    need = 40 * (len(numbered) - 1)
    centre = min(max((span_top + span_bottom) / 2, PAD + need / 2), height - PAD - need / 2)
    half = max(need, span_bottom - span_top) / 2
    slots = list(np.linspace(centre - half, centre + half, len(numbered))) if len(numbered) > 1 else [centre]
    targets = []
    for (key, poly_main), slot in zip(numbered, slots):
        lo, hi = poly_main.bounds[1], poly_main.bounds[3]
        margin = min(0.6, (hi - lo) / 4)
        z = min(max(z1 - (slot - PAD) / SCALE, lo + margin), hi - margin)
        run = poly_main.intersection(LineString([(x0 - 1, z), (x1 + 1, z)]))
        if run.is_empty:
            z = poly_main.representative_point().y
            run = poly_main.intersection(LineString([(x0 - 1, z), (x1 + 1, z)]))
        right = max(getattr(run, "geoms", [run]), key=lambda g: g.bounds[2])
        tx = right.bounds[2] - min(1.0, (right.bounds[2] - right.bounds[0]) / 2)
        targets.append((tf(tx, z), key))
    targets.sort(key=lambda t: t[0][1])
    label_w = 76
    out_dir.mkdir(parents=True, exist_ok=True)
    for theme, c in THEMES.items():
        els = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width + label_w:.0f} {height:.0f}" '
               f'width="{width + label_w:.0f}" height="{height:.0f}" font-family="{FONT}">', "<defs>"]
        for i, (angle_h, gap) in enumerate([(45, 6), (-45, 6), (45, 3.5), (-45, 3.5)]):
            els.append(f'<pattern id="h{i}" width="{gap}" height="{gap}" patternUnits="userSpaceOnUse" patternTransform="rotate({angle_h})">'
                       f'<line x1="0" y1="0" x2="0" y2="{gap}" stroke="{c["hatch"]}" stroke-width="0.7"/></pattern>')
        els.append("</defs>")
        printed_index = 0
        for part, poly in sections:
            mat = part["material"]
            d = path_d(poly, tf)
            els.append(f'<path d="{d}" fill="{c[mat]}" fill-rule="evenodd" stroke="{c["ink"]}" stroke-width="0.7" stroke-linejoin="round"/>')
            if mat in ("printed", "printed_accent"):
                # adjacent printed parts alternate hatch angle and pitch, as on a drawing
                els.append(f'<path d="{d}" fill="url(#h{printed_index % 4})" fill-rule="evenodd" stroke="none"/>')
                printed_index += 1
        ax, _ = tf(0, 0)
        els.append(f'<line x1="{ax:.1f}" y1="{PAD - 18}" x2="{ax:.1f}" y2="{height - PAD + 18:.1f}" stroke="{c["muted"]}" '
                   f'stroke-width="0.8" stroke-dasharray="14 4 3 4"/>')
        bx, knee = width + 40, width - PAD + 8
        # Leaders run horizontally to a common knee right of the drawing, then to balloons kept in the same
        # top-to-bottom order, so no two leaders cross.
        for i, (((tx, ty), _), y) in enumerate(zip(targets, slots), start=1):
            els.append(f'<polyline points="{tx:.1f},{ty:.1f} {knee:.1f},{ty:.1f} {bx - 32:.1f},{y:.1f} {bx - 19:.1f},{y:.1f}" '
                       f'fill="none" stroke="{c["ink"]}" stroke-width="1"/>')
            els.append(f'<circle cx="{tx:.1f}" cy="{ty:.1f}" r="2.5" fill="{c["ink"]}"/>')
            els.append(f'<circle cx="{bx:.1f}" cy="{y:.1f}" r="19" fill="none" stroke="{c["ink"]}" stroke-width="1.4"/>')
            els.append(f'<text x="{bx:.1f}" y="{y + 8:.1f}" font-size="22" text-anchor="middle" fill="{c["ink"]}">{i}</text>')
        els.append("</svg>")
        (out_dir / f"section-{prefix}-{theme}.svg").write_text("\n".join(els) + "\n")
    legend = [key for _, key in targets]
    (render_dir / "section-balloons.json").write_text(json.dumps(legend, indent=1) + "\n")
    return legend
