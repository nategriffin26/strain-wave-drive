"""Millimetre inputs, dimensional limits and ideal no-slip kinematics."""
from dataclasses import asdict, dataclass, field, fields, replace
from enum import Enum
import math


class DesignError(ValueError):
    """A requested dimension cannot produce this mechanical arrangement."""


class Variant(str, Enum):
    PLANETARY = "planetary"
    PLAIN = "plain"


class OutputBearing(str, Enum):
    THIN = "6816-2RS"
    PROTOTYPE = "6016"


@dataclass(frozen=True)
class Limit:
    minimum: float
    maximum: float
    unit: str
    reason: str


def dimension(default, minimum, maximum, unit, meaning, reason):
    return field(default=default, metadata={"meaning": meaning, "limit": Limit(minimum, maximum, unit, reason)})


@dataclass(frozen=True)
class Parameters:
    variant: Variant = field(default=Variant.PLANETARY, metadata={"meaning": "Mechanism preset; categorical, no units."})
    output_bearing: OutputBearing = field(default=OutputBearing.THIN, metadata={"meaning": "Unscaled catalog output bearing designation.", "choices": ("6816-2RS", "6016")})
    motor_clearance: float = dimension(0.2, 0.1, 0.3, "mm", "Normal clearance around the motor body inside the journal sleeve.", "The range preserves at least two 0.4 mm extrusion widths at the motor corners with the supported bearing offsets.")
    flex_teeth: int = dimension(120, 80, 240, "teeth", "External flex tooth count; circular spline has two more.", "The two-lobe profile requires fine teeth and the supported 256 mm print envelope.")
    module: float = dimension(0.7, 0.4, 1.2, "mm", "External tooth pitch diameter divided by tooth count.", "The printed trapezoid tip must remain at least 0.2 mm wide; larger modules are outside the supported tooth family.")
    flex_wall: float = dimension(1.62907222000944, 1.2, 4.0, "mm", "Minimum radial wall from external root to the deepest bore or inner tooth root.", "At least three 0.4 mm extrusion widths must remain; walls over 4 mm are outside this thin-ring flexure family.")
    flex_height: float = dimension(16.0, 16.0, 32.0, "mm", "Axial flex height; sun and planet faces span this complete height.", "The shared 16 mm minimum leaves 0.5 mm above the plain roller cap; standard DIN 7984 M3 axles through 40 mm retain at least 3 mm thread engagement up to 32 mm. Planetary heights use 0.5 mm shim steps.")
    flex_axial_clearance: float = dimension(0.0, 0.0, 1.0, "mm", "Total axial cavity clearance beyond the flex height.", "Negative clearance compresses the flex; more than 1 mm permits loss of axial tooth engagement.")
    roller_preload: float = dimension(0.7, 0.0, 1.4, "mm", "Radial intrusion of the plain roller envelope into the circular flex bore.", "Preload cannot be negative; the prototype tooth height bounds the supported radial lift.")
    planet_preload: float = dimension(0.105381480006292, 0.0, 0.5, "mm", "Holder-axis excess over the nominal ring-to-planet pitch distance.", "Preload cannot be negative; larger offsets consume the measured outer-tooth radial tip clearance.")
    sun_teeth: int = dimension(28, 20, 60, "teeth", "Sun tooth count of the internal planetary stage.", "The supported involute profile excludes undercut small pinions and fits inside the flex ring.")
    planet_teeth: int = dimension(28, 20, 60, "teeth", "Tooth count of each of the two opposed planets.", "Each planet must retain its 10.2 mm bearing pockets inside the tooth roots.")
    helix_angle: float = dimension(30.0, 10.0, 40.0, "deg", "Helix angle of each opposed herringbone half.", "The supported twist-extruded profile is a nonzero moderate-angle herringbone.")
    tooth_offset: float = dimension(0.0, -0.1, 0.1, "mm", "Additional tangential width of each rigid tooth slot.", "The coupon sweep bounds the supported allowance without removing the tooth lands.")
    bearing_offset: float = dimension(0.0, -0.1, 0.1, "mm", "Diametral adjustment of bearing seats and journals; positive loosens both.", "This is the measured-fit coupon range, not a catalog bearing size change.")
    roller_pocket_offset: float = dimension(0.0, -0.1, 0.1, "mm", "Diametral adjustment of printed 623 bearing pockets.", "This is the measured-fit coupon range.")
    insert_offset: float = dimension(0.0, -0.1, 0.2, "mm", "Diametral adjustment of the 4.2 mm heat-set pilot.", "The coupon range includes the manufacturer's 4.4 mm recommendation at +0.2 mm.")
    motor_pilot_offset: float = dimension(0.0, -0.1, 0.1, "mm", "Diametral adjustment of the 38.2 mm motor register.", "This is the measured-fit coupon range.")
    shaft_offset: float = dimension(0.0, -0.1, 0.1, "mm", "Diametral D-bore adjustment; the flat moves by half the offset.", "This is the measured-fit coupon range.")

    def validate(self):
        if not isinstance(self.variant, Variant):
            raise DesignError(f"variant = {self.variant!r} is outside planetary/plain: select a supported mechanism.")
        if not isinstance(self.output_bearing, OutputBearing):
            raise DesignError(f"output_bearing = {self.output_bearing!r} is outside 6816-2RS/6016: catalog bearings are never scaled.")
        for f in fields(self):
            limit = f.metadata.get("limit")
            if limit is None:
                continue
            value = getattr(self, f.name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
                raise DesignError(f"{f.name} = {value!r} is not a finite number: {limit.reason}")
            if value < limit.minimum or value > limit.maximum:
                direction, bound = ("below", limit.minimum) if value < limit.minimum else ("above", limit.maximum)
                raise DesignError(f"{f.name} = {value:g} {limit.unit} is {direction} the {bound:g} {limit.unit} limit: {limit.reason}")
            if f.type is int and value != int(value):
                raise DesignError(f"{f.name} = {value:g} must be an integer: individual teeth cannot be fractional.")
        if self.flex_teeth % 2:
            raise DesignError(f"flex_teeth = {self.flex_teeth} is not divisible by 2: opposed lobes must have the same tooth phase.")
        if self.variant is Variant.PLANETARY:
            if (self.sun_teeth + self.ring_teeth) % 2:
                raise DesignError(f"sun_teeth + ring_teeth = {self.sun_teeth + self.ring_teeth} must be even: the opposed planets must mesh simultaneously.")
            if abs(self.flex_height*2-round(self.flex_height*2)) > 1e-8:
                raise DesignError(f"flex_height = {self.flex_height:g} mm is outside 0.5 mm shim steps: the full-height planets need an unbroken catalog inner-race backstop.")
            if self.planet_root_radius <= 5.1 + self.roller_pocket_offset / 2 + 1.2:
                raise DesignError(f"planet_root_radius = {self.planet_root_radius:g} mm is below the bearing-pocket plus 1.2 mm wall limit: the 623 pockets would break through the planet roots.")
        if self.flex_root_radius <= self.flex_wall + 10:
            raise DesignError(f"flex_wall = {self.flex_wall:g} mm leaves a bore radius below 10 mm: the wave generator cannot fit.")
        if self.flex_root_radius - self.flex_wall <= 20.1:
            raise DesignError(f"flex_teeth × module leaves bore radius {self.flex_root_radius - self.flex_wall:g} mm below 20.1 mm: the flex must clear the 38.2 mm motor pilot opening and its retaining land.")
        if self.flex_tip_radius * 2 + 20 > 256:
            raise DesignError(f"flex_teeth × module produces diameter {self.flex_tip_radius * 2 + 20:g} mm above 256 mm: the surrounding printed housing must fit the print bed.")
        return self

    @property
    def circular_teeth(self):
        return self.flex_teeth + 2

    @property
    def ring_teeth(self):
        return self.sun_teeth + 2 * self.planet_teeth

    @property
    def flex_root_radius(self):
        thickening = (3 / 7) * self.module if self.variant is Variant.PLANETARY else 0
        return (self.flex_teeth - 2.5) * self.module / 2 + thickening

    @property
    def flex_tip_radius(self):
        return math.hypot(self.flex_root_radius + 2.2 * self.module, 0.25 * self.module)

    @property
    def planetary_module(self):
        return (self.flex_root_radius - self.flex_wall) / (self.ring_teeth / (2 * math.cos(math.radians(self.helix_angle))) + 1)

    @property
    def planet_pitch_radius(self):
        return self.planet_teeth * self.planetary_module / (2 * math.cos(math.radians(self.helix_angle)))

    @property
    def planet_root_radius(self):
        return self.planet_pitch_radius - 1.25 * self.planetary_module

    @property
    def ring_pitch_radius(self):
        return self.ring_teeth * self.planetary_module / (2 * math.cos(math.radians(self.helix_angle)))

    @property
    def planet_axis_radius(self):
        return self.ring_pitch_radius - self.planet_pitch_radius + self.planet_preload

    @property
    def roller_axis_radius(self):
        return self.flex_root_radius - self.flex_wall - 5 + self.roller_preload

    @property
    def ratio(self):
        harmonic = self.flex_teeth / 2
        if self.variant is Variant.PLAIN:
            return -harmonic
        ring_sun = self.ring_teeth / self.sun_teeth
        return -(harmonic * (1 + ring_sun) + ring_sun)

    def dictionary(self):
        return {**asdict(self), "variant": self.variant.value, "output_bearing": self.output_bearing.value}


def preset(name):
    try:
        variant = Variant(name)
    except ValueError as exc:
        raise DesignError(f"preset = {name!r} is outside planetary/plain: select a supported mechanism.") from exc
    return Parameters(variant=variant, flex_wall=1.5 if variant is Variant.PLAIN else 1.62907222000944).validate()


def apply_overrides(parameters, assignments):
    descriptors = {f.name: f for f in fields(parameters) if "limit" in f.metadata or "choices" in f.metadata}
    updates = {}
    for assignment in assignments:
        name, separator, text = assignment.partition("=")
        if not separator or name not in descriptors:
            raise DesignError(f"parameter = {assignment!r} is outside the named numeric parameters: use NAME=VALUE; supported names: {', '.join(descriptors)}.")
        if name == "output_bearing":
            try:
                updates[name] = OutputBearing(text)
            except ValueError as exc:
                raise DesignError(f"output_bearing = {text!r} is outside 6816-2RS/6016: catalog bearings are never scaled.") from exc
            continue
        try:
            number = float(text)
        except ValueError as exc:
            raise DesignError(f"{name} = {text!r} is not numeric: supply a value in {descriptors[name].metadata['limit'].unit}.") from exc
        if descriptors[name].type is int and math.isfinite(number) and number.is_integer():
            number = int(number)
        updates[name] = number
    return replace(parameters, **updates).validate()
