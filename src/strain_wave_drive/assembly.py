"""Named physical parts and declared mating contacts in motor-face coordinates."""
from dataclasses import dataclass, field
from typing import Literal
import cadquery as cq
from .bounds import brep_bounds

Material = Literal["PA6", "CF-PLA", "steel", "brass", "motor"]

# The render palette, expressed as display-referred sRGB for CadQuery/XCAF.
MATERIAL_COLORS_SRGB = {name: tuple(channel/255 for channel in color) for name, color in {
    "printed_accent": (43, 74, 111), "printed": (59, 61, 64), "flex": (179, 22, 28),
    "steel": (167, 173, 180), "brass": (224, 180, 92), "motor": (20, 21, 23),
}.items()}


@dataclass(frozen=True)
class CatalogItem:
    designation: str
    dimensions: str
    link: str
    note: str = ""
    vendor: str = ""
    part_number: str = ""


@dataclass
class Part:
    id: str
    label: str
    shape: cq.Shape
    material: Material
    group: str
    catalog: CatalogItem | None = None
    print_rotation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    print_note: str = "Flat face on the bed; axis vertical."
    supports: str = "none"
    role: Literal["ground", "input", "carrier", "planet", "flex", "output"] = "ground"
    pivot_mm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    catalog_owner: str | None = None

    @property
    def printed(self):
        return self.material in ("PA6", "CF-PLA")

    @property
    def render_material(self):
        if self.material == "CF-PLA" and self.id in ("circular_spline", "journal_sleeve", "dynamic_spline", "output_bearing_cap"):
            return "printed_accent"
        return {"PA6": "flex", "CF-PLA": "printed", "steel": "steel", "brass": "brass", "motor": "motor"}[self.material]


@dataclass(frozen=True)
class Contact:
    first: str
    second: str
    kind: Literal["crush_rib", "preload", "heat_set_insert", "screw_thread"]
    reason: str
    maximum_overlap_mm3: float
    nominal_overlap_mm3: float | None = None


@dataclass
class Assembly:
    name: str
    parts: list[Part] = field(default_factory=list)
    contacts: list[Contact] = field(default_factory=list)

    def add(self, part):
        if any(existing.id == part.id for existing in self.parts):
            raise ValueError(f"Duplicate physical part id: {part.id}")
        self.parts.append(part)
        return part

    def contact(self, first, second, kind, reason, maximum_overlap_mm3, nominal_overlap_mm3=None):
        self.contacts.append(Contact(first, second, kind, reason, maximum_overlap_mm3, nominal_overlap_mm3))

    def cadquery(self):
        colors = {name: cq.Color(*color) for name, color in MATERIAL_COLORS_SRGB.items()}
        assembly = cq.Assembly(name=self.name)
        for part in self.parts:
            assembly.add(part.shape, name=part.label, color=colors[part.render_material])
        return assembly


