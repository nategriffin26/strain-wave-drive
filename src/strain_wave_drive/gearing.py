"""Single-wire tooth profiles; the harmonic teeth reproduce the printed trapezoids."""
import math
import numpy as np
import cadquery as cq
from .parameters import Parameters, Variant

TAU = 2 * math.pi


def rotate(points, angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.asarray(points) @ np.array([[c, s], [-s, c]])


def _vector(point, z=0):
    return cq.Vector(float(point[0]), float(point[1]), z)


def _repeat_profile(local, root_radius, teeth, z=0, phase=0):
    """Join straight tooth segments with exact root-circle arcs, once per ring."""
    edges = []
    pitch = TAU / teeth
    local = np.asarray(local)
    for i in range(teeth):
        angle = i * pitch + phase
        points = rotate(local, angle)
        for a, b in zip(points, points[1:]):
            if np.linalg.norm(a - b) > 1e-9:
                edges.append(cq.Edge.makeLine(_vector(a, z), _vector(b, z)))
        end = rotate(local[:1], angle + pitch)[0]
        middle_angle = angle + pitch / 2
        middle = root_radius * np.array([math.cos(middle_angle), math.sin(middle_angle)])
        edges.append(cq.Edge.makeThreePointArc(_vector(points[-1], z), _vector(middle, z), _vector(end, z)))
    return cq.Wire.assembleEdges(edges)


def _profile_points(local, root_radius, teeth, arc_samples=6, phase=0):
    points = []
    pitch = TAU / teeth
    start_angle = math.atan2(local[-1][1], local[-1][0])
    end_angle = pitch + math.atan2(local[0][1], local[0][0])
    arc = np.column_stack((np.cos(np.linspace(start_angle, end_angle, arc_samples + 2)[1:-1]), np.sin(np.linspace(start_angle, end_angle, arc_samples + 2)[1:-1]))) * root_radius
    segment = np.vstack((local, arc))
    for i in range(teeth):
        points.append(rotate(segment, i * pitch + phase))
    return np.concatenate(points)


def flex_tooth(p):
    root, m = p.flex_root_radius, p.module
    x = math.sqrt(root * root - m * m)
    return [(x, -m), (root + 2.2 * m, -.25 * m), (root + 2.2 * m, .25 * m), (x, m)]


def flex_wire(p, z=0):
    return _repeat_profile(flex_tooth(p), p.flex_root_radius, p.flex_teeth, z)


def flex_points(p):
    return _profile_points(flex_tooth(p), p.flex_root_radius, p.flex_teeth)


def slot_tooth(p, asymmetric=False):
    r, m = p.flex_teeth * p.module / 2, p.module
    half_mouth = .75 * m + .1 + p.tooth_offset / 2
    half_floor = .25 * m + .1 + p.tooth_offset / 2
    floor_x = r + 1.8 * m + .2
    mouth_x = math.sqrt(r * r - half_mouth * half_mouth)
    # The measured dynamic root has 0.06 mm extra on one side; preserving it costs one coordinate.
    extra = .06 * m / .7 if asymmetric else 0
    return [(mouth_x, -half_mouth), (r, -half_mouth), (floor_x, -half_floor),
            (floor_x, half_floor + extra), (r, half_mouth), (mouth_x, half_mouth)]


def circular_wire(p, z=0):
    return _repeat_profile(slot_tooth(p), p.flex_teeth * p.module / 2, p.circular_teeth, z)


def dynamic_wire(p, z=0):
    return _repeat_profile(slot_tooth(p, True), p.flex_teeth * p.module / 2, p.flex_teeth, z)


def circular_points(p):
    return _profile_points(slot_tooth(p), p.flex_teeth * p.module / 2, p.circular_teeth)


def dynamic_points(p):
    return _profile_points(slot_tooth(p, True), p.flex_teeth * p.module / 2, p.flex_teeth)


def tooth_ring(inner_wire, outer_radius, height, z=0):
    outer = cq.Wire.makeCircle(outer_radius, cq.Vector(0, 0, z), cq.Vector(0, 0, 1))
    return cq.Solid.extrudeLinear(outer, [inner_wire], cq.Vector(0, 0, height))


def _involute_flank(teeth, normal_module, helix_angle, samples=16):
    pitch = teeth * normal_module / (2 * math.cos(math.radians(helix_angle)))
    # Native STEP flank fit: 20° normal, 22.796° transverse at a 30° helix.
    pressure = math.atan(math.tan(math.radians(20)) / math.cos(math.radians(helix_angle)))
    base = pitch * math.cos(pressure)
    root, tip = pitch - 1.25 * normal_module, pitch + normal_module
    inv = lambda r: math.sqrt(max(0, (r / base) ** 2 - 1)) - math.acos(min(1, base / r))
    radii = np.linspace(max(root, base), tip, samples)
    angles = np.array([math.pi / (2 * teeth) + inv(pitch) - inv(float(r)) for r in radii])
    positive = np.column_stack((radii * np.cos(angles), radii * np.sin(angles)))
    return root, tip, positive, angles


def involute_points(teeth, normal_module, helix_angle, phase=0):
    root, tip, positive, angles = _involute_flank(teeth, normal_module, helix_angle)
    start = [root * math.cos(angles[0]), -root * math.sin(angles[0])]
    end = [start[0], -start[1]]
    tip_angles = np.linspace(-angles[-1], angles[-1], 5)[1:-1]
    tip_arc = np.column_stack((tip * np.cos(tip_angles), tip * np.sin(tip_angles)))
    local = np.vstack(([start], positive * [1, -1], tip_arc, positive[::-1], [end]))
    keep = np.r_[True, np.linalg.norm(np.diff(local, axis=0), axis=1) > 1e-8]
    return _profile_points(local[keep], root, teeth, arc_samples=3, phase=phase)


def involute_wire(teeth, normal_module, helix_angle, z=0, phase=0):
    root, tip, positive, angles = _involute_flank(teeth, normal_module, helix_angle)
    root_negative = np.array([root * math.cos(angles[0]), -root * math.sin(angles[0])])
    root_positive = root_negative * [1, -1]
    edges = []
    pitch = TAU / teeth
    for i in range(teeth):
        angle = i * pitch + phase
        negative = rotate(positive * [1, -1], angle)
        upper = rotate(positive, angle)
        start, end = rotate([root_negative, root_positive], angle)
        if np.linalg.norm(start - negative[0]) > 1e-8:
            edges.append(cq.Edge.makeLine(_vector(start, z), _vector(negative[0], z)))
        edges.append(cq.Edge.makeSpline([_vector(v, z) for v in negative]))
        middle = rotate([[tip, 0]], angle)[0]
        edges.append(cq.Edge.makeThreePointArc(_vector(negative[-1], z), _vector(middle, z), _vector(upper[-1], z)))
        edges.append(cq.Edge.makeSpline([_vector(v, z) for v in upper[::-1]]))
        if np.linalg.norm(end - upper[0]) > 1e-8:
            edges.append(cq.Edge.makeLine(_vector(upper[0], z), _vector(end, z)))
        next_start = rotate([root_negative], angle + pitch)[0]
        middle = rotate([[root, 0]], angle + pitch / 2)[0]
        edges.append(cq.Edge.makeThreePointArc(_vector(end, z), _vector(middle, z), _vector(next_start, z)))
    return cq.Wire.assembleEdges(edges)


def herringbone(teeth, normal_module, helix_angle, width, z=0, phase=0, hand=1):
    radius = teeth * normal_module / (2 * math.cos(math.radians(helix_angle)))
    half = width / 2
    twist = hand * math.degrees(math.tan(math.radians(helix_angle)) * half / radius)
    bottom = involute_wire(teeth, normal_module, helix_angle, z, phase)
    center = involute_wire(teeth, normal_module, helix_angle, z + half, phase + math.radians(twist))
    lower = cq.Solid.extrudeLinearWithRotation(bottom, [], cq.Vector(0, 0, z), cq.Vector(0, 0, half), twist)
    upper = cq.Solid.extrudeLinearWithRotation(center, [], cq.Vector(0, 0, z + half), cq.Vector(0, 0, half), -twist)
    return lower.fuse(upper).clean()


class RadialRingMap:
    """Material-angle map on a prescribed neutral curve; not a material solver."""
    def __init__(self, radius, u, radial):
        self.radius, self._u = radius, u
        derivative = np.gradient(radial, u, edge_order=2)
        c, s = np.cos(u), np.sin(u)
        self._neutral = np.column_stack((radial * c, radial * s))
        tangent = np.column_stack((derivative * c - radial * s, derivative * s + radial * c))
        speed = np.linalg.norm(tangent, axis=1)
        self._normal = np.column_stack((tangent[:, 1], -tangent[:, 0])) / speed[:, None]
        self._arc = np.r_[0, np.cumsum((speed[:-1] + speed[1:]) * np.diff(u) / 2)]
        self.length_ratio = float(self._arc[-1] / (TAU * radius))
        self._arc *= TAU / self._arc[-1]

    def forward(self, points, flex_angle=0, wave_angle=0):
        points = np.asarray(points)
        radii = np.linalg.norm(points, axis=-1)
        angles = np.arctan2(points[..., 1], points[..., 0]) + flex_angle - wave_angle
        q = np.mod(angles, TAU)
        neutral = np.column_stack([np.interp(q, self._arc, self._neutral[:, i]) for i in (0, 1)])
        normal = np.column_stack([np.interp(q, self._arc, self._normal[:, i]) for i in (0, 1)])
        normal /= np.linalg.norm(normal, axis=1)[:, None]
        return rotate(neutral + (radii - self.radius)[:, None] * normal, wave_angle)


class RollerRingMap(RadialRingMap):
    """Six even Fourier bending modes, constrained by roller lift and perimeter."""
    name = "Two-point-loaded ring Fourier surrogate"

    def __init__(self, radius, lift):
        u = np.linspace(0, TAU, 8193)
        modes = sum(np.cos(n * u) / (n * n - 1) ** 2 for n in range(2, 13, 2))
        modes /= modes[0]
        low, high = -lift, 0.0
        for _ in range(40):
            contraction = (low + high) / 2
            radial = radius + contraction + (lift - contraction) * modes
            length = np.trapezoid(np.hypot(radial, np.gradient(radial, u, edge_order=2)), u)
            if length > TAU * radius:
                high = contraction
            else:
                low = contraction
        contraction = (low + high) / 2
        super().__init__(radius, u, radius + contraction + (lift - contraction) * modes)


class PlanetaryRingMap(RadialRingMap):
    """Two-zone contact geometry; hoop strain and contact force are not predicted."""
    name = "Two-zone planetary contact-constrained radial surrogate"

    def __init__(self, radius, preload, minor_contraction):
        u = np.linspace(0, TAU, 8193)
        mean = (preload - minor_contraction) / 2
        amplitude = (preload + minor_contraction) / 2
        super().__init__(radius, u, radius + mean + amplitude * np.cos(2 * u))


def deformation(p):
    neutral = p.flex_root_radius - p.flex_wall / 2
    if p.variant is Variant.PLAIN:
        return RollerRingMap(neutral, p.roller_preload)
    minor_contraction = max(0, p.flex_tip_radius - p.flex_teeth * p.module / 2)
    return PlanetaryRingMap(neutral, p.planet_preload, minor_contraction)
