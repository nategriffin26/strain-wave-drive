"""Ratio equations of a generated build as light and dark SVGs, from the parameters in its report.json."""
from strain_wave_drive.parameters import Variant, apply_overrides, preset

THEMES = {"light": ("#1f2328", "#59636e"), "dark": ("#d1d9e0", "#9198a1")}
SANS = "-apple-system, BlinkMacSystemFont, Segoe UI, Helvetica, Arial, sans-serif"
SERIF = "Georgia, 'Times New Roman', serif"
WIDTH, ROW_H, TOP = 460, 74, 30


def omega(sub):
    return f'<tspan font-family="{SERIF}" font-style="italic">ω</tspan><tspan font-size="13" dy="5">{sub}</tspan>'


def op(text):
    """Text after a subscript: back on the baseline, with a gap."""
    return f'<tspan dy="-5" dx="7">{text}</tspan>'


def number(value):
    return f"{value:g}".replace("-", "−")


def report_parameters(report):
    """The Parameters a build used, rebuilt from its report.json so derived tooth counts match the build."""
    values = report["parameters"]
    return apply_overrides(preset(values["variant"]), [f"{name}={value}" for name, value in values.items() if name != "variant"])


def rows(p):
    n = p.flex_teeth / (p.circular_teeth - p.flex_teeth)
    if p.variant is Variant.PLAIN:
        return [(f'{omega("in")}{op("/")} {omega("out")}{op(f"= −{number(p.flex_teeth)} / ({number(p.circular_teeth)} − {number(p.flex_teeth)}) =")} '
                 f'<tspan font-weight="600">{number(p.ratio)}</tspan>',
                 f"Flex {p.flex_teeth}, circular spline {p.circular_teeth} (fixed), rollers on the motor shaft")]
    k = p.ring_teeth / p.sun_teeth
    return [
        (f'{omega("S")}{op(f"+ {number(k)}")}{omega("F")}{op(f"= {number(1 + k)}")}{omega("H")}',
         f"Sun {p.sun_teeth}, ring {p.ring_teeth} in the flex, carrier = wave generator"),
        (f'{omega("F")}{op("= −")}{omega("H")}{op(f"/ {number(n)}")}',
         f"Flex {p.flex_teeth}, circular spline {p.circular_teeth} (fixed)"),
        (f'{omega("S")}{op("/")} {omega("out")}{op(f"= −({number(1 + k)} × {number(n)} + {number(k)}) =")} '
         f'<tspan font-weight="600">{number(p.ratio)}</tspan>',
         f"Dynamic spline {p.flex_teeth} turns with the flex"),
    ]


def write_ratio(report, out_dir):
    preset_name = report["name"]
    lines = rows(report_parameters(report))
    height = TOP + ROW_H * len(lines) - 16
    out_dir.mkdir(parents=True, exist_ok=True)
    for theme, (ink, muted) in THEMES.items():
        els = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {height}" width="{WIDTH}" height="{height}" font-family="{SANS}">']
        for i, (equation, note) in enumerate(lines):
            y = TOP + i * ROW_H
            els.append(f'<text x="0" y="{y}" font-size="24" fill="{ink}">{equation}</text>')
            els.append(f'<text x="0" y="{y + 26}" font-size="15" fill="{muted}">{note}</text>')
            if i < len(lines) - 1:
                els.append(f'<line x1="0" y1="{y + 42}" x2="{WIDTH}" y2="{y + 42}" stroke="{muted}" stroke-width="0.6" opacity="0.5"/>')
        els.append("</svg>")
        (out_dir / f"ratio-{preset_name}-{theme}.svg").write_text("\n".join(els) + "\n")
