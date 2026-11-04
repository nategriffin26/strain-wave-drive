"""Axial-section rotation envelopes and phase-coupled sun/planet mesh checks."""
import math
from itertools import combinations
import numpy as np
import cadquery as cq
from shapely import affinity, transform
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union
from .bounds import brep_bounds
from .gearing import deformation
from .parameters import Variant

LINEAR_TOLERANCE_MM = .01
RADIAL_ENVELOPE_MARGIN_MM = .02
AXIAL_SAMPLE_SPACING_MM = 4.0




def section_polygon(shape, z, pivot=(0, 0), wave=None):
    section = cq.Workplane('XY').newObject([shape]).section(z).val()
    result = Polygon()
    for wire in section.Wires():
        points, _ = wire.sample(LINEAR_TOLERANCE_MM)
        coordinates = np.asarray([point.toTuple()[:2] for point in points])
        if wave is not None:
            coordinates = wave.forward(coordinates)
        polygon = Polygon(coordinates - np.asarray(pivot))
        if not polygon.is_valid:
            raise ValueError(f'An exact BRep section at Z={z:g} is invalid; the motion check cannot certify it.')
        result = result.symmetric_difference(polygon)
    return result


def radial_intervals(polygon):
    polygons = list(polygon.geoms) if polygon.geom_type == 'MultiPolygon' else [polygon]
    result = []
    for region in polygons:
        if region.is_empty:
            continue
        points = np.asarray(region.exterior.coords)
        result.append((region.distance(Point(0, 0)), float(np.linalg.norm(points, axis=1).max())))
    return result


def rotation_envelope(part, pivot=(0, 0), orbit=False, wave=None):
    bounds = brep_bounds(part.shape)
    levels = sorted({round(vertex.Z, 6) for vertex in part.shape.Vertices()} | {round(bounds.zmin, 6), round(bounds.zmax, 6)})
    levels = [level for level in levels if bounds.zmin-1e-6 <= level <= bounds.zmax+1e-6]
    refined = [levels[0]]
    for low, high in zip(levels, levels[1:]):
        count = max(1, math.ceil((high-low)/AXIAL_SAMPLE_SPACING_MM))
        refined.extend(np.linspace(low, high, count+1)[1:])
    rectangles = []
    radius = math.hypot(*pivot)
    for low, high in zip(refined, refined[1:]):
        if high-low < 1e-6:
            continue
        inset = min(1e-4, (high-low)/10)
        intervals = []
        for z in (low+inset, (low+high)/2, high-inset):
            intervals.extend(radial_intervals(section_polygon(part.shape, z, pivot, wave)))
        # Keep disconnected radial regions separate rather than filling the space between bosses and a skirt.
        intervals.sort()
        merged = []
        for minimum, maximum in intervals:
            if orbit:
                minimum = max(0, minimum-radius, radius-maximum)
                maximum += radius
            minimum = max(0, minimum-RADIAL_ENVELOPE_MARGIN_MM)
            maximum += RADIAL_ENVELOPE_MARGIN_MM
            if merged and minimum <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], maximum))
            else:
                merged.append((minimum, maximum))
        rectangles.extend(box(minimum, low, maximum, high) for minimum, maximum in merged)
    return unary_union(rectangles)


def coupled_gear_mesh(assembly, p, dimensions):
    if p.variant is Variant.PLAIN:
        return None
    parts = {part.id: part for part in assembly.parts}
    samples = []
    for fraction in (.25, .75):
        z = dimensions['gear_bottom_z_mm'] + fraction*p.flex_height
        sun = section_polygon(parts['sun_gear'].shape, z)
        planets = [(part, section_polygon(part.shape, z, part.pivot_mm[:2]))
                   for part in assembly.parts if part.id.startswith('planet_gear_')]
        for phase in np.linspace(0, 2*math.pi/p.sun_teeth, 17, endpoint=False):
            moving_sun = affinity.rotate(sun, phase, origin=(0, 0), use_radians=True)
            for part, planet in planets:
                moving_planet = affinity.rotate(planet, -phase*p.sun_teeth/p.planet_teeth, origin=(0, 0), use_radians=True)
                moving_planet = affinity.translate(moving_planet, *part.pivot_mm[:2])
                area = moving_sun.intersection(moving_planet).area
                samples.append({'part': part.id, 'z_mm': z, 'sun_relative_carrier_rad': float(phase),
                                'overlap_mm2': area, 'passed': area <= .01})
    return {'name': 'Phase-coupled sun and planet clearance', 'passed': all(sample['passed'] for sample in samples),
            'model': 'Exact BRep sections sampled at both herringbone halves; signed tooth-count kinematics in the carrier frame.',
            'linear_tolerance_mm': LINEAR_TOLERANCE_MM, 'area_tolerance_mm2': .01, 'samples': samples}


