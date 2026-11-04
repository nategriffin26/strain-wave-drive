# Usage: blender -b --factory-startup -P tools/render/scene.py -- --input build/planetary/render --out images --prefix planetary
"""Renders a render-input directory (parts.json + STLs in mm, Z up, motor face at Z=0) with Cycles.

parts.json: {"name", "azimuth_deg"?, "parts": [{"name", "file", "material", "group"}]}, material one of
flex, printed, printed_accent, steel, brass, motor. Writes <prefix>-<view>-<scheme>.png for every view and
scheme, and prints seconds per image. tools/render/render_images.py is the one command that runs this.
"""
import argparse
import json
import math
import sys
import time
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

MM = 0.001
VIEWS = ("hero", "exploded", "cutaway", "top", "section")
MATERIALS = ("flex", "printed", "printed_accent", "steel", "brass", "motor")
RESOLUTION = {"hero": (1800, 1200), "exploded": (1200, 1800), "cutaway": (1600, 1400), "top": (1400, 1400), "section": (1400, 1400)}
GPU_BACKENDS = ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI")


def srgb(hex_colour):
    """Linear RGBA from an sRGB hex string."""
    h = hex_colour.lstrip("#")
    out = []
    for i in (0, 2, 4):
        c = int(h[i:i + 2], 16) / 255
        out.append(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4)
    return (*out, 1.0)


# Filament colours: red PA6 flex; CF-PLA in grey (fixed parts) and blue (output parts).
PA6_RED = "#b3161c"
CF_GRAY = "#3b3d40"
CF_BLUE = "#2b4a6f"
PALETTE = {"flex": ("nylon", PA6_RED, 0.42), "printed": ("plastic", CF_GRAY, 0.62),
           "printed_accent": ("plastic", CF_BLUE, 0.6), "steel": ("metal", "#a7adb4", 0.26),
           "brass": ("metal", "#e0b45c", 0.28), "motor": ("motor", "#141517", 0.38)}
# Lights: (name, azimuth relative to the camera, elevation, size in subject radii, relative power, colour).
FRONT = ("front", 0, 12, 2.5, 150, "#ffffff")  # camera-side fill so cut interiors read
SCHEMES = {
    "light": {"world": "#ffffff", "world_strength": 0.3, "exposure": -0.2,
              "lights": [("key", -55, 55, 1.4, 620, "#fff6ec"), ("fill", 70, 18, 1.4, 220, "#eef3ff"),
                         ("rim", 160, 30, 0.5, 160, "#ffffff"), FRONT]},
    "dark": {"world": "#20262e", "world_strength": 0.45, "exposure": -0.6,
             "lights": [("key", -55, 55, 1.4, 620, "#fff6ec"), ("fill", 70, 18, 1.4, 160, "#e8eeff"),
                        ("rim", 160, 30, 0.5, 700, "#dfe8ff"), ("rim2", -140, 15, 0.4, 500, "#ffffff"), FRONT]},
}
SECTION_TINT = 0.12
LENS_MM, ELEVATION_DEG = 85, 24
CUTAWAY_DEG = 135  # removed wedge; 90 hides the second planet and the sun


def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--prefix", required=True)
    p.add_argument("--views", nargs="+", default=["hero", "exploded", "cutaway"], choices=VIEWS)
    p.add_argument("--schemes", nargs="+", default=["light", "dark"], choices=sorted(SCHEMES))
    p.add_argument("--samples", type=int, default=96)
    p.add_argument("--scale", type=float, default=1.0, help="resolution multiplier")
    return p.parse_args(argv)


# ---------------------------------------------------------------- scene

def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    prefs = bpy.context.preferences.addons["cycles"].preferences
    device = "CPU"
    for backend in GPU_BACKENDS:
        try:
            prefs.compute_device_type = backend
        except TypeError:
            continue
        prefs.get_devices()
        gpus = [d for d in prefs.devices if d.type == backend]
        if gpus:
            for d in prefs.devices:
                d.use = d.type == backend
            device = backend
            break
    scene.cycles.device = "CPU" if device == "CPU" else "GPU"
    scene.cycles.use_adaptive_sampling = True
    scene.cycles.adaptive_threshold = 0.015
    scene.cycles.use_denoising = True
    scene.cycles.denoiser = "OPENIMAGEDENOISE"
    scene.cycles.max_bounces = 8
    scene.render.film_transparent = True
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    scene.render.image_settings.compression = 90
    return scene, device


