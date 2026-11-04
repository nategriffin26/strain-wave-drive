"""Printed gear geometry; housing dimensions are supplied by the chosen package."""
import math
import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Curve
from .bounds import brep_bounds
from . import gearing
from .hardware import cylinder, d_shaft, annulus, BEARINGS
from .parameters import Variant, DesignError


def crush_rib_wire(base_radius, rib_axis_radius, inward, z=0):
    rib_radius, count = .4, 25
    alpha = math.acos((base_radius ** 2 + rib_axis_radius ** 2 - rib_radius ** 2) / (2 * base_radius * rib_axis_radius))
    edges = []
    def point(radius, angle):
        return cq.Vector(radius * math.cos(angle), radius * math.sin(angle), z)
    for index in range(count):
        angle = index * 2 * math.pi / count
        first = point(base_radius, angle - alpha)
        last = point(base_radius, angle + alpha)
        crest = point(rib_axis_radius + (-rib_radius if inward else rib_radius), angle)
        edges.append(cq.Edge.makeThreePointArc(first, crest, last))
        next_angle = angle + 2 * math.pi / count
        edges.append(cq.Edge.makeThreePointArc(last, point(base_radius, (angle + next_angle) / 2), point(base_radius, next_angle - alpha)))
    return cq.Wire.assembleEdges(edges)


def bearing_seat(outer_diameter, height, offset=0, z=0):
    base = (outer_diameter + .6 + offset) / 2
    inner = crush_rib_wire(base, base - .009467, True, z)
    outside = cq.Wire.makeCircle(base + 4, cq.Vector(0, 0, z), cq.Vector(0, 0, 1))
    return cq.Solid.extrudeLinear(outside, [inner], cq.Vector(0, 0, height))


def bearing_journal(inner_diameter, height, offset=0, z=0):
    base = (inner_diameter - .4 - offset) / 2
    outer = crush_rib_wire(base, base, False, z)
    inside = cq.Wire.makeCircle(base - 4, cq.Vector(0, 0, z), cq.Vector(0, 0, 1))
    return cq.Solid.extrudeLinear(outer, [inside], cq.Vector(0, 0, height))


def plain_flexspline(p, z=0):
    bore = cq.Wire.makeCircle(p.flex_root_radius - p.flex_wall, cq.Vector(0, 0, z), cq.Vector(0, 0, 1))
    return cq.Solid.extrudeLinear(gearing.flex_wire(p, z), [bore], cq.Vector(0, 0, p.flex_height))


def planetary_flexspline(p, z=0):
    outer = cq.Solid.extrudeLinear(gearing.flex_wire(p, z), [], cq.Vector(0, 0, p.flex_height))
    cavity = gearing.herringbone(p.ring_teeth, p.planetary_module, p.helix_angle, p.flex_height, z, phase=math.pi / p.ring_teeth, hand=1)
    return outer.cut(cavity).clean()


def circular_spline_ring(p, outer_radius, height, z=0):
    return gearing.tooth_ring(gearing.circular_wire(p, z), outer_radius, height, z)


def dynamic_spline_ring(p, outer_radius, height, z=0):
    return gearing.tooth_ring(gearing.dynamic_wire(p, z), outer_radius, height, z)


def sun_gear(p, z=0):
    gear = gearing.herringbone(p.sun_teeth, p.planetary_module, p.helix_angle, p.flex_height, z, hand=-1)
    bore = d_shaft(6.45 + p.shaft_offset, 2.725 + p.shaft_offset / 2, z - .01, p.flex_height + .02)
    return gear.cut(bore).clean()


def planet_gear(p, z=0):
    # A half-tooth phase puts a planet space opposite the sun tooth on the centerline.
    phase = math.pi / p.planet_teeth
    gear = gearing.herringbone(p.planet_teeth, p.planetary_module, p.helix_angle, p.flex_height, z, phase=phase, hand=1)
    pocket_radius = 5.1 + p.roller_pocket_offset / 2
    lower = cylinder(pocket_radius, z - .01, 4.21)
    upper = cylinder(pocket_radius, z + p.flex_height - 4.2, 4.21)
    axle = cylinder(2.6, z - .01, p.flex_height + .02)
    return gear.cut(lower, upper, axle).clean()


def flexspline(p, z=0):
    if p.variant is Variant.PLAIN:
        return plain_flexspline(p, z)
    return planetary_flexspline(p, z)


def circle_points(radius, count, phase=0):
    return [(radius * math.cos(phase + 2 * math.pi * i / count),
             radius * math.sin(phase + 2 * math.pi * i / count)) for i in range(count)]


