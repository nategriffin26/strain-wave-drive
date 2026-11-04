"""Regenerate named CAD, manufacturing files and explicit geometry checks."""
import argparse
from collections import Counter
from dataclasses import asdict
import json
from pathlib import Path
import sys
from time import perf_counter
from .assembly import build, motion
from .checks import manufactured_interference, operating_mesh, assembly_access, retention_stack, measured_holder_preload
from .coupons import build_coupons
from .export import export, write_glb
from .gearing import deformation
from .parameters import DesignError, apply_overrides, preset, Variant
from .prototype import compare
from .bounds import brep_bounds
from .motion_check import motion_clearance


def generate(name, output_root, assignments=()):
    started = perf_counter()
    destination = Path(output_root) / name
    destination.mkdir(parents=True, exist_ok=True)
    reference = Path(__file__).resolve().parents[2] / 'reference'
    if name == 'coupons':
        assembly = build_coupons()
        parameters, dimensions, wave = None, {}, None
    else:
        parameters = apply_overrides(preset(name), assignments)
        assembly, dimensions = build(parameters)
        wave = deformation(parameters)
    geometry_seconds = perf_counter() - started
    glb = write_glb(assembly, destination / f'{name}.glb') if parameters is not None else None
    checks = [export(assembly, destination, Path(output_root).parent / 'build' / name / 'render', wave)]
    if parameters is not None:
        fits = compare(parameters, assembly, dimensions, json.loads((reference/'prototype-fits.json').read_text()))
        checks.extend((manufactured_interference(assembly), assembly_access(assembly, parameters, dimensions),
                       retention_stack(assembly, parameters, dimensions), fits))
        checks.extend(operating_mesh(parameters, json.loads((reference/'prototype-profiles.json').read_text())))
        checks.extend(motion_clearance(assembly, parameters, dimensions))
        if parameters.variant is Variant.PLANETARY:
            checks.append(measured_holder_preload(fits['measured_features'], parameters.planet_preload))
    catalog = Counter(part.catalog for part in assembly.parts if part.catalog is not None)
    custom_parts = [part.id for part in assembly.parts if not part.printed and part.catalog is None and part.catalog_owner is None]
    standard_items_without_sku = [item.designation for item in catalog if not item.part_number]
    checks.append({'name': 'Catalog hardware classification', 'passed': not custom_parts,
                   'custom_parts': custom_parts, 'standard_designations_without_vendor_sku': standard_items_without_sku})
    bounds = [brep_bounds(part.shape) for part in assembly.parts]
    minimum = [min(getattr(b, axis + 'min') for b in bounds) for axis in 'xyz']
    maximum = [max(getattr(b, axis + 'max') for b in bounds) for axis in 'xyz']
    report = {
        'name': name, 'units': 'mm', 'passed': all(check['passed'] for check in checks),
        'parameters': parameters.dictionary() if parameters else {}, 'dimensions': dimensions,
        'counts': {'printed_parts': sum(part.printed for part in assembly.parts),
                   'catalog_items': sum(catalog.values()), 'catalog_line_items': len(catalog),
                   'custom_metal_parts': len(custom_parts), 'scene_nodes': len(assembly.parts)},
        'catalog': [{**asdict(item), 'quantity': quantity} for item, quantity in catalog.items()],
        'envelope': {'bounds_mm': [minimum, maximum],
                     'size_mm': [high-low for low, high in zip(minimum, maximum)],
                     'diameter_mm': 2*dimensions['drum_outer_radius_mm'] if dimensions else None,
                     'height_above_motor_face_mm': maximum[2] if parameters else None,
                     'total_height_with_motor_mm': maximum[2]-minimum[2] if parameters else None,
                     'limits': 'Motor connector, flexible leads and external payload are excluded.'},
        'ratio': parameters.ratio if parameters else None,
        'geometry_seconds': geometry_seconds, 'build_seconds': perf_counter()-started,
        'glb': glb, 'declared_contacts': [asdict(contact) for contact in assembly.contacts],
        'assembly_order': dimensions.get('assembly_order', []), 'checks': checks,
        'limits': ['Geometry checks do not establish torque, efficiency, fatigue life or loaded backlash.',
                   'New packaging, full-height planets and zero axial float require physical coupon and assembly trials.'],
    }
    if parameters is not None:
        report['motion'] = motion(assembly, parameters)
    report['files'] = [{'path': str(path.relative_to(destination)), 'bytes': path.stat().st_size}
                       for path in sorted(destination.rglob('*')) if path.is_file() and path.name != 'report.json']
    report['build_seconds'] = perf_counter()-started
    (destination / 'report.json').write_text(json.dumps(report, indent=2, allow_nan=False) + '\n')
    failed = [check['name'] for check in checks if not check['passed']]
    print(json.dumps({'preset': name, 'passed': report['passed'], 'build_seconds': report['build_seconds'],
                      'geometry_seconds': geometry_seconds, 'counts': report['counts'], 'glb_bytes': glb['bytes'] if glb else 0,
                      'failed_checks': failed, 'report': str(destination / 'report.json')}), flush=True)
    return report['passed']


def main(argv=None):
    parser = argparse.ArgumentParser(description='Regenerate the planetary and plain drives and the fit-test coupons.')
    parser.add_argument('--preset', choices=('planetary', 'plain', 'coupons'), help='Generate only this preset; default generates all three.')
    parser.add_argument('--set', action='append', default=[], metavar='NAME=VALUE', help='Override a named parameter; repeat for multiple parameters.')
    parser.add_argument('--out', type=Path, default=Path('models'), help='Output root (default: models).')
    arguments = parser.parse_args(argv)
    if arguments.set and arguments.preset not in ('planetary', 'plain'):
        parser.error('--set requires --preset planetary or --preset plain; coupons retain their labelled calibration values.')
    names = (arguments.preset,) if arguments.preset else ('planetary', 'plain', 'coupons')
    passed = True
    try:
        for name in names:
            passed = generate(name, arguments.out, arguments.set) and passed
    except DesignError as exc:
        parser.error(str(exc))
    return 0 if passed else 1


if __name__ == '__main__':
    sys.exit(main())