def build(parameters):
    """Assemble the selected split Slim-wrap package without custom metal parts."""
    import math
    from . import hardware as hw
    from . import printed_parts as printed
    from .parameters import Variant
    from . import contact_limits as limits
    p = parameters.validate()
    d = printed.dimensions(p)
    assembly = Assembly(p.variant.value)

    def mating_contact(first, second, kind, reason, nominal):
        assembly.contact(first, second, kind, reason, limits.allowance(nominal), nominal)

    def add(part, role="ground", x=0, y=0, pivot=None):
        if x or y:
            part.shape = part.shape.translate((x, y, 0))
        part.role = role
        if part.group == "motor" or part.id.startswith(("motor_screw_", "motor_nut_")):
            part.group = "motor"
        elif part.id == "output_bearing":
            part.group = "ground"
        elif part.id == "carrier_bearing":
            part.group = "input"
        else:
            part.group = role
        if pivot is not None:
            part.pivot_mm = pivot
        return assembly.add(part)

    def printed_part(part_id, label, shape, role, rotation=(0, 0, 0), supports="none", note="Flat face on the bed; axis vertical."):
        return add(Part(part_id, label, shape, "PA6" if part_id == "flexspline" else "CF-PLA",
                        role, print_rotation=rotation, print_note=note, supports=supports), role)

    def insert(part_id, label, owner, x, y, z, role):
        part = add(hw.insert(part_id, label, z), role, x, y)
        mating_contact(part.id, owner, "heat_set_insert", "Nominal insert/pilot overlap, scaled with insert_offset; 5% plus 0.05 mm³ margin.", limits.insert_overlap(p.insert_offset))
        return part

    def thread(screw, receiver):
        axis = "x" if screw.id == "shaft_clamp_screw" else "z"
        first, second = brep_bounds(screw.shape), brep_bounds(receiver.shape)
        engagement = max(0, min(getattr(first,axis+"max"),getattr(second,axis+"max"))-max(getattr(first,axis+"min"),getattr(second,axis+"min")))
        major, minor = (2, 1.65) if receiver.catalog.designation == "ISO 4032 M4" else (1.5, 1.25)
        nominal = math.pi*(major*major-minor*minor)*engagement
        mating_contact(screw.id, receiver.id, "screw_thread", "Nominal thread annulus over the measured axial engagement; 5% plus 0.05 mm³ margin.", nominal)

    def upward_screw(length, part_id, label, underside, x, y, role):
        part = hw.cap_screw(3, length, part_id, label)
        part.shape = part.shape.rotate((0, 0, 0), (1, 0, 0), 180).translate((0, 0, underside))
        return add(part, role, x, y)

    for part in hw.motor_parts():
        add(part, "input" if part.id == "motor_shaft" else "ground")
    shapes = printed.housing_shapes(p, d)
    printed_part("circular_spline", "Circular spline and motor plate", shapes["circular_spline"], "ground",
                 note="Motor mounting face down; bearing shoulder below it needs accessible bed support.", supports="bed-only beneath motor-plate overhang")
    printed_part("journal_sleeve", "Motor-wrap journal with lower inner-race shoulder", shapes["journal_sleeve"], "ground")
    printed_part("dynamic_spline", "Dynamic spline and output drum", shapes["dynamic_spline"], "output", rotation=(180, 0, 0),
                 note="Closed roof on the bed, open bearing skirt upward.")
    printed_part("output_bearing_cap", "Output bearing lower outer-race cap", shapes["output_bearing_cap"], "output")
    output_bearing = add(hw.bearing(p.output_bearing.value, "output_bearing", "Output bearing " + p.output_bearing.value,
                                   d["bearing_bottom_z_mm"]), "output")
    mating_contact(output_bearing.id, "journal_sleeve", "crush_rib", "Measured journal-rib area scaled by bearing width and configured rib penetration.", limits.JOURNAL_RIB_AREA*d["bearing_width_mm"]*limits.rib_area_scale(.2,p.bearing_offset))
    mating_contact(output_bearing.id, "dynamic_spline", "crush_rib", "Measured outer-seat rib area scaled by bearing width and configured rib penetration.", limits.OUTER_SEAT_RIB_AREA*d["bearing_width_mm"]*limits.rib_area_scale(.109467,p.bearing_offset))

    for index, (x, y) in enumerate([(x, y) for x in (-23.57, 23.57) for y in (-23.57, 23.57)], 1):
        screw = add(hw.cap_screw(4, 12, f"motor_screw_{index}", f"Motor mounting screw {index}", 1.65), x=x, y=y)
        angle = math.degrees(math.atan2(y, x)) - 5
        nut = add(hw.motor_nut(f"motor_nut_{index}", f"Motor mounting nut {index}", angle=angle), x=x, y=y)
        thread(screw, nut)
    for index, (x, y) in enumerate(printed.circle_points(34.2509, 4), 1):
        receiver = insert(f"housing_insert_{index}", f"Motor-plate joint insert {index}", "circular_spline", x, y, -1.75, "ground")
        screw = upward_screw(8, f"housing_screw_{index}", f"Journal-to-motor-plate screw {index}", -6, x, y, "ground")
        thread(screw, receiver)
    cap_under = d["bearing_bottom_z_mm"] - 2
    cap_length = next(length for length in (8, 10, 12, 16, 20, 25, 30, 35, 40) if cap_under + length >= 3)
    d["output_cap_screw_length_mm"] = cap_length
    for index, (x, y) in enumerate(printed.circle_points(d["cap_bolt_radius_mm"], 8), 1):
        receiver = insert(f"output_cap_insert_{index}", f"Output cap insert {index}", "dynamic_spline", x, y, 0, "output")
        screw = upward_screw(cap_length, f"output_cap_screw_{index}", f"Output cap screw {index}", cap_under, x, y, "output")
        thread(screw, receiver)
    for index, (x, y) in enumerate(printed.circle_points(d["payload_bolt_radius_mm"], 6, math.pi / 6), 1):
        insert(f"payload_insert_{index}", f"Output attachment insert {index}", "dynamic_spline", x, y, d["roof_top_z_mm"] - 5, "output")

    flex = printed_part("flexspline", "PA6 flexspline", printed.flexspline(p, d["flex_bottom_z_mm"]), "flex",
                        note="Axis vertical; dry PA6. The print mesh is the neutral circular ring, never its operating deformation.")
    scale = p.flex_teeth/120*(p.module/.7)**2*(d["fixed_band_top_z_mm"]-d["flex_floor_z_mm"])/7.8
    mating_contact(flex.id, "circular_spline", "preload", "Measured prototype-sized neutral-flex overlap scaled by tooth count, module squared and band height.", limits.FIXED_FLEX_BASELINE[p.variant.value]*scale)
    mating_contact(flex.id, "dynamic_spline", "preload", "Aligned neutral flex and dynamic spline have zero measured default overlap; operating mesh is checked separately.", 0)

    if p.variant is Variant.PLANETARY:
        cartridge = printed.planetary_carrier_shapes(p, d)
        printed_part("sun_gear", "Sun gear and split shaft clamp", cartridge["sun_gear"], "input",
                     supports="accessible bed-only support below gear and clamp", note="Shaft axis vertical; lower hub end on bed.")
        printed_part("carrier_lower", "Lower planetary carrier", cartridge["carrier_lower"], "carrier",
                     supports="accessible bed-only support beneath bridge", note="Insert bosses on bed; remove bridge supports before installing inserts.")
        printed_part("carrier_upper", "Upper planetary carrier and bearing shoulder", cartridge["carrier_upper"], "carrier")
        support = add(hw.bearing("6702-2RS", "carrier_bearing", "Carrier-to-sun radial bearing", d["gear_top_z_mm"] + .5), "carrier")
        mating_contact(support.id, "carrier_upper", "crush_rib", "Measured 6702 rib-seat area scaled by configured penetration.", 4*limits.CARRIER_SEAT_RIB_AREA*limits.rib_area_scale(.109467,p.bearing_offset))
        planet_shape = printed.planet_gear(p, d["gear_bottom_z_mm"])
        gb, gt = d["gear_bottom_z_mm"], d["gear_top_z_mm"]
        middle_shims = round((p.flex_height - 14) * 2)
        lower_middle = middle_shims // 2
        upper_middle = middle_shims - lower_middle
        spacer_z = gb + 4 + .5 * lower_middle
        for index, x in enumerate((-p.planet_axis_radius, p.planet_axis_radius), 1):
            pivot = (x, 0.0, (gb + gt) / 2)
            planet = printed_part(f"planet_gear_{index}", f"Planet gear {index}", planet_shape.translate((x, 0, 0)), "planet")
            planet.pivot_mm = pivot
            nominal = limits.PLANET_FLEX_BASELINE*(p.flex_height/16)*(p.planetary_module/.804)**2*(p.planet_preload/.105381480006292)**1.5
            mating_contact(planet.id, flex.id, "preload", "Measured default planet/flex overlap scaled by face width, module squared and configured preload.", nominal)
            for suffix, z in (("lower", gb), ("upper", gt - 4)):
                add(hw.bearing("623ZZ", f"planet_{index}_{suffix}_bearing", f"Planet {index} {suffix} bearing", z), "planet", x=x, pivot=pivot)
            add(hw.spacer(f"planet_{index}_spacer", f"Planet {index} inner-race spacer", spacer_z), "carrier", x=x)
            shim_z = ([gb - .5] + [gb + 4 + .5 * j for j in range(lower_middle)] +
                      [spacer_z + 6 + .5 * j for j in range(upper_middle)] + [gt] +
                      [gt + 2.5 + .5 * j for j in range(d["planet_axle_head_shim_count"])])
            for number, z in enumerate(shim_z, 1):
                add(hw.shim(f"planet_{index}_shim_{number}", f"Planet {index} inner-race shim {number}", z), "carrier", x=x)
            receiver = insert(f"planet_{index}_axle_insert", f"Planet {index} axle insert", "carrier_lower", x, 0, gb - 5.5, "carrier")
            screw = add(hw.low_head_screw(d["planet_axle_length_mm"], f"planet_{index}_axle", f"Planet {index} axle screw",
                                         d["planet_axle_head_bottom_z_mm"]), "carrier", x=x)
            thread(screw, receiver)
        clamp_owner, clamp_role = "sun_gear", "input"
    else:
        cartridge = printed.plain_carrier_shapes(p, d)
        printed_part("roller_hub", "Direct-drive roller hub and shaft clamp", cartridge["roller_hub"], "carrier",
                     supports="accessible bed-only support beneath arms", note="Shaft axis vertical; lower clamp end on bed.")
        printed_part("roller_cap", "Roller axle cap", cartridge["roller_cap"], "carrier")
        first_bearing = d["flex_bottom_z_mm"] + (p.flex_height - 10) / 2
        lower_face, upper_face = first_bearing - .5, first_bearing + 10.5
        for index, x in enumerate((-p.roller_axis_radius, p.roller_axis_radius), 1):
            pivot = (x, 0.0, first_bearing + 5)
            for suffix, z in (("lower", first_bearing), ("upper", first_bearing + 6)):
                bearing = add(hw.bearing("623ZZ", f"roller_{index}_{suffix}", f"Roller {index} {suffix} bearing", z), "planet", x=x, pivot=pivot)
                mating_contact(bearing.id, flex.id, "preload", "Nominal circular roller/bore intersection at configured preload; 5% plus 0.05 mm³ margin.", limits.roller_overlap(p))
            shim_z = [lower_face] + [first_bearing + 4 + .5 * j for j in range(4)] + [first_bearing + 10]
            for number, z in enumerate(shim_z, 1):
                add(hw.shim(f"roller_{index}_shim_{number}", f"Roller {index} inner-race shim {number}", z), "carrier", x=x)
            receiver = insert(f"roller_{index}_axle_insert", f"Roller {index} axle insert", "roller_hub", x, 0, lower_face - 4, "carrier")
            screw = add(hw.cap_screw(3, 20, f"roller_{index}_axle", f"Roller {index} axle screw", upper_face + 2), "carrier", x=x)
            thread(screw, receiver)
        clamp_owner, clamp_role = "roller_hub", "carrier"

    clamp_screw = hw.cap_screw(3, 16, "shaft_clamp_screw", "Shaft clamp screw")
    clamp_screw.shape = clamp_screw.shape.rotate((0, 0, 0), (0, 1, 0), 90).translate((8, d["clamp_axis_y_mm"], d["clamp_axis_z_mm"]))
    add(clamp_screw, clamp_role)
    receiver = hw.insert("shaft_clamp_insert", "Shaft clamp insert")
    receiver.shape = receiver.shape.rotate((0, 0, 0), (0, 1, 0), 90).translate((-8, d["clamp_axis_y_mm"], d["clamp_axis_z_mm"]))
    add(receiver, clamp_role)
    mating_contact(receiver.id, clamp_owner, "heat_set_insert", "Nominal insert/pilot overlap; flat boss retains 1.3 mm radial wall.", limits.insert_overlap(p.insert_offset))
    thread(clamp_screw, receiver)
    d["assembly_order"] = [
        "Print the bearing, motor-corner, shaft-access and axial-height coupons; select offsets before printing the drive.",
        "Heat-set all inserts. Slide the output bearing onto the journal sleeve from its open upper end; its lower inner-race shoulder is integral.",
        "Slide the sleeve/bearing over the front of the motor and park it 18 mm below its final position. Do not pass it over the rear connector.",
        "Fasten the circular-spline motor plate with four M4×12 cap screws from above. Hold each exposed rear-flange nut 5° from its corner diagonal before raising the sleeve.",
        "Raise the sleeve to the motor plate and tighten its four M3×8 screws from below through the Ø5.8 counterbores.",
        (f"Each planet axle, bottom to top: lower carrier; 1 shim; lower 623ZZ bearing; {lower_middle} shims; one 6 mm RAF spacer; {upper_middle} shims; upper 623ZZ bearing; 1 shim; upper carrier; {d['planet_axle_head_shim_count']} shims; DIN 7984 M3×{d['planet_axle_length_mm']:g} screw head. All {2+lower_middle+upper_middle+d['planet_axle_head_shim_count']} shims per axle are DIN 988 3×5×0.5 mm. Fit the spacer and its internal shims before seating the second bearing." if p.variant is Variant.PLANETARY else
         "Stack each roller pair with four middle shims and one end shim at each carrier plate, contacting only the bearing inner races."),
        ("Slide the 6702 onto the sun shoulder, then assemble the sun, planets and carrier plates outside the drum. The carrier's outer-race shoulder sits above the bearing. Tighten the axle screws against the metal stacks and check free rotation." if p.variant is Variant.PLANETARY else
         "Fit the roller axle cap, tighten the screws against the metal stacks, and check that all four rollers spin freely."),
        "Place the drum roof-down. Install the flex and assembled carrier through its open bottom, then lower the motor assembly in.",
        "Enter the bearing into the drum's ribbed seat; fit the rotating lower outer-race cap and tighten its eight screws from below.",
        "With the hex key removed, jog the motor slowly until the V-notched ports and clamp socket line up. Disconnect motor power before inserting the key and tightening the clamp.",
        "Attach the payload using the six M3 inserts on a 68 mm pitch circle. Hand-turn through a complete cycle and verify no binding before applying power.",
    ]
    return assembly, d