def load_parts(directory):
    spec = json.loads((directory / "parts.json").read_text())
    objects = []
    for part in spec["parts"]:
        if part["material"] not in MATERIALS:
            raise SystemExit(f"{directory}: part {part['name']} has material {part['material']!r}; use one of {', '.join(MATERIALS)}")
        bpy.ops.wm.stl_import(filepath=str(directory / part["file"]), global_scale=MM)
        obj = bpy.context.selected_objects[0]
        # The importer puts the scale on the object; bake it so object coordinates are metres too.
        obj.data.transform(obj.matrix_basis)
        obj.matrix_basis.identity()
        obj.name = part["name"]
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-7)
        # The fast Manifold boolean silently skips non-manifold meshes; those take the Exact solver.
        obj["manifold"] = all(e.is_manifold for e in bm.edges)
        bm.to_mesh(obj.data)
        bm.free()
        smooth(obj.data)
        obj["material_class"] = part["material"]
        obj["group"] = part["group"]
        objects.append(obj)
    return spec, objects


def smooth(mesh):
    mesh.shade_smooth()
    mesh.set_sharp_from_angle(angle=math.radians(32))


# ---------------------------------------------------------------- materials

def node(nodes, kind, **inputs):
    n = nodes.new(kind)
    for key, value in inputs.items():
        n.inputs[key].default_value = value
    return n


def math_node(nodes, links, operation, a, b=None):
    n = nodes.new("ShaderNodeMath")
    n.operation = operation
    for socket, value in ((n.inputs[0], a), (n.inputs[1], b)):
        if value is None:
            continue
        if isinstance(value, (int, float)):
            socket.default_value = value
        else:
            links.new(value, socket)
    return n.outputs[0]


def section_mask(nt, planes):
    """Shader value that is 1 on faces lying in any of the given cut planes (world normal, offset)."""
    nodes, links = nt.nodes, nt.links
    geo = nodes.new("ShaderNodeNewGeometry")
    mask = None
    for normal, offset in planes:
        d_pos = nodes.new("ShaderNodeVectorMath"); d_pos.operation = "DOT_PRODUCT"
        links.new(geo.outputs["Position"], d_pos.inputs[0]); d_pos.inputs[1].default_value = normal
        near = nodes.new("ShaderNodeMath"); near.operation = "COMPARE"
        links.new(d_pos.outputs["Value"], near.inputs[0]); near.inputs[1].default_value = offset; near.inputs[2].default_value = 2e-6
        d_nrm = nodes.new("ShaderNodeVectorMath"); d_nrm.operation = "DOT_PRODUCT"
        links.new(geo.outputs["True Normal"], d_nrm.inputs[0]); d_nrm.inputs[1].default_value = normal
        facing = math_node(nodes, links, "GREATER_THAN", math_node(nodes, links, "ABSOLUTE", d_nrm.outputs["Value"]), 0.999)
        both = math_node(nodes, links, "MULTIPLY", near.outputs[0], facing)
        mask = both if mask is None else math_node(nodes, links, "MAXIMUM", mask, both)
    return mask


