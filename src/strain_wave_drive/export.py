"""STEP assembly names, shared STL/3MF tessellation and manufacturing tables."""
from collections import Counter
import csv
import json
from pathlib import Path
import re
import struct
from xml.etree import ElementTree as ET
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED
import numpy as np
import cadquery as cq
from .checks import print_mesh
from .bounds import brep_bounds
from .assembly import MATERIAL_COLORS_SRGB

LINEAR_TOLERANCE = 0.02
ANGULAR_TOLERANCE = 0.1
CORE_NAMESPACE = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
# Fixed archive and header timestamps keep regenerated files byte-identical, so a rebuild with no
# geometry change leaves git clean.
FIXED_DATE_TIME = (1980, 1, 1, 0, 0, 0)
STEP_TIMESTAMP = re.compile(rb"(FILE_NAME\('[^']*',)'[^']*'")


def write_step(cadquery_assembly, destination):
    destination = Path(destination)
    cadquery_assembly.export(str(destination), exportType="STEP", mode="default", write_pcurves=False)
    data = destination.read_bytes()
    header_end = data.index(b"DATA;")
    destination.write_bytes(STEP_TIMESTAMP.sub(rb"\1'1980-01-01T00:00:00'", data[:header_end], count=1) + data[header_end:])


def step_assembly(assembly, destination):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    write_step(assembly.cadquery(), destination)


def tessellate(shape, linear_tolerance=LINEAR_TOLERANCE, angular_tolerance=ANGULAR_TOLERANCE):
    vertices, triangles = shape.tessellate(linear_tolerance, angular_tolerance)
    return np.asarray([v.toTuple() for v in vertices], dtype=np.float64), np.asarray(triangles, dtype=np.int32)


def write_stl(path, vertices, triangles):
    facets = vertices[triangles]
    normals = np.cross(facets[:, 1] - facets[:, 0], facets[:, 2] - facets[:, 0])
    magnitude = np.linalg.norm(normals, axis=1)
    normals /= np.maximum(magnitude[:, None], np.finfo(float).tiny)
    records = np.zeros(len(triangles), dtype=[("normal", "<f4", (3,)), ("vertices", "<f4", (3, 3)), ("attribute", "<u2")])
    records["normal"], records["vertices"] = normals, facets
    with Path(path).open("wb") as stream:
        stream.write(b"strain-wave-drive; units=millimetres".ljust(80, b" "))
        stream.write(struct.pack("<I", len(triangles)))
        stream.write(records.tobytes())


def write_3mf(path, vertices, triangles, label):
    ET.register_namespace("", CORE_NAMESPACE)
    tag = lambda name: f"{{{CORE_NAMESPACE}}}{name}"
    model = ET.Element(tag("model"), unit="millimeter", attrib={"{http://www.w3.org/XML/1998/namespace}lang": "en-US"})
    ET.SubElement(model, tag("metadata"), name="Title").text = label
    resources = ET.SubElement(model, tag("resources"))
    obj = ET.SubElement(resources, tag("object"), id="1", type="model", name=label)
    mesh = ET.SubElement(obj, tag("mesh"))
    vs = ET.SubElement(mesh, tag("vertices"))
    for x, y, z in vertices:
        ET.SubElement(vs, tag("vertex"), x=f"{x:.7f}", y=f"{y:.7f}", z=f"{z:.7f}")
    ts = ET.SubElement(mesh, tag("triangles"))
    for a, b, c in triangles:
        ET.SubElement(ts, tag("triangle"), v1=str(a), v2=str(b), v3=str(c))
    ET.SubElement(ET.SubElement(model, tag("build")), tag("item"), objectid="1")
    content_types = b'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/></Types>'
    relationships = b'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/></Relationships>'
    with ZipFile(path, "w") as archive:
        for name, data in (("[Content_Types].xml", content_types), ("_rels/.rels", relationships),
                           ("3D/3dmodel.model", ET.tostring(model, encoding="utf-8", xml_declaration=True))):
            archive.writestr(ZipInfo(name, date_time=FIXED_DATE_TIME), data, compress_type=ZIP_DEFLATED)


def print_pose(part):
    shape = part.shape
    for axis, angle in zip(((1, 0, 0), (0, 1, 0), (0, 0, 1)), part.print_rotation):
        if angle:
            shape = shape.rotate((0, 0, 0), axis, angle)
    bounds = brep_bounds(shape)
    return shape.translate((128 - (bounds.xmin + bounds.xmax) / 2, 128 - (bounds.ymin + bounds.ymax) / 2, -bounds.zmin))