def motion(assembly, parameters):
    import math
    import numpy as np
    from .gearing import deformation
    from .parameters import Variant
    p = parameters
    wave = deformation(p)
    angles = np.linspace(0, 2 * math.pi, 256, endpoint=False)
    direction = np.column_stack((np.cos(angles), np.sin(angles)))
    neutral = wave.forward(direction * wave.radius)
    normal = wave.forward(direction * (wave.radius + 1)) - neutral
    carrier_divisor = -p.ratio / (p.flex_teeth / 2)
    ratios = {"motor_to_output": p.ratio, "motor_to_carrier": carrier_divisor,
              "motor_to_planet": None, "motor_to_roller": None}
    if p.variant is Variant.PLANETARY:
        spin_rate = (1 + p.sun_teeth / p.planet_teeth) / carrier_divisor - p.sun_teeth / p.planet_teeth
        ratios["motor_to_planet"] = 1 / spin_rate
    else:
        epsilon = 1e-5
        bore = p.flex_root_radius - p.flex_wall
        points = bore * np.array([[math.cos(-epsilon), math.sin(-epsilon)], [math.cos(epsilon), math.sin(epsilon)]])
        contact = wave.forward(points)
        effective_radius = (contact[1, 1] - contact[0, 1]) / (2 * epsilon)
        spin_rate = 1 + effective_radius / 5 * (-2 / p.flex_teeth - 1)
        ratios["motor_to_roller"] = 1 / spin_rate
    parts = []
    for part in assembly.parts:
        parts.append({"id": part.id, "label": part.label, "group": part.group, "material": part.render_material,
                      "role": part.role, "pivot_mm": list(part.pivot_mm), "axis": [0, 0, 1],
                      "printable": part.printed, "catalog_owner": part.catalog_owner})
    bounds = [brep_bounds(part.shape) for part in assembly.parts]
    bounds_mm = [[min(getattr(b, axis + "min") for b in bounds) for axis in "xyz"],
                 [max(getattr(b, axis + "max") for b in bounds) for axis in "xyz"]]
    return {"variant": p.variant.value, "units": "mm", "bounds_mm": bounds_mm, "ratios": ratios,
            "ratio_convention": "Signed divisors: member world angle = motor angle / divisor.",
            "wave": {"model": "sampled_neutral_curve", "shape_model": wave.name,
                     "neutral_radius_mm": wave.radius,
                     "lift_mm": p.roller_preload if p.variant is Variant.PLAIN else p.planet_preload,
                     "axis_deg": 0, "samples": np.column_stack((neutral, normal)).round(9).tolist(),
                     "neutral_line_length_ratio": wave.length_ratio,
                     "limits": "Geometric illustration and prototype-relative regression surrogate; not a measured elastic/contact solution."},
            "parts": parts}