def make_material(name, entry, planes, motor_z):
    kind, colour, roughness = entry
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    base = srgb(colour)
    bsdf = node(nodes, "ShaderNodeBsdfPrincipled", Roughness=roughness)
    colour_socket = None

    def bevel(radius_mm, samples):
        b = nodes.new("ShaderNodeBevel"); b.samples = samples
        b.inputs["Radius"].default_value = radius_mm * MM
        links.new(b.outputs["Normal"], bsdf.inputs["Normal"])

    if kind == "plastic":
        # CF-PLA: matte with a fine fibre speckle in roughness and colour, softened printed edges.
        tex = nodes.new("ShaderNodeTexCoord")
        noise = node(nodes, "ShaderNodeTexNoise", Scale=9000.0, Detail=2.0, Roughness=0.6)
        links.new(tex.outputs["Object"], noise.inputs["Vector"])
        rmap = node(nodes, "ShaderNodeMapRange", **{"To Min": roughness - 0.12, "To Max": roughness + 0.1})
        links.new(noise.outputs["Fac"], rmap.inputs["Value"])
        links.new(rmap.outputs["Result"], bsdf.inputs["Roughness"])
        cmix = nodes.new("ShaderNodeMix"); cmix.data_type = "RGBA"
        links.new(noise.outputs["Fac"], cmix.inputs["Factor"])
        cmix.inputs["A"].default_value = tuple(c * 0.82 for c in base[:3]) + (1,)
        cmix.inputs["B"].default_value = tuple(min(1, c * 1.2) for c in base[:3]) + (1,)
        colour_socket = cmix.outputs["Result"]
        bevel(0.35, 6)
        bsdf.inputs["Specular IOR Level"].default_value = 0.25
    elif kind == "nylon":
        # Dry PA6 is opaque and saturated; subsurface and sheen wash the red toward pink.
        bsdf.inputs["Specular IOR Level"].default_value = 0.4
        bevel(0.25, 6)
    elif kind == "metal":
        bsdf.inputs["Metallic"].default_value = 1.0
        bevel(0.15, 4)
    elif kind == "motor":
        # Black anodised stack with aluminium end caps; the shaft above the face is steel.
        z_min, z_face = motor_z
        length = z_face - z_min
        tex = nodes.new("ShaderNodeTexCoord")
        sep = nodes.new("ShaderNodeSeparateXYZ")
        links.new(tex.outputs["Object"], sep.inputs[0])
        top = math_node(nodes, links, "GREATER_THAN", sep.outputs["Z"], z_face - 0.13 * length)
        bottom = math_node(nodes, links, "LESS_THAN", sep.outputs["Z"], z_min + 0.16 * length)
        cap = math_node(nodes, links, "MAXIMUM", top, bottom)
        cmix = nodes.new("ShaderNodeMix"); cmix.data_type = "RGBA"
        links.new(cap, cmix.inputs["Factor"])
        cmix.inputs["A"].default_value = base
        cmix.inputs["B"].default_value = srgb("#c4c7cb")
        colour_socket = cmix.outputs["Result"]
        links.new(cap, bsdf.inputs["Metallic"])
        rough = nodes.new("ShaderNodeMath"); rough.operation = "MULTIPLY_ADD"
        links.new(cap, rough.inputs[0]); rough.inputs[1].default_value = -0.1; rough.inputs[2].default_value = roughness
        links.new(rough.outputs[0], bsdf.inputs["Roughness"])
        bsdf.inputs["Coat Weight"].default_value = 0.25
        bsdf.inputs["Coat Roughness"].default_value = 0.3
    else:
        raise ValueError(f"unknown material kind {kind!r}")

    if colour_socket is None:
        rgb = nodes.new("ShaderNodeRGB")
        rgb.outputs[0].default_value = base
        colour_socket = rgb.outputs[0]
    if planes and kind != "nylon":
        # Cut faces read a little lighter than the surfaces around them.
        lighter = nodes.new("ShaderNodeMix"); lighter.data_type = "RGBA"; lighter.blend_type = "SCREEN"
        lighter.inputs["Factor"].default_value = SECTION_TINT
        links.new(colour_socket, lighter.inputs["A"])
        lighter.inputs["B"].default_value = (1, 1, 1, 1)
        final = nodes.new("ShaderNodeMix"); final.data_type = "RGBA"
        links.new(section_mask(nt, planes), final.inputs["Factor"])
        links.new(colour_socket, final.inputs["A"]); links.new(lighter.outputs["Result"], final.inputs["B"])
        colour_socket = final.outputs["Result"]
    links.new(colour_socket, bsdf.inputs["Base Color"])
    links.new(bsdf.outputs[0], out.inputs["Surface"])
    return mat


def assign_materials(objects, planes):
    motor_objs = [o for o in objects if o["material_class"] == "motor"]
    # object coordinates, so the caps stay put when the motor is exploded
    motor_z = (min(c[2] for o in motor_objs for c in o.bound_box), 0.0) if motor_objs else (0.0, 0.0)
    cache = {}
    for obj in objects:
        cls = obj["material_class"]
        if cls not in cache:
            cache[cls] = make_material(cls, PALETTE[cls], planes, motor_z)
        obj.data.materials.clear()
        obj.data.materials.append(cache[cls])


# ---------------------------------------------------------------- world and lights

def setup_world(scene, scheme):
    world = bpy.data.worlds.new("world")
    scene.world = world
    world.use_nodes = True
    bg = world.node_tree.nodes["Background"]
    bg.inputs["Color"].default_value = srgb(scheme["world"])
    bg.inputs["Strength"].default_value = scheme["world_strength"]
    scene.view_settings.view_transform = "AgX"
    # Punchy keeps saturated filament colours (the PA6 red) from desaturating toward pink.
    for look in ("AgX - Punchy", "Punchy"):
        try:
            scene.view_settings.look = look
            break
        except TypeError:
            continue
    scene.view_settings.exposure = scheme["exposure"]