def coupled_flex_carrier(flex, carrier, parameters, wave):
    flex_bounds, carrier_bounds = brep_bounds(flex.shape), brep_bounds(carrier.shape)
    low, high = max(flex_bounds.zmin,carrier_bounds.zmin), min(flex_bounds.zmax,carrier_bounds.zmax)
    levels = sorted({low,high} | {vertex.Z for vertex in carrier.shape.Vertices() if low < vertex.Z < high})
    period = 2*math.pi/(math.gcd(parameters.flex_teeth,parameters.ring_teeth) if parameters.variant is Variant.PLANETARY else parameters.flex_teeth)
    samples = []
    for bottom, top in zip(levels,levels[1:]):
        if top-bottom <= 1e-6:
            continue
        inset = min(1e-4,(top-bottom)/10)
        for z in (bottom+inset,(bottom+top)/2,top-inset):
            neutral = section_polygon(flex.shape,z)
            fixed_carrier = section_polygon(carrier.shape,z)
            for phase in np.linspace(0,period,17,endpoint=False):
                material = affinity.rotate(neutral,phase,origin=(0,0),use_radians=True)
                deformed = transform(material,wave.forward)
                area = deformed.intersection(fixed_carrier).area
                samples.append({'z_mm':z,'material_phase_rad':float(phase),'overlap_mm2':area,'passed':area <= .01})
    return {'parts':[flex.id,carrier.id], 'frame':'wave-fixed carrier; phase-coupled flex material',
            'passed':all(sample['passed'] for sample in samples),'area_tolerance_mm2':.01,
            'maximum_section_overlap_mm2':max((sample['overlap_mm2'] for sample in samples),default=0),'samples':samples}


def motion_clearance(assembly, p, dimensions):
    wave = deformation(p)
    bounds = {part.id: brep_bounds(part.shape) for part in assembly.parts}
    contacts = {frozenset((contact.first, contact.second)): contact for contact in assembly.contacts}
    envelopes = {}
    def envelope(part, pivot=(0, 0), orbit=False):
        key = (part.id, tuple(pivot), orbit)
        if key not in envelopes:
            envelopes[key] = rotation_envelope(part, pivot, orbit, wave if part.role == 'flex' else None)
        return envelopes[key]
    pairs, exclusions = [], []
    for first, second in combinations(assembly.parts, 2):
        if first.role == second.role:
            continue
        if p.variant is Variant.PLAIN and {first.role,second.role} == {'input','carrier'}:
            continue  # The direct roller hub is clamped to the input shaft: no relative rotation.
        a, b = bounds[first.id], bounds[second.id]
        if min(a.zmax, b.zmax)-max(a.zmin, b.zmin) <= 1e-6:
            continue
        pair = frozenset((first.id, second.id))
        if first.catalog_owner == second.id or second.catalog_owner == first.id:
            exclusions.append({'parts': [first.id, second.id], 'basis': 'one catalog item',
                               'reason': 'Factory motor body/shaft interface; its axisymmetric nominal touch is unchanged by rotation.'})
            continue
        if pair in contacts:
            exclusions.append({'parts': [first.id, second.id], 'basis': contacts[pair].kind,
                               'reason': 'Bounded manufactured-contact check and prototype-relative flex mesh check apply instead.'})
            continue
        if 'sun_gear' in pair and any(name.startswith('planet_gear_') for name in pair):
            exclusions.append({'parts': [first.id, second.id], 'basis': 'phase-coupled gear mesh',
                               'reason': 'Checked at signed tooth-count phases, not independent full rotations.'})
            continue
        if {first.role, second.role} == {'carrier', 'planet'}:
            planet = first if first.role == 'planet' else second
            carrier = second if first.role == 'planet' else first
            if not planet.id.startswith('planet_gear_'):
                # A bearing/roller is axisymmetric about its axle; its local spin cannot change solid-pair clearance.
                continue
            pivot = planet.pivot_mm[:2]
            first_envelope, second_envelope = envelope(planet, pivot), envelope(carrier, pivot)
            frame = 'planet axle in the carrier frame'
        else:
            first_envelope = envelope(first, first.pivot_mm[:2], True) if first.role == 'planet' else envelope(first)
            second_envelope = envelope(second, second.pivot_mm[:2], True) if second.role == 'planet' else envelope(second)
            frame = 'drive axis, complete relative revolution'
        intersection = first_envelope.intersection(second_envelope)
        area = intersection.area
        if area > 1e-6 and {first.role,second.role} == {'flex','carrier'}:
            flex = first if first.role == 'flex' else second
            carrier = second if first.role == 'flex' else first
            pairs.append(coupled_flex_carrier(flex,carrier,p,wave))
            continue
        pairs.append({'parts': [first.id, second.id], 'frame': frame, 'overlap_rz_mm2': area,
                      'passed': area <= 1e-6, 'overlap_bounds_rz_mm': list(intersection.bounds) if not intersection.is_empty else None})
    result = [{'name': 'Full-revolution rotating-part clearance', 'passed': all(pair['passed'] for pair in pairs),
               'model': 'Radial/axial envelopes for independent relative rotations; local planet spin about its axle; flex material phases in the wave-fixed carrier frame. Rigid co-motion and axisymmetric bearing spin use the solid-pair check.',
               'linear_tolerance_mm': LINEAR_TOLERANCE_MM, 'radial_conservative_margin_mm': RADIAL_ENVELOPE_MARGIN_MM,
               'maximum_axial_interval_mm': AXIAL_SAMPLE_SPACING_MM, 'sections_per_interval': 3,
               'pairs': pairs, 'declared_contacts': exclusions,
               'limits': 'Section sampling is not a general continuous swept-volume proof. Radial margins make the generated prismatic/chamfered package conservative; geared pairs use a separate coupled-phase check. Flex uses the stated geometric wave surrogate, not an elastic deformation solution.'}]
    gears = coupled_gear_mesh(assembly, p, dimensions)
    if gears is not None:
        result.append(gears)
    return result
