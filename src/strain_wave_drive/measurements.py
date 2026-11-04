"""Read fit dimensions from manufactured solids, independently of parameter values."""
import math
import numpy as np
import cadquery as cq
from OCP.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from .bounds import brep_bounds


def cylinders(shape):
    result = []
    for face in shape.Faces():
        surface = BRepAdaptor_Surface(face.wrapped)
        if face.geomType() != 'CYLINDER':
            continue
        cylinder = surface.Cylinder()
        axis = cylinder.Axis()
        if abs(axis.Direction().Z()) < 1-1e-6:
            continue
        point, bounds = axis.Location(), brep_bounds(face)
        result.append({'radius': cylinder.Radius(), 'x': point.X(), 'y': point.Y(),
                       'z_min': bounds.zmin, 'z_max': bounds.zmax})
    return result


def section_wires(shape, z):
    return cq.Workplane('XY').newObject([shape]).section(z).val().Wires()


def profile(wire):
    arcs = []
    for edge in wire.Edges():
        if edge.geomType() == 'CIRCLE':
            arcs.append((edge, BRepAdaptor_Curve(edge.wrapped).Circle().Radius()))
        elif edge.geomType() == 'BSPLINE':
            points, _ = edge.sample(5)
            radii = [math.hypot(point.x, point.y) for point in points]
            if max(radii)-min(radii) < 1e-5:
                arcs.append((edge, sum(radii)/len(radii)))
    if not arcs:
        raise ValueError('The measured tooth section has no circular root datum.')
    radii = [radius for _, radius in arcs]
    base = min(radii)
    points = np.array([[vertex.X, vertex.Y] for vertex in wire.Vertices()])
    root_points = points[np.abs(np.linalg.norm(points, axis=1)-base) < 1e-5]
    nearest = root_points[np.argsort(root_points[:, 0])[-2:]]
    flats = [edge for edge in wire.Edges() if edge.geomType() == 'LINE' and
             abs(edge.startPoint().x-edge.endPoint().x) < 1e-6]
    floor = max(flats, key=lambda edge: edge.Center().x) if flats else None
    return {'base_radius': base, 'tip_radius': max(radii),
            'count': sum(abs(radius-base) < 1e-5 for radius in radii),
            'root_chord': float(np.linalg.norm(nearest[1]-nearest[0])),
            'flat_width': floor.Length() if floor else None,
            'flat_x': floor.Center().x if floor else None,
            'radial_envelope': float(np.linalg.norm(points, axis=1).max()), 'arcs': arcs}


def helix_angle(shape, lower_z, width, centre, tooth_count, pitch_radius):
    phases = []
    increment = min(.1, width/20)
    for z in (lower_z+width/4, lower_z+width/4+increment):
        wire = max(section_wires(shape, z), key=lambda item: len(item.Edges()))
        data = profile(wire)
        angles = [math.atan2(edge.Center().y-centre[1], edge.Center().x-centre[0])
                  for edge, radius in data['arcs'] if abs(radius-data['base_radius']) < 1e-5]
        phases.append(float(np.angle(np.exp(1j*tooth_count*np.array(angles)).mean())/tooth_count))
    delta = math.atan2(math.sin(tooth_count*(phases[1]-phases[0])), math.cos(tooth_count*(phases[1]-phases[0])))/tooth_count
    return math.degrees(math.atan(abs(delta)*pitch_radius/increment))