def write_csv(path, fieldnames, rows):
    with Path(path).open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def export(assembly, destination, render_destination, render_deformation=None):
    destination, render_destination = Path(destination), Path(render_destination)
    step_dir, print_dir = destination / "step", destination / "print"
    print_only = assembly.name == "coupons"
    directories = (destination, print_dir) if print_only else (destination, step_dir, print_dir, render_destination)
    for directory in directories:
        directory.mkdir(parents=True, exist_ok=True)
    if not print_only:
        step_assembly(assembly, destination / f"{assembly.name}-assembly.step")
    rendering, print_rows, print_checks = [], [], []
    catalog_counts = Counter(p.catalog for p in assembly.parts if p.catalog is not None)
    for part in assembly.parts:
        if not print_only:
            vertices, triangles = tessellate(part.shape)
            if part.role == "flex" and render_deformation is not None:
                vertices[:, :2] = render_deformation.forward(vertices[:, :2])
            write_stl(render_destination / f"{part.id}.stl", vertices, triangles)
            rendering.append({"name": part.label, "file": f"{part.id}.stl", "material": part.render_material, "group": part.group})
        if not part.printed:
            continue
        if not print_only:
            write_step(cq.Assembly(name=part.id).add(part.shape, name=part.label, color=cq.Color(*MATERIAL_COLORS_SRGB[part.render_material])),
                       step_dir / f"{part.id}.step")
        oriented = print_pose(part)
        vertices, triangles = tessellate(oriented)
        write_stl(print_dir / f"{part.id}.stl", vertices, triangles)
        write_3mf(print_dir / f"{part.id}.3mf", vertices, triangles, part.label)
        print_checks.append(print_mesh(part, vertices, triangles))
        density = 1.13 if part.material == "PA6" else 1.25
        print_rows.append({"part": part.id, "qty": 1, "material": part.material, "layer_height_mm": .16,
                           "walls": 6, "infill_percent": 100, "supports": part.supports, "orientation": part.print_note,
                           "estimated_mass_g": round(part.shape.Volume() * density / 1000, 2),
                           "note": part.print_note if print_only else "Solid-equivalent mass using assumed density; dry PA6 and calibrate coupons before assembly."})
    if not print_only:
        write_csv(destination / "bom.csv", ["item", "designation", "vendor", "part_number", "qty", "dimensions", "link", "note"],
                  [{"item": i + 1, "designation": item.designation, "vendor": item.vendor, "part_number": item.part_number,
                    "qty": qty, "dimensions": item.dimensions, "link": item.link, "note": item.note} for i, (item, qty) in enumerate(catalog_counts.items())])
        (render_destination / "parts.json").write_text(json.dumps({"name": assembly.name, "azimuth_deg": 45, "parts": rendering}, indent=2) + "\n")
    write_csv(destination / "print.csv", ["part", "qty", "material", "layer_height_mm", "walls", "infill_percent", "supports", "orientation", "estimated_mass_g", "note"], print_rows)
    return {"name": "Printed-part geometry", "passed": all(c["passed"] for c in print_checks), "parts": print_checks}


def write_glb(assembly, destination):
    """Compact neutral-pose GLB; numeric coordinates intentionally remain in mm."""
    document = {"asset": {"version": "2.0", "generator": "strain-wave-drive", "extras": {"units": "mm", "up_axis": "Z"}},
                "scene": 0, "scenes": [{"nodes": []}], "nodes": [], "meshes": [], "buffers": [],
                "bufferViews": [], "accessors": [], "materials": []}
    binary = bytearray()
    colors = {name: [value/12.92 if value <= .04045 else ((value+.055)/1.055)**2.4 for value in color]+[1]
              for name, color in MATERIAL_COLORS_SRGB.items()}
    materials = {}
    for name, color in colors.items():
        materials[name] = len(document["materials"])
        document["materials"].append({"name": name, "pbrMetallicRoughness": {"baseColorFactor": color,
                                       "metallicFactor": .75 if name in ("steel", "brass") else 0,
                                       "roughnessFactor": .4 if name in ("steel", "brass") else .75}})

    def accessor(array, component_type, shape, target, bounds=False):
        padding = (-len(binary)) % 4
        binary.extend(b"\0" * padding)
        view = len(document["bufferViews"])
        document["bufferViews"].append({"buffer": 0, "byteOffset": len(binary), "byteLength": array.nbytes, "target": target})
        binary.extend(array.tobytes())
        item = {"bufferView": view, "componentType": component_type, "count": len(array), "type": shape}
        if bounds:
            item.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        index = len(document["accessors"])
        document["accessors"].append(item)
        return index

    for part in assembly.parts:
        vertices, triangles = tessellate(part.shape, .05, .3)
        positions = vertices.astype("<f4")
        facets = positions[triangles]
        face_normals = np.cross(facets[:, 1] - facets[:, 0], facets[:, 2] - facets[:, 0])
        normals = np.zeros_like(positions)
        for corner in range(3):
            np.add.at(normals, triangles[:, corner], face_normals)
        normals /= np.maximum(np.linalg.norm(normals, axis=1)[:, None], np.finfo(np.float32).tiny)
        short = len(vertices) < 65536
        indices = triangles.astype("<u2" if short else "<u4").reshape(-1)
        position_index = accessor(positions, 5126, "VEC3", 34962, True)
        normal_index = accessor(normals, 5126, "VEC3", 34962)
        index_index = accessor(indices, 5123 if short else 5125, "SCALAR", 34963)
        mesh = len(document["meshes"])
        document["meshes"].append({"name": part.id, "primitives": [{"attributes": {"POSITION": position_index, "NORMAL": normal_index},
                                          "indices": index_index, "material": materials[part.render_material]}]})
        document["scenes"][0]["nodes"].append(len(document["nodes"]))
        document["nodes"].append({"name": part.id, "mesh": mesh})
    binary.extend(b"\0" * ((-len(binary)) % 4))
    document["buffers"] = [{"byteLength": len(binary)}]
    encoded = json.dumps(document, separators=(",", ":")).encode()
    encoded += b" " * ((-len(encoded)) % 4)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as stream:
        stream.write(struct.pack("<III", 0x46546C67, 2, 28 + len(encoded) + len(binary)))
        stream.write(struct.pack("<II", len(encoded), 0x4E4F534A))
        stream.write(encoded)
        stream.write(struct.pack("<II", len(binary), 0x004E4942))
        stream.write(binary)
    return {"bytes": destination.stat().st_size, "nodes": len(document["nodes"]), "units": "mm",
            "linear_tolerance_mm": .05, "angular_tolerance_rad": .3}
