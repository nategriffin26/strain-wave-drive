"""Rewrites the generated blocks of README.md from the generator's outputs; the prose around them stays hand-edited.

Usage: uv run python tools/readme.py

A block is <!-- generated:NAME -->…<!-- /generated -->, inline or spanning lines. Its content comes from
models/<preset>/report.json, bom.csv and print.csv, models/coupons/print.csv and
build/planetary/render/section-balloons.json (written by tools/render/render_images.py). Tooth counts and ratios
come from the parameters each report was built with; only the parameter table shows the presets' defaults. Run
the generator and render_images.py first.
"""
import csv
import json
import re
import sys
from dataclasses import fields
from pathlib import Path

from strain_wave_drive.parameters import Parameters, preset

sys.path.insert(0, str(Path(__file__).resolve().parent / "render"))
from ratio_svg import report_parameters  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
BLOCK = re.compile(r"<!-- generated:([a-z0-9-]+) -->(.*?)<!-- /generated -->", re.S)


def read(path):
    path = ROOT / path
    if not path.exists():
        raise SystemExit(f"{path.relative_to(ROOT)} not found: run the generator and tools/render/render_images.py first.")
    if path.suffix == ".json":
        return json.loads(path.read_text())
    with path.open(encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def num(value, digits=1):
    text = f"{value:.{digits}f}" if isinstance(value, float) else str(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace("-", "−")


def table(header, rows, align):
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(align) + " |"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)


def summary(reports):
    def column(r):
        e, c = r["envelope"], r["counts"]
        return [f"{num(r['ratio'], 0)} : 1", f"Ø{num(e['diameter_mm'])} × {num(e['height_above_motor_face_mm'])} mm",
                f"{num(e['total_height_with_motor_mm'])} mm", c["printed_parts"],
                f"{c['catalog_items']} items, {c['catalog_line_items']} part numbers", c["custom_metal_parts"]]
    labels = ["Ratio", "Size above the motor face", "Height with motor", "Printed parts", "Catalog hardware", "Machined parts"]
    columns = [column(r) for r in reports.values()]
    return table(["", *(name.capitalize() for name in reports)], [(label, *(col[i] for col in columns)) for i, label in enumerate(labels)],
                 ["---"] + ["---:"] * len(reports))


def section_parts(balloons, prints, render_parts):
    label_to_id = {p["name"].rstrip(" _0123456789"): Path(p["file"]).stem for p in render_parts}
    material = {row["part"]: row["material"] for row in prints}
    return table(["", "Part", "Material"], [(i, label, material.get(label_to_id.get(label), "—")) for i, label in enumerate(balloons, start=1)],
                 ["---:", "---", "---"])


def coupons(rows):
    families = {}
    for row in rows:
        families.setdefault(re.sub(r"(_minus_\d+|_plus_\d+|_\d+)$", "", row["part"]), []).append(row)
    return table(["Coupon", "Prints", "Material", "What it settles"],
                 [(name.replace("_", " ").capitalize(), len(group), group[0]["material"], group[0]["note"]) for name, group in families.items()],
                 ["---", "---:", "---", "---"])


def print_settings(prints):
    values = lambda key: ", ".join(sorted({row[key] for row in prints}))
    return f"{values('layer_height_mm')} mm layers, {values('walls')} walls, {values('infill_percent')}% infill"


def printed_parts(prints, render_parts):
    label = {Path(p["file"]).stem: p["name"] for p in render_parts}
    return table(["Part", "Qty", "Material", "Mass (g)", "Supports", "Orientation"],
                 [(label.get(row["part"], row["part"].replace("_", " ")), row["qty"], row["material"], num(float(row["estimated_mass_g"])),
                   row["supports"], row["orientation"]) for row in prints],
                 ["---", "---:", "---", "---:", "---", "---"])


def hardware(bom):
    return table(["Qty", "Part", "Vendor", "Part number", "Size"],
                 [(row["qty"], row["designation"], row["vendor"], f"[{row['part_number']}]({row['link']})", row["dimensions"]) for row in bom],
                 ["---:", "---", "---", "---", "---"])


def parameters():
    planetary, plain = preset("planetary"), preset("plain")
    shown = lambda value: num(round(value, 3) if isinstance(value, float) else value, 3)
    rows = []
    for f in fields(Parameters):
        limit = f.metadata.get("limit")
        if limit is None:
            continue
        default = shown(getattr(planetary, f.name))
        if getattr(plain, f.name) != getattr(planetary, f.name):
            default += f" (plain {shown(getattr(plain, f.name))})"
        unit = "°" if limit.unit == "deg" else " " + limit.unit
        rows.append((f"`{f.name}`", default, f"{num(limit.minimum, 2)} to {num(limit.maximum, 2)}{unit}", f.metadata["meaning"]))
    return table(["Parameter", "Default", "Range", "Meaning"], rows, ["---", "---:", "---", "---"])


def fits(report):
    check = next(c for c in report["checks"] if c["name"] == "Prototype fit comparison")
    kept, changed = [], []
    for record in check["records"]:
        comparisons = record.get("comparisons") or []
        if comparisons:
            kept.append((record["fit"],
                         ", ".join(f"{c['dimension'].replace('_', ' ')} {num(c['prototype'], 3)}" for c in comparisons),
                         ", ".join(num(round(c["actual"], 3), 3) + ("" if c["passed"] else " ✗") for c in comparisons)))
        if record["status"] == "deliberate_change" and record.get("deliberate_change"):
            changed.append((record["fit"], record["deliberate_change"]))
    return (table(["Fit", "Prototype", "Generated"], kept, ["---", "---", "---"])
            + f"\n\n<details><summary>Changed from the prototype ({len(changed)})</summary>\n\n"
            + table(["Fit", "Change"], changed, ["---", "---"]) + "\n\n</details>")


def blocks():
    reports = {name: read(f"models/{name}/report.json") for name in ("planetary", "plain")}
    planetary = reports["planetary"]
    prints = read("models/planetary/print.csv")
    render_parts = read("build/planetary/render/parts.json")["parts"]
    p, q = report_parameters(planetary), report_parameters(reports["plain"])
    return {
        "ratio-planetary": f"{num(planetary['ratio'], 0)} : 1",
        "ratio-plain": f"{num(reports['plain']['ratio'], 0)} : 1",
        "summary": summary(reports),
        "flex-teeth": str(p.flex_teeth),
        "circular-teeth": str(p.circular_teeth),
        "ring-teeth": str(p.ring_teeth),
        "tooth-difference": str(p.circular_teeth - p.flex_teeth),
        "plain-ratio": f"−{q.flex_teeth} / ({q.circular_teeth} − {q.flex_teeth}) = {num(reports['plain']['ratio'], 0)} : 1",
        "section-parts": section_parts(read("build/planetary/render/section-balloons.json"), prints, render_parts),
        "coupons": coupons(read("models/coupons/print.csv")),
        "print-settings": print_settings(prints),
        "printed-parts": printed_parts(prints, render_parts),
        "hardware": hardware(read("models/planetary/bom.csv")),
        "assembly": "\n".join(f"{i}. {step}" for i, step in enumerate(planetary["assembly_order"], start=1)),
        "build-seconds": num(planetary["build_seconds"], 0),
        "parameters": parameters(),
        "fits": fits(planetary),
    }


def main():
    values = blocks()
    readme = ROOT / "README.md"
    text = readme.read_text()
    unknown = sorted({name for name, _ in BLOCK.findall(text)} - values.keys())
    if unknown:
        raise SystemExit(f"README.md has generated blocks with no source: {', '.join(unknown)}")

    def fill(match):
        name, old = match.group(1), match.group(2)
        body = values[name]
        if "\n" in old or "\n" in body:
            body = f"\n\n{body}\n\n"
        return f"<!-- generated:{name} -->{body}<!-- /generated -->"

    updated, count = BLOCK.subn(fill, text)
    readme.write_text(updated)
    print(f"README.md: {count} generated blocks rewritten")


if __name__ == "__main__":
    main()