def rounded_motor_void(p, z, height):
    width = 57 + 2 * p.motor_clearance
    return cq.Workplane("XY").box(width, width, height, centered=(True, True, False)).edges("|Z").fillet(4 + p.motor_clearance).val().translate((0, 0, z))


def dimensions(p):
    bore, outside, width = BEARINGS[p.output_bearing.value]
    fixed_radius = max((p.circular_teeth * p.module + 10) / 2, 40)
    cap_bolt_radius = max(outside / 2 + 2.6, fixed_radius + 4.3)
    flex_floor = 11.7
    flex_bottom = flex_floor + p.flex_axial_clearance / 2
    flex_roof = flex_floor + p.flex_height + p.flex_axial_clearance
    gear_bottom = flex_bottom
    gear_top = gear_bottom+p.flex_height
    journal_radius = (79.6 - p.bearing_offset) / 2
    corner_wall = journal_radius - (math.sqrt(2) * 24.5 + 4 + p.motor_clearance)
    if corner_wall < .8:
        raise DesignError(f"motor_clearance = {p.motor_clearance:g} mm leaves journal corner wall {corner_wall:g} mm below 0.8 mm: retain two 0.4 mm extrusion widths.")
    if p.flex_teeth * p.module / 2 < 38.7:
        raise DesignError(f"flex_teeth × module / 2 = {p.flex_teeth * p.module / 2:g} mm is below 38.7 mm: the tooth band must clear the NEMA23 mounting screws and their top-access counterbores.")
    axle_length = next(length for length in (20, 25, 30, 35, 40) if length >= p.flex_height+8)
    extra_head_shims = max(0, math.ceil((axle_length - p.flex_height - 9) * 2 - 1e-9))
    axle_head_shims = 2 + extra_head_shims
    axle_head_bottom = gear_top + 2.5 + axle_head_shims * .5
    roof_under = max(flex_roof + 6.5, gear_top + 6.5,
                     axle_head_bottom + 2.5 if p.variant is Variant.PLANETARY else flex_roof + 6.5)
    return {
        "bearing_id_mm": bore, "bearing_od_mm": outside, "bearing_width_mm": width,
        "bearing_bottom_z_mm": -2 - width, "bearing_top_z_mm": -2.0,
        "fixed_outer_radius_mm": fixed_radius, "cap_bolt_radius_mm": cap_bolt_radius,
        "drum_outer_radius_mm": cap_bolt_radius + 3.8, "drum_clearance_radius_mm": fixed_radius + .5,
        "payload_bolt_radius_mm": 34.0,
        "flex_floor_z_mm": flex_floor, "flex_bottom_z_mm": flex_bottom, "flex_roof_z_mm": flex_roof,
        "fixed_band_top_z_mm": (flex_floor + flex_roof) / 2 - .2,
        "dynamic_band_bottom_z_mm": (flex_floor + flex_roof) / 2 + .2,
        "gear_bottom_z_mm": gear_bottom, "gear_top_z_mm": gear_top,
        "roof_underside_z_mm": roof_under, "roof_top_z_mm": roof_under + 3,
        "planet_axle_length_mm": axle_length, "planet_axle_head_shim_count": axle_head_shims,
        "planet_axle_head_bottom_z_mm": axle_head_bottom,
        "journal_base_radius_mm": journal_radius, "minimum_journal_corner_wall_mm": corner_wall,
        "clamp_axis_y_mm": 5.5, "clamp_axis_z_mm": 6.1,
        "motor_plate_relief_radius_mm": (p.flex_teeth - 2.5) * p.module / 2 - .6,
    }


def _drill(shape, points, radius, z, height):
    return shape.cut(*(cylinder(radius, z, height, x, y) for x, y in points))


def _service_port(shape, d):
    y, z = d["clamp_axis_y_mm"], d["clamp_axis_z_mm"]
    length = d["drum_outer_radius_mm"] + 2
    tool = cq.Solid.makeCylinder(3.05, length, cq.Vector(0, y, z), cq.Vector(1, 0, 0))
    # Matching V notches make the nested ports visibly indexable before inserting a key.
    notch = cq.Workplane("YZ").polyline([(y-.7, z+2.5), (y+.7, z+2.5), (y, z+4.1)]).close().extrude(length).val()
    return shape.cut(tool, notch)


def _exterior_chamfer(shape, z, radius, amount):
    edges = []
    for edge in shape.Edges():
        if edge.geomType() != "CIRCLE":
            continue
        circle = BRepAdaptor_Curve(edge.wrapped).Circle()
        centre = circle.Location()
        bounds = brep_bounds(edge)
        if (math.hypot(centre.X(), centre.Y()) < 1e-6 and abs(circle.Radius()-radius) < 1e-6 and
                abs(bounds.zmin-z) < 1e-6 and abs(bounds.zmax-z) < 1e-6):
            edges.append(edge)
    if not edges:
        raise DesignError("An exposed circular edge required for the print chamfer is missing.")
    return shape.Solids()[0].chamfer(amount, None, edges)