def add_lights(scheme, centre, radius, camera_azimuth):
    """Area lights; each light's azimuth is relative to the camera's (0 = behind the camera)."""
    for name, az, el, size, power, colour in scheme["lights"]:
        data = bpy.data.lights.new(name, "AREA")
        data.shape = "DISK"
        data.size = size * radius
        # power is a relative irradiance: watts scale with the square of the light distance
        data.energy = power / 40 * (radius * 4.0) ** 2
        data.color = srgb(colour)[:3]
        # One shadow-casting light keeps the floor shadow a single soft contact shadow.
        data.use_shadow = name == "key"
        obj = bpy.data.objects.new(name, data)
        bpy.context.scene.collection.objects.link(obj)
        d = direction(camera_azimuth + az, el)
        obj.location = centre + d * radius * 4.0
        obj.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()


def add_floor(z, radius):
    bpy.ops.mesh.primitive_plane_add(size=radius * 30, location=(0, 0, z))
    floor = bpy.context.active_object
    floor.is_shadow_catcher = True


# ---------------------------------------------------------------- views

def direction(azimuth, elevation):
    a, e = math.radians(azimuth), math.radians(elevation)
    return Vector((math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)))


def world_points(objects):
    return [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]


def bounds(objects):
    pts = world_points(objects)
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    return lo, hi


def explode(objects):
    """Separates groups along Z, each clear of the one below by a gap.

    Groups stack by the top face of their main parts (hardware ignored, since screw heads stand proud), so an
    enclosing part such as the output drum lifts above everything it covers.
    """
    groups = {}
    for o in objects:
        groups.setdefault(o["group"], []).append(o)

    def top(group):
        main = [o for o in group if o["material_class"] not in ("steel", "brass")] or group
        return bounds(main)[1].z, bounds(main)[0].z

    order = sorted(groups, key=lambda g: top(groups[g]))
    lo, hi = bounds(objects)
    gap = 0.06 * (hi.z - lo.z)
    lift, previous_top = 0.0, None
    for g in order:
        g_lo, g_hi = bounds(groups[g])
        if previous_top is not None:
            lift = max(lift, previous_top + gap - g_lo.z)
        for o in groups[g]:
            o.location.z += lift
        previous_top = g_hi.z + lift


def cut_planes(cut_start_deg, wedge_deg):
    """The two half-planes through the axis that bound the removed wedge."""
    planes = []
    for angle in (cut_start_deg, cut_start_deg + wedge_deg):
        a = math.radians(angle)
        planes.append(((-math.sin(a), math.cos(a), 0.0), 0.0))
    return planes


def box_cutter(scale, location, rotation_z=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1)
    cutter = bpy.context.active_object
    cutter.scale = scale
    cutter.location = location
    bpy.ops.object.transform_apply(location=True, scale=True)
    cutter.rotation_euler = (0, 0, rotation_z)
    cutter.hide_render = True
    bpy.context.view_layer.update()
    return cutter


def apply_cut(objects, cutter):
    """Boolean-differences the cutter from every object it touches; drops objects it swallows."""
    c_lo, c_hi = bounds([cutter])
    kept = []
    for o in objects:
        lo, hi = bounds([o])
        # The purchased motor stays whole: its insides say nothing about the drive.
        if (o["material_class"] == "motor" or hi.x < c_lo.x or lo.x > c_hi.x or hi.y < c_lo.y or lo.y > c_hi.y
                or hi.z < c_lo.z or lo.z > c_hi.z):
            kept.append(o)
            continue
        mod = o.modifiers.new("cut", "BOOLEAN")
        mod.operation = "DIFFERENCE"
        mod.object = cutter
        mod.solver = "MANIFOLD" if o["manifold"] else "EXACT"
        bpy.context.view_layer.objects.active = o
        bpy.ops.object.modifier_apply(modifier=mod.name)
        if len(o.data.polygons) == 0:
            bpy.data.objects.remove(o)
            continue
        smooth(o.data)
        kept.append(o)
    return kept


