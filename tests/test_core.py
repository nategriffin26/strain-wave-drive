import json
import math
import struct
from dataclasses import fields, replace
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile
from xml.etree import ElementTree as ET

import cadquery as cq
import numpy as np
import trimesh
from OCP.BRepAdaptor import BRepAdaptor_Surface

from strain_wave_drive.parameters import DesignError, apply_overrides, preset
from strain_wave_drive.assembly import Assembly, Part, build
from strain_wave_drive.bounds import brep_bounds
from strain_wave_drive.checks import manufactured_interference, operating_mesh, print_mesh, retention_stack
from strain_wave_drive.export import print_pose, tessellate, write_stl, write_3mf, write_glb
from strain_wave_drive.hardware import cylinder, motor_parts
from strain_wave_drive.printed_parts import plain_flexspline, dimensions, shaft_clamp_hub, plain_carrier_shapes
from strain_wave_drive.prototype import compare
from strain_wave_drive.motion_check import rotation_envelope, coupled_flex_carrier
from strain_wave_drive.measurements import measure_features
from strain_wave_drive.checks import measured_holder_preload
from strain_wave_drive.gearing import deformation

ROOT = Path(__file__).resolve().parents[1]


class ParameterTests(unittest.TestCase):
    def test_signed_ratios_include_moving_internal_ring(self):
        self.assertEqual(preset('plain').ratio, -60)
        self.assertEqual(preset('planetary').ratio, -243)

    def test_measured_planet_axis_is_holder_not_loose_cad_pose(self):
        p = preset('planetary')
        self.assertAlmostEqual(p.planetary_module, .804, places=10)
        self.assertAlmostEqual(p.planet_axis_radius, 26.1, places=10)

    def test_limits_reject_one_step_below_and_above(self):
        p = preset('plain')
        for descriptor in fields(p):
            limit = descriptor.metadata.get('limit')
            if limit is None:
                continue
            step = 1 if descriptor.type is int else .001
            for value in (limit.minimum - step, limit.maximum + step):
                with self.subTest(parameter=descriptor.name, value=value):
                    with self.assertRaisesRegex(DesignError, descriptor.name):
                        replace(p, **{descriptor.name: value}).validate()
            for value in (limit.minimum, limit.maximum):
                with self.subTest(parameter=descriptor.name, boundary=value):
                    replace(p, **{descriptor.name: int(value) if descriptor.type is int else value}).validate()

    def test_nonfinite_and_fractional_tooth_inputs_are_named(self):
        for text in ('module=nan', 'flex_wall=inf', 'flex_teeth=120.5', 'flex_teeth=121'):
            with self.subTest(text=text), self.assertRaises(DesignError):
                apply_overrides(preset('plain'), [text])
        with self.assertRaisesRegex(DesignError, 'flex_wall = 0.9 mm.*1.2 mm'):
            apply_overrides(preset('plain'), ['flex_wall=0.9'])
        with self.assertRaisesRegex(DesignError, 'parameter'):
            apply_overrides(preset('plain'), ['unknown=1'])

    def test_planet_face_width_has_one_axial_input(self):
        self.assertNotIn('planet_width', preset('planetary').dictionary())
        with self.assertRaisesRegex(DesignError, 'parameter'):
            apply_overrides(preset('planetary'), ['planet_width=16'])
        with self.assertRaisesRegex(DesignError, '0.5 mm shim steps'):
            apply_overrides(preset('planetary'), ['flex_height=16.1'])
        apply_overrides(preset('planetary'), ['flex_height=16.5'])


