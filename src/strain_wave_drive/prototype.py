"""Public prototype measurements remain independent of the generator's defaults."""
import math
from OCP.BRepAdaptor import BRepAdaptor_Surface
from .parameters import preset, Variant
from .measurements import measure_features, cylinders


def compare(p, assembly, d, reference):
    parts = {part.id: part for part in assembly.parts}
    actual = measure_features(assembly, d)
    def measured(name, value):
        actual[name] = float(value)
    def coaxial_diameter(shape, nominal):
        candidates = []
        for face in shape.Faces():
            if face.geomType() != 'CYLINDER':
                continue
            surface = BRepAdaptor_Surface(face.wrapped).Cylinder()
            center = surface.Location()
            if math.hypot(center.X(), center.Y()) < 1e-6:
                candidates.append(2 * surface.Radius())
        if not candidates:
            raise ValueError('Expected a coaxial cylindrical fit surface.')
        return min(candidates, key=lambda diameter: abs(diameter - nominal))
    def rib_axes(shape, nominal_radius):
        result = []
        for face in shape.Faces():
            if face.geomType() != 'CYLINDER':
                continue
            surface = BRepAdaptor_Surface(face.wrapped).Cylinder()
            center = surface.Location()
            radius = math.hypot(center.X(), center.Y())
            if abs(surface.Radius() - .4) < 1e-6 and abs(radius - nominal_radius) < 1:
                result.append(radius)
        if not result:
            raise ValueError('Expected Ø0.8 crush-rib surfaces.')
        return result
    measured('journal_base', coaxial_diameter(parts['journal_sleeve'].shape, 79.6))
    measured('journal_crest', 2 * (max(rib_axes(parts['journal_sleeve'].shape, 40)) + .4))
    bearing_od = 2*max(face['radius'] for face in cylinders(parts['output_bearing'].shape))
    measured('seat_base_clearance', coaxial_diameter(parts['dynamic_spline'].shape, bearing_od+.6)-bearing_od)
    measured('seat_crest_clearance', 2*(min(rib_axes(parts['dynamic_spline'].shape,bearing_od/2))-.4)-bearing_od)
    measured('motor_pilot', coaxial_diameter(parts['circular_spline'].shape, 38.2))
    version = 'fusion_v12' if p.variant is Variant.PLANETARY else 'fusion_v83'
    fields = {
        'tooth_counts': [('module', actual['module'], 'fusion_v83.module_mm'), ('flex_teeth', actual['flex_count'], 'fusion_v83.flex_teeth'), ('circular_teeth', actual['circular_count'], 'fusion_v83.circular_teeth'), ('dynamic_teeth', actual['dynamic_count'], 'fusion_v83.dynamic_teeth')],
        'flex_tooth_form': [('root_chord', actual['flex_root_chord'], 'fusion_v83.root_chord_mm'), ('tip_chord', actual['flex_tip_chord'], 'fusion_v83.tip_chord_mm'), ('radial_addendum', actual['flex_addendum'], 'fusion_v83.radial_addendum_from_root_circle_mm')],
        'fixed_slot_form': [('mouth_width', actual['fixed_mouth'], 'fusion_v83.mouth_width_mm'), ('root_width', actual['fixed_floor_width'], 'fusion_v83.root_width_mm'), ('depth', actual['fixed_depth'], 'fusion_v83.slot_depth_mm')],
        'dynamic_slot_form': [('mouth_width', actual['dynamic_mouth'], 'fusion_v83.mouth_width_mm'), ('root_width', actual['dynamic_floor_width'], 'fusion_v83.root_width_mm')],
        'flex_diameters': [('root_diameter', actual['flex_root_diameter'], version+'.root_diameter_mm'), ('tip_diameter', actual['flex_tip_diameter'], version+'.tip_diameter_mm')],
        'rigid_diameters': [('tip_diameter', actual['rigid_tip_diameter'], 'fusion_v83.tip_diameter_mm'), ('root_envelope', actual['rigid_root_envelope'], 'fusion_v83.root_envelope_diameter_mm')],
        'radial_tip_clearance': [('tip_to_slot_floor', actual['radial_tip_clearance'], version+'.tip_to_slot_floor_mm')],
        'sun_shaft_fit': [('diameter', actual['shaft_diameter'], 'printed_prototype.sun_bore_mm'), ('flat', actual['shaft_flat'], 'printed_prototype.sun_flat_x_mm')],
        'output_inner_fit': [('base_journal', actual['journal_base'], 'fusion_v83.base_journal_mm'), ('crest_journal', actual['journal_crest'], 'fusion_v83.crest_journal_mm')],
        'output_outer_fit': [('base_diametral_clearance', actual['seat_base_clearance'], 'clearance.base_diametral_mm'), ('crest_diametral_clearance', actual['seat_crest_clearance'], 'clearance.crest_diametral_mm')],
        'motor_pilot': [('pilot_bore', actual['motor_pilot'], 'fusion_v83.pilot_bore_mm')],
        'motor_bolts': [('square_pitch', actual['motor_square'], 'fusion_v83.square_pitch_mm'), ('minimum_hole', actual['motor_hole_min'], 'fusion_v83.spline_hole_mm'), ('maximum_hole', actual['motor_hole_max'], 'fusion_v83.spline_hole_mm')],
        'motor_head_recess': [('minimum_diameter', actual['motor_head_min'], 'fusion_v83.diameter_mm'), ('maximum_diameter', actual['motor_head_max'], 'fusion_v83.diameter_mm')],
        'insert_fixed': [('minimum_pilot', actual['insert_pilot_min'], 'fusion_v83.diameter_mm'), ('maximum_pilot', actual['insert_pilot_max'], 'fusion_v83.diameter_mm'), ('minimum_depth', actual['insert_depth_min'], 'fusion_v83.straight_depth_mm'), ('maximum_depth', actual['insert_depth_max'], 'fusion_v83.straight_depth_mm')],
        'm3_holes': [('minimum_hole', actual['m3_hole_min'], 'fusion_v83.diameter_mm'), ('maximum_hole', actual['m3_hole_max'], 'fusion_v83.diameter_mm')],
    }
    if p.variant is Variant.PLAIN:
        fields['plain_flex_wall'] = [('bore', actual['flex_bore'], 'fusion_v83.bore_diameter_mm'), ('wall', actual['flex_wall'], 'fusion_v83.wall_mm'), ('height', actual['flex_height'], 'fusion_v83.height_mm')]
        fields['plain_roller_preload'] = [('axis_radius', actual['roller_axis'], 'fusion_v83.axis_radius_mm'), ('roller_od', actual['roller_od'], 'fusion_v83.roller_od_mm'), ('radial_clearance', -actual['roller_preload'], 'clearance.radial_clearance_mm')]
    else:
        fields['planetary_flex_wall'] = [('minimum_web', actual['flex_wall'], 'fusion_v12.minimum_web_mm'), ('height', actual['flex_height'], 'fusion_v12.height_mm'), ('internal_root_diameter', actual['flex_inner_root_diameter'], 'fusion_v12.inner_root_diameter_mm')]
        fields['planetary_gear_geometry'] = [('sun_teeth', actual['sun_count'], 'fusion_v12.sun_teeth'), ('planet_teeth', actual['planet_count'], 'fusion_v12.planet_teeth'), ('ring_teeth', actual['ring_count'], 'fusion_v12.ring_teeth'), ('normal_module', actual['normal_module'], 'fusion_v12.normal_module_mm'), ('helix_angle_deg', actual['helix_angle'], 'fusion_v12.helix_deg')]
        fields['planetary_preload'] = [('holder_axis_radius', actual['planet_axis'], 'fusion_v12.holder_axis_radius_mm'), ('radial_clearance', -actual['planet_preload'], 'clearance.holder_constrained_radial_clearance_mm')]
        fields['planet_bearing_seat'] = [('pocket_diameter', actual['pocket_diameter'], 'fusion_v12.pocket_diameter_mm'), ('pocket_depth', actual['pocket_depth'], 'fusion_v12.pocket_depth_mm')]
    changes = {
        'tooth_bands': 'The compact package uses two 7.8 mm tooth bands with 0.4 mm axial separation by default.',
        'plain_flex_wall': 'The plain preset uses the prototype smooth-bore flex; the planetary preset uses its herringbone alternative.',
        'intermediate_flex': 'The intermediate 16.6 mm internally toothed flex is excluded in favour of the two 16 mm prototype variants.',
        'planetary_flex_wall': 'The planetary preset uses the prototype herringbone flex; the plain preset uses its smooth-bore alternative.',
        'plain_roller_preload': 'The plain preset retains roller preload; the planetary preset uses geared preload instead.',
        'roller_axles': 'A split hub and cap with 0.5 mm inner-race end shims replace the open carrier windows to clamp the axles without dragging the rollers.',
        'carrier_center_bore': 'A split D-shaft clamp replaces the flange coupling; its lower 4 mm is round because the motor flat starts 6 mm above the mounting face.',
        'planetary_gear_geometry': 'The planetary preset retains the prototype tooth counts and helix; the plain preset has no planetary gears.',
        'planetary_preload': 'The planetary holder retains the measured 26.1 mm axis radius, rather than the unconstrained 25.9 mm gear position in the original CAD.',
        'planetary_axial_coverage': f'Planet face width is {p.flex_height:g} mm instead of the prototype 11 mm; the 16 mm default spans the full flex height.',
        'planet_bearing_seat': 'The centre bore increases from 3.4 to 5.2 mm to clear the catalog Ø5 shims and Ø4.5 spacer without rubbing the rotating gear.',
        'planet_holder_holes': 'The split carrier uses Ø3.2 axle clearance and Ø4.2 insert pilots for the catalog M3 clamp screws.',
        'output_bearing_identity': f'The selected {p.output_bearing.value} is {d["bearing_id_mm"]:g}×{d["bearing_od_mm"]:g}×{d["bearing_width_mm"]:g} mm; the 6016 option retains the prototype 80×125×22 mm bearing size.',
        'output_outer_fit': 'The seat follows the selected bearing OD while retaining 0.6 mm base diametral clearance and 0.219 mm rib-crest interference.',
        'output_inner_fit': 'Journal length follows the 10 or 22 mm bearing width; the prototype Ø79.6 base and Ø80.4 rib crests remain.',
        'output_axial_seat': 'Opposing shoulders capture the selected catalog bearing width, replacing the prototype end-cap alternatives; trial the actual bearing before tightening the cap.',
        'central_bearing_identity': 'A 6702 carrier-to-sun support replaces the prototype 6706 carrier-to-output support, removing the bearing connection to the output.',
        'central_outer_fit': 'The Ø21 support seat uses 25 Ø0.8 crush ribs and an upper outer-race shoulder opposed by a lower sun shoulder.',
        'central_inner_fit': 'The 15 mm support bearing uses a 14.9 mm clearance journal instead of the prototype 30.2 mm interference journal; calibrate bearing_offset with the fit plate.',
        'other_owned_bearings': 'The BOM includes only the bearings used by the selected mechanism.',
        'insert_backplate': 'Arm-mount pilots are omitted; four housing-joint inserts remain in the motor plate rather than the journal sleeve.',
        'insert_drum': 'Eight cap inserts move above the bearing and six payload inserts occupy the roof bosses; pilots remain Ø4.2 plus insert_offset.',
        'insert_carrier': 'The shaft clamp uses one radial insert instead of four flange-coupling inserts.',
        'insert_axle': 'Two lower axle inserts receive full-length screws that clamp catalog metal inner-race backstops.',
        'm3_head_recess': 'Head recesses follow the selected ISO 4762 and DIN 7984 screws and their assembly tool paths.',
        'endcap_fasteners': 'Eight Ø3.2 holes and M3 screws replace the prototype end cap’s incompatible Ø2.2 and Ø2.9 holes.',
        'motor_bolts': 'M4 through-bolts replace M5 because M5 nuts do not clear the motor’s rear recesses; the 47.14 mm square and Ø5.2 housing holes remain.',
        'motor_head_recess': 'M4 heads retain the prototype 1.65 mm seating floor and are tightened before the cartridge is installed.',
        'motor_wrap': 'The motor void has 0.2 mm normal clearance by default; the thin journal corner wall must be checked with the motor-corner coupon.',
        'coupling': 'The printed split shaft clamp replaces the catalog flange coupling without adding custom metal parts.',
        'flex_axial': f'The cavity is {p.flex_height+p.flex_axial_clearance:g} mm high instead of 16.55 mm; zero default axial clearance removes flex float but requires a PA6 growth and fit trial.',
        'fixed_dynamic_axial': 'Tooth-band separation decreases from the prototype 0.9 mm to 0.4 mm.',
        'fixed_dynamic_radial': 'The rotating drum’s radial clearance around the fixed spline decreases from 2.3 mm to 0.5 mm.',
        'endcap_backplate_gap': 'Separate inner- and outer-race shoulders have 5.5 mm radial separation with the default bearing.',
        'carrier_floor_gap': 'The lower carrier runs above a recessed motor plate; the full assembly is checked instead of preserving the prototype’s isolated 0.4 mm floor gap.',
        'central_bearing_axial': 'Opposing sun and carrier shoulders retain the 6702 without roof contact; the report includes planet-pocket endplay and a stated axial stack scenario.',
        'output_attachment': 'Six M3 roof inserts on a 68 mm pitch circle replace the arm-specific eight-hole, 80 mm pattern and clear the rotating axle heads.',
    }
    baseline = preset(p.variant.value).dictionary()
    configured = p.dictionary()
    overrides = {key: value for key, value in configured.items() if value != baseline[key]}
    default_geometry = not any(key != 'output_bearing' for key in overrides)
    records = []
    for source in reference['records']:
        key = source['id']
        comparisons = []
        for name, value, source_path in fields.get(key, []):
            section, field = source_path.split('.')
            expected = source[section][field]
            tolerance = 0 if 'teeth' in name else reference['default_tolerance_mm']
            comparisons.append({'dimension': name, 'actual': float(value), 'prototype': expected, 'tolerance': tolerance,
                                'measured': True, 'passed': abs(value-expected) <= tolerance})
        if not comparisons and key not in changes:
            raise ValueError('Prototype record has no explicit disposition: ' + key)
        passed = all(c['passed'] for c in comparisons)
        status = ('preserved' if passed else 'mismatch') if comparisons else 'deliberate_change'
        if comparisons and not passed and not default_geometry:
            status = 'parameter_override'
        records.append({'id': key, 'fit': source['fit'], 'status': status, 'comparisons': comparisons,
                        'deliberate_change': changes.get(key), 'source': 'Printed prototype, January 2026; Fusion design v83/v12'})
    return {'name': 'Prototype fit comparison', 'passed': all(r['status'] != 'mismatch' for r in records),
            'parameter_overrides': overrides, 'measured_features': actual,
            'measurement_basis': 'All Generated values are extracted from BRep sections, cylindrical faces or plane locations. Tooth counts count section root arcs. Harmonic module uses measured rigid tip diameter/flex count; planetary module uses measured tip-minus-root radius divided by 2.25. Helix angle uses root-arc phase change over a measured 0.1 mm axial increment. No parameter-only value enters the fit pass/fail gate.',
            'records': records}
