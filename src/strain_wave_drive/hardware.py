"""Nominal catalog envelopes; threads and bearing internals are simplified."""
import math
import cadquery as cq
from .assembly import CatalogItem, Part
from .parameters import DesignError

MOTOR_CATALOG = "https://www.omc-stepperonline.com/nema-23-bipolar-1-8deg-1-26nm-178-4oz-in-2-8a-2-5v-57x57x56mm-4-wires-23hs22-2804s"
INSERT_CATALOG = "https://www.ruthex.de/en/products/ruthex-gewindeeinsatz-m3-100-stuck-made-for-voron-rx-m3x5x4-messing-gewindebuchsen-fur-3d-druck"
BEARINGS = {"6016": (80.0, 125.0, 22.0), "6816-2RS": (80.0, 100.0, 10.0),
            "623ZZ": (3.0, 10.0, 4.0), "6702-2RS": (15.0, 21.0, 4.0)}
CAP_HEADS = {3: (5.5, 3.0, 2.5, 1.3), 4: (7.0, 4.0, 3.0, 2.0)}
BEARING_SOURCES = {
    "6816-2RS": ("VXB", "6816-2RS", "https://vxb.com/products/6816-2rs-sealed-bearing-80x100x10"),
    "6016": ("McMaster-Carr", "5972K251", "https://www.mcmaster.com/5972K251/"),
    "6702-2RS": ("RCBearings", "MDD-6702-2RS", "https://rcbearings.com/product/113/"),
    "623ZZ": ("RCBearings", "MDC-623-ZZ", "https://rcbearings.com/product/349/"),
}
CAP_SCREW_PART_NUMBERS = {(4, 12): "91290A148", (3, 8): "91290A113", (3, 16): "91290A120",
                         (3, 20): "91290A123", (3, 30): "91290A130"}


def cylinder(radius, z, height, x=0, y=0):
    return cq.Solid.makeCylinder(radius, height, cq.Vector(x, y, z))


def annulus(outer_radius, inner_radius, z, height):
    outer = cq.Wire.makeCircle(outer_radius, cq.Vector(0, 0, z), cq.Vector(0, 0, 1))
    inner = cq.Wire.makeCircle(inner_radius, cq.Vector(0, 0, z), cq.Vector(0, 0, 1))
    return cq.Solid.extrudeLinear(outer, [inner], cq.Vector(0, 0, height))


def d_shaft(diameter, flat_x, z, height):
    disk = cylinder(diameter / 2, z, height)
    outside = cq.Workplane("XY").box(diameter, 2 * diameter, height + 2, centered=(False, True, False)).val().translate((flat_x, 0, z - 1))
    return disk.cut(outside)


def hex_prism(across_flats, z, height):
    return cq.Workplane("XY", origin=(0, 0, z)).polygon(6, across_flats / math.cos(math.pi / 6)).extrude(height).val()


def bearing(designation, part_id, label, z=0):
    if designation not in BEARINGS:
        raise DesignError(f"bearing = {designation!r} is outside the supported catalog: choose {', '.join(BEARINGS)}.")
    bore, outside, width = BEARINGS[designation]
    shape = annulus(outside / 2, bore / 2, z, width)
    vendor, number, link = BEARING_SOURCES[designation]
    item = CatalogItem(designation, f"{bore:g} × {outside:g} × {width:g} mm", link,
                       "Nominal mounting envelope; races, shields and rolling elements are not separately modeled.", vendor, number)
    return Part(part_id, label, shape, "steel", "bearings", item)


def cap_screw(diameter, length, part_id, label, underside_z=0):
    if diameter not in CAP_HEADS:
        raise DesignError(f"screw diameter = {diameter!r} is outside M3/M4: no verified head dimensions are supplied for this size.")
    head_diameter, head_height, socket, socket_depth = CAP_HEADS[diameter]
    shape = cylinder(diameter / 2, underside_z - length, length).fuse(cylinder(head_diameter / 2, underside_z, head_height))
    shape = shape.cut(hex_prism(socket, underside_z + head_height - socket_depth, socket_depth + .01))
    number = CAP_SCREW_PART_NUMBERS.get((diameter, length))
    item = CatalogItem(f"ISO 4762 M{diameter}×{length:g}", f"Head Ø{head_diameter:g} × {head_height:g}; socket {socket:g}; under-head length {length:g} mm",
                       f"https://www.mcmaster.com/{number}/" if number else "",
                       "Smooth major-diameter thread envelope; length excludes head." + ("" if number else " Supplier SKU for this non-default length is unverified."),
                       "McMaster-Carr" if number else "", number or "")
    return Part(part_id, label, shape, "steel", "fasteners", item)




def insert(part_id, label, z=0):
    # Maximum knurl envelope, relieved center band and entry pilot; no decorative helical facets.
    shape = annulus(2.5, 1.25, z + .3, 3.4).fuse(annulus(2.125, 1.25, z, .3), annulus(2.125, 1.25, z + 3.7, .3))
    item = CatalogItem("Ruthex RX-M3x5x4", "M3; maximum Ø5; entry Ø4.25; length 4 mm", INSERT_CATALOG,
                       "Manufacturer recommends Ø4.4 pilot; prototype Ø4.2 is retained pending the coupon. US source: https://www.fabreeko.com/products/threaded-heat-inserts-m3x5x4-100pc-per-bag", "ruthex.de", "RX-M3x5x4")
    return Part(part_id, label, shape, "brass", "fasteners", item)


