"""Renders every README image for a preset from its generated geometry.

Usage: uv run python tools/render/render_images.py planetary [--render-dir build/planetary/render]
       [--report models/planetary/report.json] [--out images]

Writes images/<preset>-{hero,exploded,cutaway}-{light,dark}.png (Blender Cycles; set BLENDER if blender is
not on PATH), images/section-<preset>-{light,dark}.svg and images/ratio-<preset>-{light,dark}.svg (tooth counts
from the build's report.json), and prints the part named by each section balloon.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from ratio_svg import write_ratio  # noqa: E402
from section_svg import write_section  # noqa: E402

BLENDER_CANDIDATES = ("/Applications/Blender.app/Contents/MacOS/Blender",)


def blender():
    for candidate in (os.environ.get("BLENDER"), shutil.which("blender"), *BLENDER_CANDIDATES):
        if candidate and Path(candidate).exists():
            return candidate
    # scene.py uses the Manifold boolean solver, added in Blender 4.5.
    raise SystemExit("Blender not found: install Blender 4.5 or newer, or set BLENDER=/path/to/blender.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("preset", choices=("planetary", "plain"))
    p.add_argument("--render-dir", type=Path)
    p.add_argument("--report", type=Path)
    p.add_argument("--out", type=Path, default=Path("images"))
    p.add_argument("--scale", type=float, default=1.0, help="resolution multiplier; 0.5 for a quick look")
    args = p.parse_args()
    render_dir = args.render_dir or Path("build") / args.preset / "render"
    report_path = args.report or Path("models") / args.preset / "report.json"
    for needed in (render_dir / "parts.json", report_path):
        if not needed.exists():
            raise SystemExit(f"{needed} not found: run the generator for {args.preset} first.")
    report = json.loads(report_path.read_text())
    start = time.perf_counter()
    subprocess.run([blender(), "-b", "--factory-startup", "-P", str(HERE / "scene.py"), "--",
                    "--input", str(render_dir), "--out", str(args.out), "--prefix", args.preset,
                    "--scale", str(args.scale)], check=True)
    legend = write_section(render_dir, args.out, args.preset)
    write_ratio(report, args.out)
    print(f"section balloons: {', '.join(f'{i} {name}' for i, name in enumerate(legend, start=1))}")
    print(f"done in {time.perf_counter() - start:.0f} s")


if __name__ == "__main__":
    main()
