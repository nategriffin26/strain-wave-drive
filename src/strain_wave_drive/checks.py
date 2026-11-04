"""Geometry checks report interference instead of treating plausible motion as proof."""
import math
from itertools import combinations
import numpy as np
from shapely.geometry import Polygon
import trimesh
from OCP.BRepAdaptor import BRepAdaptor_Surface
from .gearing import deformation, flex_points, circular_points, dynamic_points, rotate
from .parameters import Variant, preset
from .bounds import brep_bounds

OVERLAP_VOLUME_TOLERANCE = 0.01
BASELINE_OVERLAP_MARGIN_MM2 = 0.10
BASELINE_ENGAGEMENT_MARGIN_MM = 0.05


def _mesh_samples(p, flex, fixed, dynamic, angles):
    wave = deformation(p)
    fixed_cavity = Polygon(fixed)
    samples = []
    for phase in angles:
        output = -2 * phase / p.flex_teeth
        transformed = wave.forward(flex, output, phase)
        deformed = Polygon(transformed)
        dynamic_cavity = Polygon(rotate(dynamic, output))
        engagement = []
        radii = np.linalg.norm(transformed, axis=1)
        polar = np.arctan2(transformed[:, 1], transformed[:, 0])
        # An exact-axis ray can hit a root gap even when adjacent teeth engage.
        # Measure the tooth-tip envelope in a one-pitch half-width around each lobe.
        for lobe in (phase, phase + math.pi):
            delta = np.arctan2(np.sin(polar - lobe), np.cos(polar - lobe))
            selected = radii[np.abs(delta) <= 2 * math.pi / p.flex_teeth]
            radius = float(selected.max()) if len(selected) else 0.0
            engagement.append(radius - p.flex_teeth * p.module / 2)
        samples.append({"wave_angle_deg": round(math.degrees(phase), 6),
                        "fixed_overlap_mm2": deformed.difference(fixed_cavity).area,
                        "dynamic_overlap_mm2": deformed.difference(dynamic_cavity).area,
                        "major_axis_engagement_mm": engagement})
    return wave, samples


def operating_mesh(p, reference, phases=17):
    # Noncommensurate phase perturbations avoid sampling only equivalent tooth phases.
    indices = np.arange(phases)
    angles = indices * math.pi / phases + .017 * np.mod(indices * math.sqrt(2), 1)
    baseline_parameters = preset(p.variant.value)
    wave, samples = _mesh_samples(p, flex_points(p), circular_points(p), dynamic_points(p), angles)
    baseline_wave, baseline = _mesh_samples(baseline_parameters,
        np.asarray(reference[p.variant.value + "_flexspline"]),
        np.asarray(reference["circular_spline"]), np.asarray(reference["dynamic_spline"]), angles)
    area_scale = (p.flex_teeth * p.module ** 2) / (baseline_parameters.flex_teeth * baseline_parameters.module ** 2)
    length_scale = p.module / baseline_parameters.module
    passed = True
    for actual, original in zip(samples, baseline):
        overlaps_pass = all(actual[key] / area_scale <= original[key] + BASELINE_OVERLAP_MARGIN_MM2
                            for key in ("fixed_overlap_mm2", "dynamic_overlap_mm2"))
        engagement_pass = all(a / length_scale >= b - BASELINE_ENGAGEMENT_MARGIN_MM and a >= .2 * length_scale
                              for a, b in zip(actual["major_axis_engagement_mm"], original["major_axis_engagement_mm"]))
        actual["baseline"] = original
        actual["passed"] = bool(overlaps_pass and engagement_pass)
        passed = passed and actual["passed"]
    result = [{"name": "Operating mesh relative to printed prototype", "passed": bool(passed),
               "model": wave.name, "baseline_source": reference["source"],
               "overlap_margin_at_prototype_scale_mm2": BASELINE_OVERLAP_MARGIN_MM2,
               "engagement_margin_at_prototype_scale_mm": BASELINE_ENGAGEMENT_MARGIN_MM,
               "area_scale": area_scale, "length_scale": length_scale,
               "neutral_line_length_ratio": wave.length_ratio,
               "baseline_neutral_line_length_ratio": baseline_wave.length_ratio, "samples": samples,
               "basis": "Candidate and original STL sections use the same geometric surrogate. Absolute overlap is retained. The 0.10 mm² area and 0.05 mm depth margins cover section tessellation and rounded sketch dimensions. Areas normalize by tooth count × module², depths by module.",
               "limits": "Regression check only: no material stiffness, tooth compliance, local contact force, loaded backlash or fatigue prediction. Planetary contact geometry allows hoop compression; it is not an inextensible ellipse or a measured deformation field."}]
    return result