def motor_parts():
    # Native front-body section: convex R4 transitions surround the open R4 nut recesses.
    diagonal = 4 / math.sqrt(2)
    segments = [
        ((28.5, -15.57), None, (28.5, 15.57)),
        ((28.5, 15.57), (24.5 + diagonal, 15.57 + diagonal), (24.5, 19.57)),
        ((24.5, 19.57), None, (23.57, 19.57)),
        ((23.57, 19.57), (23.57 - diagonal, 23.57 - diagonal), (19.57, 23.57)),
        ((19.57, 23.57), None, (19.57, 24.5)),
        ((19.57, 24.5), (15.57 + diagonal, 24.5 + diagonal), (15.57, 28.5)),
    ]
    edges = []
    for quarter in range(4):
        c, s = math.cos(quarter * math.pi / 2), math.sin(quarter * math.pi / 2)
        def point(xy):
            return cq.Vector(c * xy[0] - s * xy[1], s * xy[0] + c * xy[1], -56)
        for first, middle, last in segments:
            edges.append(cq.Edge.makeLine(point(first), point(last)) if middle is None else
                         cq.Edge.makeThreePointArc(point(first), point(middle), point(last)))
    body = cq.Solid.extrudeLinear(cq.Wire.assembleEdges(edges), [], cq.Vector(0, 0, 51))
    flange = cq.Workplane("XY").box(57, 57, 5, centered=(True, True, False)).edges("|Z").fillet(4).val().translate((0, 0, -5))
    body = body.fuse(flange)
    for x in (-23.57, 23.57):
        for y in (-23.57, 23.57):
            body = body.cut(cylinder(2.55, -5, 5.01, x, y))
    body = body.fuse(annulus(19.05, 3.175, 0, 1.6)).clean()
    shaft = cq.Workplane("XY").circle(3.175).extrude(21).edges(">Z").chamfer(.5).val()
    flat = cq.Workplane("XY").box(8, 10, 16, centered=(False, True, False)).val().translate((2.675, 0, 6))
    shaft = shaft.cut(flat)
    item = CatalogItem("23HS22-2804S", "57×57×56 body; Ø38.1×1.6 pilot; 47.14 bolt square; Ø6.35×21 D shaft", MOTOR_CATALOG,
                       "Manufacturer lists 1.24 Nm holding torque. Body and shaft are separate motion nodes but one motor; connector and leads are omitted. Shaft flat X=2.675 begins at Z=6; the full-diameter shaft ends at Z=20.5 mm.", "StepperOnline", "23HS22-2804S")
    return (Part("motor_body", "NEMA 23 motor body", body, "motor", "motor", item),
            Part("motor_shaft", "NEMA 23 motor shaft", shaft, "steel", "motor", role="input", catalog_owner="motor_body"))


def low_head_screw(length, part_id, label, underside_z=0):
    shape = cylinder(1.5, underside_z - length, length).fuse(cylinder(2.75, underside_z, 2))
    shape = shape.cut(hex_prism(2, underside_z + .5, 1.51))
    item = CatalogItem(f"DIN 7984 M3×{length:g}", f"Head Ø5.5×2; socket 2; under-head length {length:g} mm",
                       "https://accu-components.com/us/low-head-cap-screws/8730-SSCL-M3-25-A2" if length == 25 else "https://accu-components.com/us/shop/precision-components/precision-screws/cap-head-screws/low-head-cap-screws",
                       "M3×25 has at least 12 mm of thread." if length == 25 else "Select the stated DIN 7984 M3 length from the vendor family; standard designation is exact, but a vendor SKU has not been verified.",
                       "Accu", "SSCL-M3-25-A2" if length == 25 else "")
    return Part(part_id, label, shape, "steel", "fasteners", item)


def shim(part_id, label, z=0):
    item = CatalogItem("DIN 988 3×5×0.5", "ID 3; OD 5; thickness 0.5 mm",
                       "https://kb-3d.com/store/inserts-fasteners-adhesives/3009-3x5x05mm-shim-ring-washer-pack-of-50-din988-1635423113345.html",
                       "Contacts bearing inner races only.", "KB-3D", "SS-3505-50")
    return Part(part_id, label, annulus(2.5, 1.5, z, .5), "steel", "fasteners", item)


def spacer(part_id, label, z=0):
    item = CatalogItem("RAF M0505-3-B", "ID 3.2; OD 4.5; length 6 mm",
                       "https://www.mwcomponents.com/shop/m0505-3-b",
                       "Catalog brass spacer; no cutting or turning required.", "MW Components / RAF", "M0505-3-B")
    return Part(part_id, label, annulus(2.25, 1.6, z, 6), "brass", "fasteners", item)


def motor_nut(part_id, label, z=-8.2, angle=40):
    shape = hex_prism(7, z, 3.2).cut(cylinder(1.65, z - .01, 3.22))
    shape = shape.rotate((0, 0, 0), (0, 0, 1), angle)
    item = CatalogItem("ISO 4032 M4", "Across flats 7 mm; height 3.2 mm; M4×0.7",
                       "https://www.mcmaster.com/90593A003/",
                       "Maximum hex corners nearly touch the motor recess. Clock the nut 5° from the corner diagonal and trial the actual nut/tool before printing the housing.", "McMaster-Carr", "90593A003")
    return Part(part_id, label, shape, "steel", "fasteners", item)
