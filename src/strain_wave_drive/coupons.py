"""Thin tooth pairs and dimensioned fit plates use the same profiles as the drive."""
from dataclasses import replace
import cadquery as cq
from .assembly import Assembly, Part
from .parameters import preset
from .gearing import flex_wire
from .hardware import cylinder, d_shaft, annulus
from .printed_parts import (circular_spline_ring, crush_rib_wire, bearing_seat, bearing_journal,
                            dimensions, rounded_motor_void, shaft_clamp_hub)


def _label(text, x, y, z):
    """Raised numeric strokes avoid an external font requirement."""
    strokes = {'a': (1, 4, 2, .5), 'b': (2, 3, .5, 2), 'c': (2, 1, .5, 2),
               'd': (1, 0, 2, .5), 'e': (0, 1, .5, 2), 'f': (0, 3, .5, 2), 'g': (1, 2, 2, .5)}
    digits = {'0': 'abcdef', '1': 'bc', '2': 'abged', '3': 'abgcd', '4': 'fgbc',
              '5': 'afgcd', '6': 'afgecd', '7': 'abc', '8': 'abcdefg', '9': 'abfgcd', '-': 'g'}
    shapes = []
    for index, character in enumerate(text):
        origin = x + 3.5 * index
        if character == '.':
            shapes.append(cylinder(.3, z, .5, origin + 1, y))
        elif character == '+':
            for dx, dy, width, height in [(1, 2, 2, .5), (1, 2, .5, 2)]:
                shapes.append(cq.Workplane('XY').box(width, height, .5, centered=(True, True, False)).val().translate((origin + dx, y + dy, z)))
        else:
            for segment in digits[character]:
                dx, dy, width, height = strokes[segment]
                shapes.append(cq.Workplane('XY').box(width, height, .5, centered=(True, True, False)).val().translate((origin + dx, y + dy, z)))
    return shapes


def fits_plate(offset):
    plate = cq.Workplane('XY').rect(240, 150).extrude(2).val()
    # The large bearing gauges are short axial slices of the full-size interfaces.
    seat = bearing_seat(125, 6, offset).translate((-45, 5, 0))
    journal = bearing_journal(80, 6, offset).translate((70, 28, 0))
    seat_cut = cq.Solid.extrudeLinear(crush_rib_wire((125.6 + offset) / 2, (125.6 + offset) / 2 - .009467, True, -1), [], cq.Vector(0, 0, 8)).translate((-45, 5, 0))
    journal_cut = cylinder((79.6 - offset) / 2 - 4, -1, 8, 70, 28)
    plate = plate.fuse(seat, journal).cut(seat_cut, journal_cut)
    plate = plate.fuse(bearing_seat(21, 6, offset).translate((15, -60, 0)),
                       annulus((14.9 - offset) / 2, 3.225, 2, 4).translate((105, -45, 0)))
    for x, y in [(-100, -60), (-75, -65), (-50, -65), (-25, -65)]:
        plate = plate.fuse(cylinder(12 if x == -50 else 8, 0, 6, x, y))
    plate = plate.cut(cylinder((10.2 + offset) / 2, 1.8, 4.3, -100, -60),
                      cylinder((4.2 + offset) / 2, 1.5, 4.6, -75, -65),
                      cylinder(2.2, 1.5, 4.6, -50, -65),
                      d_shaft(6.45 + offset, 2.725 + offset / 2, -1, 8).translate((-25, -65, 0)),
                      cylinder((38.2 + offset) / 2, -1, 8, 65, -40))
    for x in (-23.57, 23.57):
        for y in (-23.57, 23.57):
            plate = plate.cut(cylinder(2.6, -1, 8, 65 + x, -40 + y))
    label_shapes = _label(f'{offset:+.1f}', -112, -42, 2) + _label('4.4', -55, -61, 6)
    return plate.fuse(*label_shapes).clean()