def measured_holder_preload(features, requested_preload):
    actual = features["planet_axis"]-(features["ring_pitch_radius"]-features["planet_pitch_radius"])
    return {"name": "Measured planetary holder preload", "passed": abs(actual-requested_preload) <= .02,
            "measured_holder_axis_radius_mm": features["planet_axis"],
            "measured_ring_pitch_radius_mm": features["ring_pitch_radius"],
            "measured_planet_pitch_radius_mm": features["planet_pitch_radius"],
            "measured_radial_preload_mm": actual, "requested_radial_preload_mm": requested_preload,
            "measurement_tolerance_mm": .02,
            "method": "Placed planet bearing-bore axis and gear root/tip dimensions read from BRep; standard 2.25-module whole-depth convention reconstructs pitch radii."}


def manufactured_interference(assembly):
    declarations = {frozenset((c.first, c.second)): c for c in assembly.contacts}
    bounds = {p.id: brep_bounds(p.shape) for p in assembly.parts}
    overlaps = []
    for first, second in combinations(assembly.parts, 2):
        a, b = bounds[first.id], bounds[second.id]
        if (min(a.xmax, b.xmax) - max(a.xmin, b.xmin) <= 1e-7 or
            min(a.ymax, b.ymax) - max(a.ymin, b.ymin) <= 1e-7 or
            min(a.zmax, b.zmax) - max(a.zmin, b.zmin) <= 1e-7):
            continue
        common = first.shape.intersect(second.shape)
        volume = sum(s.Volume() for s in common.Solids())
        if volume <= OVERLAP_VOLUME_TOLERANCE:
            continue
        contact = declarations.get(frozenset((first.id, second.id)))
        accepted = contact is not None and volume <= contact.maximum_overlap_mm3
        overlaps.append({"parts": [first.id, second.id], "volume_mm3": volume, "declared": contact is not None,
                         "passed": accepted, "kind": contact.kind if contact else None,
                         "reason": contact.reason if contact else "Undeclared manufactured-pose interference",
                         "maximum_declared_overlap_mm3": contact.maximum_overlap_mm3 if contact else None})
    return {"name": "Manufactured solid-pair interference", "passed": all(o["passed"] for o in overlaps),
            "pair_count": len(assembly.parts) * (len(assembly.parts) - 1) // 2,
            "volume_tolerance_mm3": OVERLAP_VOLUME_TOLERANCE, "overlaps": overlaps}


def print_mesh(part, vertices, triangles):
    mesh = trimesh.Trimesh(vertices=vertices, faces=triangles, process=True)
    bounds = mesh.bounds
    sizes = np.ptp(bounds, axis=0)
    solid_count = len(part.shape.Solids())
    passed = (part.shape.isValid() and solid_count == 1 and mesh.is_watertight and
              bool(np.all(bounds[0] >= -1e-6)) and bool(np.all(bounds[1] <= 256 + 1e-6)) and
              abs(float(bounds[0, 2])) <= 1e-6)
    return {"name": part.id, "passed": bool(passed), "valid_brep": bool(part.shape.isValid()),
            "solid_count": solid_count, "watertight": bool(mesh.is_watertight),
            "bounds_mm": bounds.tolist(), "size_mm": sizes.tolist(), "bed_z_mm": float(bounds[0, 2])}