class GeometryTests(unittest.TestCase):
    def test_plain_brep_reproduces_printed_wall_bore_and_height(self):
        shape = plain_flexspline(preset('plain'))
        radii = {round(BRepAdaptor_Surface(f.wrapped).Cylinder().Radius(), 6) for f in shape.Faces() if f.geomType() == 'CYLINDER'}
        self.assertIn(39.625, radii)
        self.assertIn(41.125, radii)
        self.assertAlmostEqual(shape.BoundingBox().zlen, 16, places=6)
        self.assertAlmostEqual(shape.BoundingBox().xlen, 85.33, places=5)
        self.assertTrue(shape.isValid())
        self.assertEqual(len(shape.Solids()), 1)

    def test_moved_part_triggers_interference_negative_control(self):
        box = cq.Workplane('XY').box(2, 2, 2).val()
        assembly = Assembly('negative control', [Part('fixed', 'Fixed block', box, 'CF-PLA', 'fixture'),
                                                Part('moving', 'Moving block', box.translate((3, 0, 0)), 'CF-PLA', 'fixture')])
        self.assertTrue(manufactured_interference(assembly)['passed'])
        assembly.parts[1].shape = box.translate((.5, 0, 0))
        result = manufactured_interference(assembly)
        self.assertFalse(result['passed'])
        self.assertAlmostEqual(result['overlaps'][0]['volume_mm3'], 6, places=6)
        self.assertEqual(result['overlaps'][0]['reason'], 'Undeclared manufactured-pose interference')

    def test_declared_contact_has_bounded_overlap_not_blanket_exemption(self):
        box = cq.Workplane('XY').box(2, 2, 2).val()
        assembly = Assembly('bounded contact', [Part('a', 'A', box, 'CF-PLA', 'fixture'), Part('b', 'B', box.translate((1.9, 0, 0)), 'steel', 'fixture')])
        assembly.contact('a', 'b', 'crush_rib', 'Finite crush-rib engagement', .5)
        self.assertTrue(manufactured_interference(assembly)['passed'])
        assembly.parts[1].shape = box.translate((1, 0, 0))
        self.assertFalse(manufactured_interference(assembly)['passed'])

    def test_lobe_engagement_does_not_mistake_tooth_root_gap_for_disengagement(self):
        reference = json.loads((ROOT / 'reference/prototype-profiles.json').read_text())
        for variant in ('plain', 'planetary'):
            with self.subTest(variant=variant):
                result = operating_mesh(preset(variant), reference)[0]
                self.assertTrue(result['passed'], result)
                self.assertEqual(len(result['samples']), 17)
                self.assertTrue(all(min(s['major_axis_engagement_mm']) > .2 for s in result['samples']))
                self.assertTrue(all('baseline' in s for s in result['samples']))

    def test_stl_and_3mf_share_mm_geometry_on_the_print_bed(self):
        part = Part('block', 'Dimension block', cq.Workplane('XY').box(2, 3, 4).val().translate((20, 30, 40)), 'CF-PLA', 'fixture')
        vertices, triangles = tessellate(print_pose(part))
        np.testing.assert_allclose(vertices.min(axis=0), [127, 126.5, 0], atol=1e-7)
        np.testing.assert_allclose(vertices.max(axis=0), [129, 129.5, 4], atol=1e-7)
        self.assertTrue(print_mesh(part, vertices, triangles)['passed'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            write_stl(path / 'block.stl', vertices, triangles)
            mesh = trimesh.load_mesh(path / 'block.stl')
            self.assertTrue(mesh.is_watertight)
            np.testing.assert_allclose(mesh.extents, [2, 3, 4], atol=1e-7)
            write_3mf(path / 'block.3mf', vertices, triangles, part.label)
            with ZipFile(path / 'block.3mf') as archive:
                model = ET.fromstring(archive.read('3D/3dmodel.model'))
                self.assertEqual(model.attrib['unit'], 'millimeter')
                namespace = {'m': 'http://schemas.microsoft.com/3dmanufacturing/core/2015/02'}
                self.assertEqual(len(model.findall('.//m:triangle', namespace)), len(triangles))
                exported = np.array([[float(v.attrib[k]) for k in ('x', 'y', 'z')] for v in model.findall('.//m:vertex', namespace)])
                np.testing.assert_allclose(exported, vertices, atol=1e-7)

    def test_cached_triangulation_cannot_lift_print_above_bed(self):
        part = Part('round', 'Round print gauge', cylinder(42.5, -14, 12), 'CF-PLA', 'fixture')
        tessellate(part.shape, .05, .3)
        self.assertAlmostEqual(brep_bounds(part.shape).zmin, -14, places=7)
        vertices, triangles = tessellate(print_pose(part))
        self.assertAlmostEqual(float(vertices[:, 2].min()), 0, places=7)
        self.assertTrue(print_mesh(part, vertices, triangles)['passed'])

    def test_shaft_clamp_relief_clears_round_shaft_below_flat(self):
        p = preset('plain')
        hub = shaft_clamp_hub(p, dimensions(p), 10.5)
        shaft = motor_parts()[1].shape
        self.assertLess(sum(s.Volume() for s in hub.intersect(shaft).Solids()), 1e-6)

    def test_roller_arms_do_not_fill_the_shaft_bore(self):
        p = preset('plain')
        hub = plain_carrier_shapes(p, dimensions(p))['roller_hub']
        shaft = motor_parts()[1].shape
        self.assertLess(sum(s.Volume() for s in hub.intersect(shaft).Solids()), 1e-6)

    def test_glb_preserves_part_id_normals_and_mm_coordinates(self):
        shape = cq.Workplane('XY').box(2, 3, 4).val().translate((20, 30, 40))
        assembly = Assembly('export', [Part('dimension_block', 'Dimension block', shape, 'CF-PLA', 'fixture')])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.glb'
            write_glb(assembly, path)
            data = path.read_bytes()
            magic, version, size = struct.unpack_from('<III', data)
            self.assertEqual((magic, version, size), (0x46546C67, 2, len(data)))
            json_size = struct.unpack_from('<I', data, 12)[0]
            document = json.loads(data[20:20+json_size])
            self.assertEqual(document['nodes'][0]['name'], 'dimension_block')
            self.assertEqual(document['asset']['extras']['units'], 'mm')
            primitive = document['meshes'][0]['primitives'][0]
            position = document['accessors'][primitive['attributes']['POSITION']]
            np.testing.assert_allclose(position['min'], [19, 28.5, 38])
            normal = document['accessors'][primitive['attributes']['NORMAL']]
            view = document['bufferViews'][normal['bufferView']]
            normals = np.frombuffer(data, dtype='<f4', count=normal['count']*3,
                                    offset=28+json_size+view['byteOffset']).reshape(-1, 3)
            np.testing.assert_allclose(np.linalg.norm(normals, axis=1), 1, atol=1e-6)


class RetentionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameters = preset('plain')
        cls.assembly, cls.dimensions = build(cls.parameters)

    def test_missing_inner_race_shim_breaks_clamp_stack(self):
        result = retention_stack(self.assembly, self.parameters, self.dimensions)
        self.assertTrue(result['passed'], result)
        removed = next(part for part in self.assembly.parts if part.id.startswith('roller_1_') and 'shim' in part.id)
        incomplete = Assembly('missing shim', [part for part in self.assembly.parts if part is not removed])
        self.assertFalse(retention_stack(incomplete, self.parameters, self.dimensions)['passed'])

    def test_changed_head_recess_is_measured_not_reported_from_parameters(self):
        plate = next(part for part in self.assembly.parts if part.id == 'circular_spline')
        cutters = [cylinder(4.6, 1.65, 4, x, y) for x in (-23.57, 23.57) for y in (-23.57, 23.57)]
        altered = replace(plate, shape=plate.shape.cut(*cutters))
        assembly = Assembly('changed head recess', [altered if part is plate else part for part in self.assembly.parts])
        report = compare(self.parameters, assembly, self.dimensions, json.loads((ROOT/'reference/prototype-fits.json').read_text()))
        record = next(record for record in report['records'] if record['id'] == 'motor_head_recess')
        self.assertEqual(record['status'], 'mismatch')

    def test_plain_flex_wave_remains_aligned_with_roller_carrier(self):
        parts = {part.id: part for part in self.assembly.parts}
        flex, hub = parts['flexspline'], parts['roller_hub']
        wave = deformation(self.parameters)
        self.assertTrue(coupled_flex_carrier(flex,hub,self.parameters,wave)['passed'])
        wrong_phase = replace(hub,shape=hub.shape.rotate((0,0,0),(0,0,1),90))
        self.assertFalse(coupled_flex_carrier(flex,wrong_phase,self.parameters,wave)['passed'])


class MotionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.parameters = preset('planetary')
        cls.assembly, cls.dimensions = build(cls.parameters)
        cls.parts = {part.id: part for part in cls.assembly.parts}

    def test_carrier_boss_rotation_clears_motor_heads(self):
        carrier = self.parts['carrier_lower']
        head = self.parts['motor_screw_1']
        head_envelope = rotation_envelope(head)
        self.assertLess(rotation_envelope(carrier).intersection(head_envelope).area, 1e-6)
        original_height = replace(carrier, shape=carrier.shape.translate((0, 0, -1.2)))
        self.assertGreater(rotation_envelope(original_height).intersection(head_envelope).area, .03)

    def test_payload_insert_circle_clears_axle_head_rotation(self):
        insert = self.parts['payload_insert_1']
        axle = self.parts['planet_2_axle']
        axle_envelope = rotation_envelope(axle)
        self.assertLess(rotation_envelope(insert).intersection(axle_envelope).area, 1e-6)
        angle = math.pi/6
        original_circle = replace(insert, shape=insert.shape.translate((-9*math.cos(angle), -9*math.sin(angle), 0)))
        self.assertGreater(rotation_envelope(original_circle).intersection(axle_envelope).area, 1)

    def test_planet_shift_cannot_hide_under_preload_allowance(self):
        flex, planet = self.parts['flexspline'], self.parts['planet_gear_2']
        contact = next(contact for contact in self.assembly.contacts if {contact.first,contact.second} == {flex.id,planet.id})
        original = Assembly('nominal preload', [flex,planet], [contact])
        self.assertTrue(manufactured_interference(original)['passed'])
        moved = replace(planet, shape=planet.shape.translate((.3,0,0)))
        self.assertFalse(manufactured_interference(Assembly('excess preload',[flex,moved],[contact]))['passed'])

    def test_holder_preload_uses_placed_bore_not_input_parameter(self):
        features = measure_features(self.assembly, self.dimensions)
        self.assertTrue(measured_holder_preload(features,self.parameters.planet_preload)['passed'])
        planet = self.parts['planet_gear_1']
        moved = replace(planet, shape=planet.shape.translate((-.2,0,0)))
        changed = Assembly('shifted holder',[moved if part is planet else part for part in self.assembly.parts])
        features = measure_features(changed,self.dimensions)
        self.assertFalse(measured_holder_preload(features,self.parameters.planet_preload)['passed'])

    def test_three_mm_thread_engagement_tolerates_only_numeric_roundoff(self):
        axle = self.parts['planet_1_axle']
        insert_top = brep_bounds(self.parts['planet_1_axle_insert'].shape).zmax
        tip = brep_bounds(axle.shape).zmin
        for shortage, expected in ((1e-12,True),(.001,False)):
            shifted = replace(axle,shape=axle.shape.translate((0,0,insert_top-3+shortage-tip)))
            assembly = Assembly('engagement threshold',[shifted if part is axle else part for part in self.assembly.parts])
            self.assertEqual(retention_stack(assembly,self.parameters,self.dimensions)['passed'],expected)


if __name__ == '__main__':
    unittest.main()