def measure_features(assembly, dimensions):
    parts = {part.id: part for part in assembly.parts}
    d = dimensions
    flex = parts['flexspline'].shape
    flex_bounds = brep_bounds(flex)
    wires = section_wires(flex, (flex_bounds.zmin+flex_bounds.zmax)/2)
    outer = max(wires, key=lambda wire: len(wire.Edges()))
    inner = next(wire for wire in wires if wire is not outer)
    external = profile(outer)
    inner_points = np.array([[vertex.X, vertex.Y] for vertex in inner.Vertices()])
    internal_root = float(np.linalg.norm(inner_points, axis=1).max())
    fixed = profile(max(section_wires(parts['circular_spline'].shape, (d['flex_floor_z_mm']+d['fixed_band_top_z_mm'])/2), key=lambda wire: len(wire.Edges())))
    dynamic = profile(max(section_wires(parts['dynamic_spline'].shape, (d['dynamic_band_bottom_z_mm']+d['flex_roof_z_mm'])/2), key=lambda wire: len(wire.Edges())))
    values = {'flex_height': flex_bounds.zlen, 'flex_count': external['count'],
              'circular_count': fixed['count'], 'dynamic_count': dynamic['count'],
              'module': 2*fixed['base_radius']/external['count'],
              'flex_root_diameter': 2*external['base_radius'], 'flex_tip_diameter': 2*external['radial_envelope'],
              'flex_inner_root_diameter': 2*internal_root, 'flex_wall': external['base_radius']-internal_root,
              'flex_root_chord': external['root_chord'], 'flex_tip_chord': external['flat_width'],
              'flex_addendum': external['flat_x']-external['base_radius'],
              'fixed_mouth': fixed['root_chord'], 'fixed_floor_width': fixed['flat_width'],
              'fixed_depth': fixed['flat_x']-fixed['base_radius'],
              'dynamic_mouth': dynamic['root_chord'], 'dynamic_floor_width': dynamic['flat_width'],
              'rigid_tip_diameter': 2*fixed['base_radius'], 'rigid_root_envelope': 2*max(fixed['radial_envelope'],dynamic['radial_envelope']),
              'radial_tip_clearance': fixed['flat_x']-external['flat_x']}
    plate = cylinders(parts['circular_spline'].shape)
    mounting = [face for face in plate if 23 < abs(face['x']) < 24 and 23 < abs(face['y']) < 24]
    through = [face for face in mounting if face['radius'] < 3]
    heads = [face for face in mounting if 3 < face['radius'] < 6]
    pilots = [face for face in plate if abs(math.hypot(face['x'],face['y'])-34.2509) < .1 and face['radius'] < 3]
    if len(through) != 4 or len(heads) != 4 or len(pilots) != 4:
        raise ValueError('Expected four motor through-bores, four head recesses and four housing-joint pilots.')
    values.update(motor_square=max(face['x'] for face in through)-min(face['x'] for face in through),
                  motor_hole_min=2*min(face['radius'] for face in through), motor_hole_max=2*max(face['radius'] for face in through),
                  motor_head_min=2*min(face['radius'] for face in heads), motor_head_max=2*max(face['radius'] for face in heads),
                  insert_pilot_min=2*min(face['radius'] for face in pilots), insert_pilot_max=2*max(face['radius'] for face in pilots),
                  insert_depth_min=min(face['z_max']-face['z_min'] for face in pilots), insert_depth_max=max(face['z_max']-face['z_min'] for face in pilots))
    cap_holes = [face for face in cylinders(parts['output_bearing_cap'].shape) if .8 < face['radius'] < 2.5]
    if len(cap_holes) != 8:
        raise ValueError('Expected eight M3 output-cap bores.')
    values.update(m3_hole_min=2*min(face['radius'] for face in cap_holes), m3_hole_max=2*max(face['radius'] for face in cap_holes))
    hub = parts['sun_gear' if 'sun_gear' in parts else 'roller_hub'].shape
    hub_cylinders = [face for face in cylinders(hub) if math.hypot(face['x'],face['y']) < 1e-6]
    bore = min(hub_cylinders, key=lambda face: abs(face['radius']-3.225))
    flats = [BRepAdaptor_Surface(face.wrapped).Plane().Location().X() for face in hub.Faces() if face.geomType() == 'PLANE' and
             abs(BRepAdaptor_Surface(face.wrapped).Plane().Axis().Direction().X()) > 1-1e-6]
    values.update(shaft_diameter=2*bore['radius'], shaft_flat=min(flats,key=lambda x:abs(x-2.725)))
    if 'planet_gear_1' in parts:
        gear = parts['planet_gear_1'].shape
        lower, upper = brep_bounds(gear).zmin, brep_bounds(gear).zmax
        faces = cylinders(gear)
        centre = (faces[0]['x'],faces[0]['y'])
        local = gear.translate((-centre[0],-centre[1],0))
        gear_profile = profile(max(section_wires(local, lower+(upper-lower)*.25),key=lambda wire:len(wire.Edges())))
        normal_module = (gear_profile['tip_radius']-gear_profile['base_radius'])/2.25
        pitch = gear_profile['base_radius']+1.25*normal_module
        ring_profile = profile(inner)
        ring_pitch = ring_profile['base_radius']+1.25*normal_module
        sun_profile = profile(max(section_wires(hub, lower+(upper-lower)*.25),key=lambda wire:len(wire.Edges())))
        pockets = [face for face in faces if 4.8 < face['radius'] < 5.4]
        values.update(planet_count=gear_profile['count'], ring_count=ring_profile['count'], sun_count=sun_profile['count'],
                      normal_module=normal_module, helix_angle=helix_angle(local,lower,upper-lower,(0,0),gear_profile['count'],pitch),
                      planet_axis=math.hypot(*centre), planet_preload=math.hypot(*centre)-(ring_pitch-pitch),
                      pocket_diameter=2*max(face['radius'] for face in pockets),
                      pocket_depth=max(face['z_max']-face['z_min'] for face in pockets), planet_pitch_radius=pitch, ring_pitch_radius=ring_pitch)
    else:
        roller = max(cylinders(parts['roller_1_lower'].shape),key=lambda face:face['radius'])
        bore = min(face['radius'] for face in cylinders(flex))
        axis = math.hypot(roller['x'],roller['y'])
        values.update(flex_bore=2*bore,roller_axis=axis,roller_od=2*roller['radius'],roller_preload=axis+roller['radius']-bore)
    return values