def assembly_access(assembly, p, d):
    """Check straight hex-key envelopes at the documented assembly stages."""
    from .hardware import hex_prism
    parts = {part.id: part for part in assembly.parts}
    paths = []

    def test_path(name, tool, selected, stage):
        bounds = brep_bounds(tool)
        collisions = []
        for part in selected:
            other = brep_bounds(part.shape)
            if (min(bounds.xmax, other.xmax) <= max(bounds.xmin, other.xmin) or
                min(bounds.ymax, other.ymax) <= max(bounds.ymin, other.ymin) or
                min(bounds.zmax, other.zmax) <= max(bounds.zmin, other.zmin)):
                continue
            volume = sum(s.Volume() for s in tool.intersect(part.shape).Solids())
            if volume > OVERLAP_VOLUME_TOLERANCE:
                collisions.append({"part": part.id, "overlap_mm3": volume})
        paths.append({"name": name, "stage": stage, "passed": not collisions, "collisions": collisions})

    motor_stage = [parts[name] for name in ("motor_body", "motor_shaft", "circular_spline")]
    for index, (x, y) in enumerate([(x, y) for x in (-23.57, 23.57) for y in (-23.57, 23.57)], 1):
        tool = hex_prism(3, 3.75, 36.25).translate((x, y, 0))
        test_path(f"Motor screw {index}: 3 mm hex key", tool, motor_stage + [parts[f"motor_screw_{index}"]],
                  "Motor plate attached before cartridge; journal/bearing parked 18 mm down the motor.")
    joint_stage = [parts[name] for name in ("motor_body", "motor_shaft", "circular_spline", "journal_sleeve", "output_bearing")]
    for index, (x, y) in enumerate(((34.2509, 0), (0, 34.2509), (-34.2509, 0), (0, -34.2509)), 1):
        start = d["bearing_bottom_z_mm"] - 14
        tool = hex_prism(2.5, start, -7.8 - start).translate((x, y, 0))
        test_path(f"Housing screw {index}: 2.5 mm hex key", tool, joint_stage + [parts[f"housing_screw_{index}"]],
                  "Raise the sleeve after fastening the motor plate; enter from the side below the sleeve, above the rear connector.")
    for index in range(1, 9):
        angle = (index - 1) * math.pi / 4
        x, y = d["cap_bolt_radius_mm"] * math.cos(angle), d["cap_bolt_radius_mm"] * math.sin(angle)
        underside = d["bearing_bottom_z_mm"] - 2
        tool = hex_prism(2.5, underside - 23, 21).translate((x, y, 0))
        test_path(f"Output cap screw {index}: 2.5 mm hex key", tool, assembly.parts,
                  "Final assembly; direct access from below the rotating cap.")
    tool = hex_prism(2.5, 0, d["drum_outer_radius_mm"] + 10).rotate((0, 0, 0), (0, 1, 0), 90)
    tool = tool.translate((10, d["clamp_axis_y_mm"], d["clamp_axis_z_mm"]))
    test_path("Shaft clamp: 2.5 mm hex key through both ports", tool, assembly.parts,
              "Final assembly with service ports and shaft-clamp socket aligned.")
    cartridge = [part for part in assembly.parts if part.role in ("input", "carrier", "planet") and part.group != "motor"]
    for index in (1, 2):
        if p.variant is Variant.PLANETARY:
            x = (-1 if index == 1 else 1) * p.planet_axis_radius
            start = d["planet_axle_head_bottom_z_mm"] + .6
            tool = hex_prism(2, start, 30).translate((x, 0, 0))
        else:
            x = (-1 if index == 1 else 1) * p.roller_axis_radius
            first = d["flex_bottom_z_mm"] + (p.flex_height - 10) / 2
            tool = hex_prism(2.5, first + 14.3, 30).translate((x, 0, 0))
        test_path(f"Carrier axle {index}: top hex key", tool, cartridge,
                  "Cartridge assembled and tightened outside the output drum.")
    return {"name": "Assembly-stage hex-key access", "passed": all(path["passed"] for path in paths), "paths": paths,
            "nut_access": {"stage": "Journal/bearing parked 18 mm below final position; cartridge and output drum absent.",
                           "rear_pocket_exposed": True, "parked_journal_top_z_mm": -20,
                           "nut_z_mm": [-8.2, -5],
                           "limits": "The native motor nut recess and clocked M4 nut envelope were checked separately. The selected user's spanner/pliers jaw shape and tightening torque need a physical assembly rehearsal."},
            "limits": "Hex-key shafts and socket entry are modeled; hands, tool handles and tightening torque are not."}