def housing_shapes(p, d):
    bottom, width = d["bearing_bottom_z_mm"], d["bearing_width_mm"]
    fixed_r, drum_r = d["fixed_outer_radius_mm"], d["drum_outer_radius_mm"]
    floor, roof = d["flex_floor_z_mm"], d["flex_roof_z_mm"]
    joint = circle_points(34.2509, 4)
    mounting = [(x, y) for x in (-23.57, 23.57) for y in (-23.57, 23.57)]

    journal = cq.Solid.extrudeLinear(crush_rib_wire(d["journal_base_radius_mm"], d["journal_base_radius_mm"], False, bottom), [], cq.Vector(0, 0, width))
    journal = journal.fuse(cylinder(42.5, bottom - 2, 2)).cut(rounded_motor_void(p, bottom - 3, width + 4))
    journal = _drill(journal, joint, 1.6, bottom - 3, width + 4)
    journal = _drill(journal, joint, 2.9, bottom - 3, -6 - (bottom - 3))

    plate = annulus(fixed_r, (38.2 + p.motor_pilot_offset) / 2, 0, floor)
    plate = plate.fuse(cylinder(42.5, -2, 2).cut(rounded_motor_void(p, -3, 4)))
    plate = plate.fuse(circular_spline_ring(p, fixed_r, d["fixed_band_top_z_mm"] - floor, floor))
    plate = plate.cut(annulus(d["motor_plate_relief_radius_mm"], (38.2 + p.motor_pilot_offset) / 2, 4, floor - 4))
    plate = _drill(plate, mounting, 2.6, -.01, floor + .02)
    plate = _drill(plate, mounting, 4.3, 1.65, floor)
    plate = _drill(plate, joint, (4.2 + p.insert_offset) / 2, -2.01, 4.51)
    plate = _service_port(plate, d)

    seat_r = (d["bearing_od_mm"] + .6 + p.bearing_offset) / 2
    outside = cq.Wire.makeCircle(drum_r, cq.Vector(0, 0, bottom), cq.Vector(0, 0, 1))
    inside = crush_rib_wire(seat_r, seat_r - .009467, True, bottom)
    drum = cq.Solid.extrudeLinear(outside, [inside], cq.Vector(0, 0, width))
    dynamic_bottom = d["dynamic_band_bottom_z_mm"]
    drum = drum.fuse(annulus(drum_r, d["drum_clearance_radius_mm"], -2, dynamic_bottom + 2),
                     dynamic_spline_ring(p, drum_r, roof - dynamic_bottom, dynamic_bottom),
                     annulus(drum_r, p.flex_root_radius - p.flex_wall - .7, roof, d["roof_underside_z_mm"] - roof),
                     cylinder(drum_r, d["roof_underside_z_mm"], 3))
    payload = circle_points(d["payload_bolt_radius_mm"], 6, math.pi / 6)
    for x, y in payload:
        drum = drum.fuse(cylinder(3.8, d["roof_top_z_mm"] - 5, 5, x, y))
    drum = _drill(drum, payload, (4.2 + p.insert_offset) / 2, d["roof_top_z_mm"] - 5, 5.01)
    cap_points = circle_points(d["cap_bolt_radius_mm"], 8)
    drum = _drill(drum, cap_points, (4.2 + p.insert_offset) / 2, bottom - .01, 5 - bottom + .01)
    drum = _drill(drum, cap_points, 1.6, 5, 8)
    drum = _service_port(drum, d)
    cap_opening = d["bearing_od_mm"] / 2 - 2
    cap = annulus(drum_r, cap_opening, bottom - 2, 2)
    cap = _drill(cap, cap_points, 1.6, bottom - 2.01, 2.02)
    plate = _exterior_chamfer(plate, -2, 42.5, .5)
    journal = _exterior_chamfer(journal, bottom-2, 42.5, .5)
    drum = _exterior_chamfer(drum, d["roof_top_z_mm"], drum_r, 1.0)
    drum = _exterior_chamfer(drum, bottom, drum_r, .5)
    cap = _exterior_chamfer(cap, bottom-2, drum_r, .4)
    cap = _exterior_chamfer(cap, bottom-2, cap_opening, .4)
    return {"circular_spline": plate.clean(), "journal_sleeve": journal.clean(),
            "dynamic_spline": drum.clean(), "output_bearing_cap": cap.clean()}