def build_coupons():
    assembly = Assembly('coupons')
    for row, variant in enumerate(('plain', 'planetary')):
        p = preset(variant)
        bore = cq.Wire.makeCircle(p.flex_root_radius - p.flex_wall, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
        flex = cq.Solid.extrudeLinear(flex_wire(p), [bore], cq.Vector(0, 0, 3))
        assembly.add(Part(f'tooth_flex_{variant}', f'{variant.title()} tooth-mesh flex coupon', flex.translate((0, row * 120, 0)), 'PA6', 'coupons',
                          print_note='Print flat, then ovalize this 3 mm PA6 ring into each matching rigid ring. Set tooth_offset from the least tight pair that turns without tooth skipping.'+(' This does not test the internal herringbone mesh.' if variant == 'planetary' else '')))
        for column, offset in enumerate((-.1, 0, .1)):
            trial = replace(p, tooth_offset=offset)
            ring = circular_spline_ring(trial, 49, 3)
            ring = ring.fuse(*_label(f'{offset:+.1f}', -6, 45, 3)).clean()
            slug = ('minus_' if offset < 0 else 'plus_' if offset > 0 else '') + f'{abs(offset):.1f}'.replace('.', '')
            assembly.add(Part(f'tooth_ring_{variant}_{slug}', f'{variant.title()} tooth slot offset {offset:+.1f} mm', ring.translate((120 * (column + 1), row * 120, 0)), 'CF-PLA', 'coupons',
                              print_note=f'Print flat. Pair the three {variant} rings at tooth_offset −0.1, 0 and +0.1 mm with the matching PA6 ring. Select the least tight pair that turns without tooth skipping.'))
    for index, offset in enumerate((-.1, 0, .1)):
        slug = ('minus_' if offset < 0 else 'plus_' if offset > 0 else '') + f'{abs(offset):.1f}'.replace('.', '')
        assembly.add(Part(f'fits_plate_{slug}', f'Bearing and shaft fit plate {offset:+.1f} mm', fits_plate(offset).translate((index * 260, 290, 0)), 'CF-PLA', 'coupons',
                          print_note='Print flat. Three plates test offsets −0.1, 0 and +0.1 mm: bearing_offset for the 125 mm seat, 80 mm ribbed journal, 21 mm seat and 14.9 mm journal; roller_pocket_offset for the 623 pocket; insert_offset for the insert pilot; motor_pilot_offset for the boss; shaft_offset for the D bore. The separate 4.4 mm pilot tests insert_offset=+0.2. Use the actual hardware.'))
        seat = bearing_seat(100, 6, offset)
        tab = cq.Workplane('XY').box(20, 10, 6, centered=(True, True, False)).val().translate((0, 53, 0))
        seat = seat.fuse(tab, *_label(f'{offset:+.1f}', -6, 52, 6)).clean()
        assembly.add(Part(f'output_seat_{slug}', f'6816 outer-seat offset {offset:+.1f} mm',
                          seat.translate((index * 120, 650, 0)), 'CF-PLA', 'coupons',
                          print_note='Print flat. The three short 100 mm seats test bearing_offset −0.1, 0 and +0.1 mm with the actual 6816 bearing. Ribs may compress, but the bearing must seat without splitting the ring.'))
    p = preset('planetary')
    d = dimensions(p)
    for index, clearance in enumerate((.1, .2, .3)):
        trial = replace(p, motor_clearance=clearance)
        outer = crush_rib_wire(39.8, 39.8, False)
        collar = cq.Solid.extrudeLinear(outer, [], cq.Vector(0, 0, 8)).cut(rounded_motor_void(trial, -1, 10))
        collar = collar.fuse(*_label(f'{clearance:.1f}', 29, -2, 8)).clean()
        assembly.add(Part(f'motor_corner_{index}', f'Motor journal corner clearance {clearance:.1f} mm',
                          collar.translate((index * 100, 430, 0)), 'CF-PLA', 'coupons',
                          print_note='Print flat. The three collars test motor_clearance 0.1, 0.2 and 0.3 mm. Fit an 80 mm bore bearing over each collar, then slide it over the front of the motor. Reject binding or cracked corner walls.'))
    for index, clearance in enumerate((0, .1, .2)):
        gap = p.flex_height + clearance
        lower = cq.Workplane('XY').box(36, 26, 2, centered=(True, True, False)).val()
        upper = lower.translate((0, 0, gap + 2))
        walls = [cq.Workplane('XY').box(3, 26, gap + 4, centered=(True, True, False)).val().translate((x, 0, 0)) for x in (-16.5, 16.5)]
        frame = lower.fuse(upper, *walls, *_label(f'{clearance:+.1f}', -6, -2, gap + 4)).clean()
        assembly.add(Part(f'axial_cavity_{index}', f'Flex axial cavity plus {clearance:.1f} mm',
                          frame.translate((330 + index * 45, 430, 0)), 'CF-PLA', 'coupons',
                          print_note='Print with the gap axis vertical and remove the upper-beam supports. Trial the conditioned PA6 gauge in all three frames at flex_axial_clearance 0, 0.1 and 0.2 mm; choose the smallest gap that does not bind.',
                          supports='accessible support beneath upper beam'))
    bore = cq.Wire.makeCircle(p.flex_root_radius - p.flex_wall, cq.Vector(0, 0, 0), cq.Vector(0, 0, 1))
    gauge = cq.Solid.extrudeLinear(flex_wire(p), [bore], cq.Vector(0, 0, p.flex_height))
    clip = cq.Workplane('XY').box(25, 20, p.flex_height, centered=(False, True, False)).val().translate((30, 0, 0))
    gauge = gauge.intersect(clip).clean()
    assembly.add(Part('axial_flex_gauge', 'Full-height PA6 flex-wall gauge', gauge.translate((480, 430, 0)),
                      'PA6', 'coupons', print_note='Print the 16 mm height vertically, dry and condition it like the final flex, then trial all axial frames to select flex_axial_clearance. This sector checks axial fit, not internal gear mesh.'))
    clamp = shaft_clamp_hub(p, d, d['gear_bottom_z_mm'])
    assembly.add(Part('shaft_clamp_gauge', 'Split D-shaft clamp gauge', clamp.translate((0, 550, 0)),
                      'CF-PLA', 'coupons', print_note='Print shaft-axis vertical; install one RX-M3x5x4 and an M3×16 screw. Trial the actual shaft and compare the fit plate to select shaft_offset and insert_offset. The lower round relief clears the unflattened shaft base.',
                      supports='accessible support beneath clamp bosses'))
    frame = cq.Workplane('XY').box(120, 65, 2, centered=(True, True, False)).val().translate((20, 0, 0))
    frame = frame.cut(cylinder(19.1, -1, 4))
    for x in (-23.57, 23.57):
        for y in (-23.57, 23.57):
            frame = frame.cut(cylinder(2.6, -1, 4, x, y))
    for x in (42, 53):
        wall = cq.Workplane('XY').box(2, 16, 12, centered=(True, True, False)).val().translate((x, d['clamp_axis_y_mm'], 2))
        frame = frame.fuse(wall)
    bridge_z = d['gear_bottom_z_mm'] - 2.5
    frame = frame.fuse(cylinder(2, 2, bridge_z - 2, -20, 0),
                       cq.Workplane('XY').box(6, 4, 2, centered=(False, True, False)).val().translate((-22, 0, bridge_z)),
                       annulus(18, 14.3, bridge_z, 2))
    for x in (-p.planet_axis_radius, p.planet_axis_radius):
        frame = frame.fuse(cylinder(3.8, 2, d['gear_bottom_z_mm'] - 2.5, x, 0))
    passage = cq.Solid.makeCylinder(3.05, 80, cq.Vector(0, d['clamp_axis_y_mm'], d['clamp_axis_z_mm']), cq.Vector(1, 0, 0))
    frame = frame.cut(passage).clean()
    assembly.add(Part('shaft_access_frame', 'Motor-mounted shaft-clamp access frame', frame.translate((170, 550, 0)),
                      'CF-PLA', 'coupons', print_note='Mount this frame to the motor face and place the clamp gauge on the shaft. A 2.5 mm hex key must turn freely through both 6.1 mm ports. If the key is blocked, report it: no offset fixes this.',
                      supports='accessible support beneath carrier bridge'))
    return assembly