def retention_stack(assembly, p, d):
    parts = {part.id: part for part in assembly.parts}
    stacks = []
    for index in (1, 2):
        prefix = f"planet_{index}_" if p.variant is Variant.PLANETARY else f"roller_{index}_"
        upper_face = (brep_bounds(parts["carrier_upper"].shape).zmin if p.variant is Variant.PLANETARY else
                      brep_bounds(parts["roller_cap"].shape).zmin)
        lower_face = (brep_bounds(parts["carrier_lower"].shape).zmax if p.variant is Variant.PLANETARY else
                      d["flex_bottom_z_mm"] + (p.flex_height - 10) / 2 - .5)
        intervals = []
        for part in assembly.parts:
            if not part.id.startswith(prefix) or not ("shim" in part.id or "spacer" in part.id or
                    part.id.endswith(("_bearing", "_lower", "_upper"))):
                continue
            bounds = brep_bounds(part.shape)
            if bounds.zmax <= upper_face + 1e-6:
                intervals.append((bounds.zmin, bounds.zmax, part.id))
        intervals.sort()
        contiguous = bool(intervals) and all(abs(a[1] - b[0]) <= 1e-6 for a, b in zip(intervals, intervals[1:]))
        complete = bool(intervals) and abs(intervals[0][0] - lower_face) <= 1e-6 and abs(intervals[-1][1] - upper_face) <= 1e-6
        stacks.append({"axis": index, "passed": bool(contiguous and complete), "intervals_mm": intervals,
                       "plate_faces_mm": [lower_face, upper_face]})
    if p.variant is Variant.PLAIN:
        return {"name": "Roller inner-race clamp stack", "passed": all(s["passed"] for s in stacks),
                "stacks": stacks, "outer_race_to_plate_gap_each_mm": .5,
                "basis": "Actual bearing/shim BRep intervals must form an unbroken stack between the two printed contact faces. The outer races remain 0.5 mm from each plate."}
    def cylinders_at(shape, radius):
        return [brep_bounds(face) for face in shape.Faces() if face.geomType() == "CYLINDER" and
                abs(BRepAdaptor_Surface(face.wrapped).Cylinder().Radius() - radius) < 1e-6]
    sun_shoulders = cylinders_at(parts["sun_gear"].shape, 8.25)
    carrier_lips = cylinders_at(parts["carrier_upper"].shape, 10)
    sun_journals = cylinders_at(parts["sun_gear"].shape, (14.9-p.bearing_offset)/2)
    if not sun_shoulders or not carrier_lips or not sun_journals:
        return {"name": "Planetary bearing trap", "passed": False, "reason": "A race-selective shoulder or the sun journal is missing.", "stacks": stacks}
    lower_shoulder = max(b.zmax for b in sun_shoulders)
    upper_shoulder = min(b.zmin for b in carrier_lips)
    bearing_width = brep_bounds(parts["carrier_bearing"].shape).zlen
    trap_gap = upper_shoulder - lower_shoulder - bearing_width
    gear_endplay = 2 * (4.2 - 4)
    relative_stack_scenario = .1
    minimum_gap = trap_gap - gear_endplay / 2 - relative_stack_scenario
    maximum_gap = trap_gap + gear_endplay / 2 + relative_stack_scenario
    journal_remaining = max(b.zlen for b in sun_journals) - maximum_gap
    top_clearance = d["roof_underside_z_mm"] - brep_bounds(parts["carrier_upper"].shape).zmax - gear_endplay / 2 - relative_stack_scenario
    middle = p.flex_height - 8
    spacer_and_shims = 6 + round((p.flex_height - 14) * 2) * .5
    tip = brep_bounds(parts["planet_1_axle"].shape).zmin
    insert_bounds = brep_bounds(parts["planet_1_axle_insert"].shape)
    engagement = insert_bounds.zmax - max(tip, insert_bounds.zmin)
    flex_bounds = brep_bounds(parts["flexspline"].shape)
    planet_bounds = [brep_bounds(parts[f"planet_gear_{index}"].shape) for index in (1,2)]
    coverage = all(abs(bounds.zmin-flex_bounds.zmin) < 1e-5 and abs(bounds.zmax-flex_bounds.zmax) < 1e-5 for bounds in planet_bounds)
    passed = (all(s["passed"] for s in stacks) and minimum_gap >= -1e-6 and journal_remaining >= 4 and top_clearance > 0 and
              abs(middle-spacer_and_shims) < 1e-8 and engagement >= 3-1e-6 and tip >= 4.5 and coverage)
    return {"name": "Planetary backstop and opposing-shoulder bearing trap", "passed": bool(passed),
            "retention_path": "Sun lower shoulder supports the 6702 inner race; upper carrier shoulder retains the outer race. Herringbone mesh locates the planet gears; bounded bearing-pocket endplay links them to the carrier. The ribbed outer seat holds its assembly position. No roof friction is used.",
            "sun_shoulder_od_mm": 16.5, "carrier_shoulder_opening_mm": 20,
            "catalog_abutment_limits": {"maximum_shaft_shoulder_od_mm": 17, "minimum_housing_shoulder_id_mm": 19,
                                        "source": "https://www.astbearings.com/catalog/thin_section_metric/6702"},
            "planet_pocket_total_endplay_mm": gear_endplay, "relative_stack_scenario_plus_minus_mm": relative_stack_scenario,
            "carrier_bearing_nominal_axial_gap_mm": trap_gap, "worst_gap_range_mm": [minimum_gap, maximum_gap],
            "minimum_remaining_journal_overlap_mm": journal_remaining, "minimum_carrier_to_roof_clearance_mm": top_clearance,
            "inner_backstop_length_mm": spacer_and_shims, "required_inner_backstop_length_mm": middle,
            "planet_center_bore_mm": 5.2, "largest_backstop_od_mm": 5,
            "axle_thread_engagement_mm": engagement, "axle_tip_z_mm": tip,
            "full_height_planet_coverage": coverage, "measured_planet_face_width_mm": [bounds.zlen for bounds in planet_bounds],
            "measured_flex_height_mm": flex_bounds.zlen,
            "stacks": stacks,
            "limits": "The ±0.1 mm relative stack scenario is a coupon scenario, not a measured printer tolerance. Confirm actual 2RS seal/face geometry and free rotation; no thrust, pullout or fatigue capacity is certified."}