def silhouette_points(objects, per_object=6000):
    """World-space mesh vertices, thinned per object; bounding-box corners overstate round parts."""
    pts = []
    for o in objects:
        verts = o.data.vertices
        stride = max(1, len(verts) // per_object)
        pts.extend(o.matrix_world @ verts[i].co for i in range(0, len(verts), stride))
    return pts


def fit_camera(cam, objects, azimuth, elevation, aspect, ortho, look_down, margin=0.04):
    """Places the camera so every mesh vertex fits the frame."""
    pts = silhouette_points(objects)
    lo, hi = bounds(objects)
    centre = (lo + hi) / 2
    if look_down:
        d, right, up = Vector((0, 0, 1)), Vector((1, 0, 0)), Vector((0, 1, 0))
    else:
        d = direction(azimuth, elevation)
        right = (-d).cross(Vector((0, 0, 1))).normalized()
        up = right.cross(-d).normalized()
    xs = [(p - centre).dot(right) for p in pts]
    ys = [(p - centre).dot(up) for p in pts]
    centre = centre + right * (max(xs) + min(xs)) / 2 + up * (max(ys) + min(ys)) / 2
    cam.data.sensor_fit = "HORIZONTAL" if aspect >= 1 else "VERTICAL"
    cam.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
    size = (hi - lo).length
    if ortho:
        cam.data.type = "ORTHO"
        cam.data.ortho_scale = (max(span_x, span_y * aspect) if aspect >= 1 else max(span_y, span_x / aspect)) / (1 - 2 * margin)
        cam.location = centre + d * size * 5
        cam.data.clip_end = size * 20
        return
    t = math.tan(cam.data.angle / 2) * (1 - 2 * margin)
    lim_h, lim_v = (t, t / aspect) if aspect >= 1 else (t * aspect, t)
    distance = max(max(q.dot(d) + abs(q.dot(right)) / lim_h, q.dot(d) + abs(q.dot(up)) / lim_v) for q in (p - centre for p in pts))
    cam.location = centre + d * distance
    cam.data.clip_start = distance * 0.01
    cam.data.clip_end = distance * 10


def build_view(directory, view, scheme_name, args):
    scene, device = reset_scene()
    spec, objects = load_parts(directory)
    azimuth = spec.get("azimuth_deg", -50.0)
    planes = None
    lo, hi = bounds(objects)
    size = max((hi - lo).length, 1e-3) * 2
    if view == "exploded":
        explode(objects)
    elif view == "cutaway":
        # The removed wedge faces the camera. Its first face lies on azimuth_deg − 45, the wave generator's
        # major axis by the parts.json convention, so one planet and both spline meshes show in section; the
        # wedge is wide enough to see the other planet and the sun behind it.
        cut_start = azimuth - 45.0
        for start in (cut_start, cut_start + CUTAWAY_DEG - 90.0):
            cutter = box_cutter((size, size, size * 2), (size / 2, size / 2, 0), math.radians(start))
            objects = apply_cut(objects, cutter)
        planes = cut_planes(cut_start, CUTAWAY_DEG)
        azimuth = cut_start + CUTAWAY_DEG / 2
    elif view == "section":
        flex = [o for o in objects if o["material_class"] == "flex"]
        f_lo, f_hi = bounds(flex or objects)
        z = (f_lo.z + f_hi.z) / 2
        cutter = box_cutter((size, size, size), (0, 0, z + size / 2))
        objects = apply_cut(objects, cutter)
        planes = [((0.0, 0.0, 1.0), z)]
    bpy.context.view_layer.update()
    assign_materials(objects, planes)
    lo, hi = bounds(objects)
    radius = max((hi - lo).length / 2, 1e-3)
    scheme = SCHEMES[scheme_name]
    setup_world(scene, scheme)
    add_lights(scheme, (lo + hi) / 2, radius, azimuth)
    look_down = view in ("top", "section")
    if not look_down:
        add_floor(lo.z, radius)
    w, h = RESOLUTION[view]
    scene.render.resolution_x, scene.render.resolution_y = int(w * args.scale), int(h * args.scale)
    scene.cycles.samples = args.samples
    cam = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    cam.data.lens = LENS_MM
    scene.collection.objects.link(cam)
    scene.camera = cam
    # The cutaway looks further down into the cut so the gears inside read.
    elevation = {"exploded": 0.6 * ELEVATION_DEG, "cutaway": 32.0}.get(view, ELEVATION_DEG)
    fit_camera(cam, objects, azimuth, elevation, w / h, look_down, look_down)
    return scene, device


def main():
    args = parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    for view in args.views:
        for scheme in args.schemes:
            scene, device = build_view(args.input, view, scheme, args)
            path = args.out / f"{args.prefix}-{view}-{scheme}.png"
            scene.render.filepath = str(path)
            t = time.perf_counter()
            bpy.ops.render.render(write_still=True)
            print(f"[render] {path}  {time.perf_counter() - t:.1f} s  ({device})", flush=True)


main()