def shaft_clamp_hub(p, d, top):
    z, y = d["clamp_axis_z_mm"], d["clamp_axis_y_mm"]
    hub = cylinder(10, 2.5, top-2.5).fuse(cq.Solid.makeCone(9.5, 10, .5, cq.Vector(0, 0, 2)))
    for x in (-10, 3):
        boss = cq.Workplane("XY").box(7, 7.6, d["flex_floor_z_mm"] - (z - 3.8), centered=(False, False, False)).val().translate((x, y - 3.8, z - 3.8))
        hub = hub.fuse(boss)
    slit = cq.Workplane("XY").box(1, 15, top - 2 + .02, centered=(True, False, False)).val().translate((0, 0, 1.99))
    hub = hub.cut(slit, d_shaft(6.45 + p.shaft_offset, 2.725 + p.shaft_offset / 2, 1.99, top - 2 + .02),
                  cylinder((6.45 + p.shaft_offset) / 2, 1.99, 4.02))
    hub = hub.cut(cq.Solid.makeCylinder(1.6, 24, cq.Vector(-12, y, z), cq.Vector(1, 0, 0)),
                  cq.Solid.makeCylinder((4.2 + p.insert_offset) / 2, 7, cq.Vector(-10, y, z), cq.Vector(1, 0, 0)),
                  cq.Solid.makeCylinder(2.85, 8, cq.Vector(8, y, z), cq.Vector(1, 0, 0)))
    return hub.clean()


def planetary_carrier_shapes(p, d):
    gb, gt, axis = d["gear_bottom_z_mm"], d["gear_top_z_mm"], p.planet_axis_radius
    lower = cq.Workplane("XY").box(2 * axis, 12, 2, centered=(True, True, False)).val().translate((0, 0, gb - 2.5))
    lower = lower.cut(cylinder(14.3, gb - 3, 4)).fuse(annulus(18, 14.3, gb - 2.5, 2))
    for x in (-axis, axis):
        lower = lower.fuse(cylinder(3.8, gb - 5.5, 5, x, 0))
    lower = _drill(lower, [(-axis, 0), (axis, 0)], (4.2 + p.insert_offset) / 2, gb - 5.51, 5.02)
    top = cq.Workplane("XY").box(2 * axis + 8, 12, 2, centered=(True, True, False)).val().translate((0, 0, gt + .5))
    top = top.fuse(cylinder(12.5, gt + .5, 5.3))
    pocket_r = (21.6 + p.bearing_offset) / 2
    pocket = cq.Solid.extrudeLinear(crush_rib_wire(pocket_r, pocket_r - .009467, True, gt + .49), [], cq.Vector(0, 0, 4.51))
    top = top.cut(pocket, cylinder(10, gt + 5, .81))
    top = _drill(top, [(-axis, 0), (axis, 0)], 1.6, gt + .49, 2.02)
    sun = sun_gear(p, gb).fuse(shaft_clamp_hub(p, d, gb + .01))
    sun = sun.fuse(annulus(8.25, 3.225 + p.shaft_offset / 2, gt, .5),
                   annulus((14.9 - p.bearing_offset) / 2, 3.225 + p.shaft_offset / 2, gt + .5, 5))
    return {"sun_gear": sun.clean(), "carrier_lower": lower.clean(), "carrier_upper": top.clean()}


def plain_carrier_shapes(p, d):
    axis = p.roller_axis_radius
    lower_bearing = d["flex_bottom_z_mm"] + (p.flex_height - 10) / 2
    lower_face, upper_face = lower_bearing - .5, lower_bearing + 10.5
    hub = shaft_clamp_hub(p, d, upper_face)
    arms = cq.Workplane("XY").box(2 * axis, 8.4, 2, centered=(True, True, False)).val().translate((0, 0, lower_face - 2))
    upper = cq.Workplane("XY").box(2 * axis, 8.4, 2, centered=(True, True, False)).val().translate((0, 0, upper_face))
    for x in (-axis, axis):
        arms = arms.fuse(cylinder(4.2, lower_face - 4, 4, x, 0))
        upper = upper.fuse(cylinder(4.2, upper_face, 2, x, 0))
    arms = arms.cut(d_shaft(6.45 + p.shaft_offset, 2.725 + p.shaft_offset / 2, lower_face - 2.01, 2.02))
    hub = hub.fuse(arms)
    hub = _drill(hub, [(-axis, 0), (axis, 0)], (4.2 + p.insert_offset) / 2, lower_face - 4.01, 4.02)
    upper = _drill(upper, [(-axis, 0), (axis, 0)], 1.6, upper_face - .01, 2.02)
    return {"roller_hub": hub.clean(), "roller_cap": upper.clean()}
