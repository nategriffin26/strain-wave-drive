"""Small contact margins around measured defaults and nominal mating geometry.

The unscaled BRep intersection volumes are preserved in reference/contact-baselines.json.
"""
import math

RELATIVE_MARGIN = .05
ABSOLUTE_MARGIN_MM3 = .05
FIXED_FLEX_BASELINE = {'plain': 175.3908758297145, 'planetary': 315.949682150587}
PLANET_FLEX_BASELINE = 4.45120991
JOURNAL_RIB_AREA = 2.465432279510133
OUTER_SEAT_RIB_AREA = .9908713367834942
CARRIER_SEAT_RIB_AREA = 1.0181815729146663


def allowance(nominal_overlap):
    return nominal_overlap*(1+RELATIVE_MARGIN)+ABSOLUTE_MARGIN_MM3


def insert_overlap(offset):
    pilot = (4.2+offset)/2
    return math.pi*(3.4*max(0, 2.5**2-pilot**2)+.6*max(0, 2.125**2-pilot**2))


def rib_area_scale(default_penetration, diametral_offset):
    def segment(depth):
        radius = .4
        depth = min(2*radius, max(0, depth))
        return radius*radius*math.acos((radius-depth)/radius)-(radius-depth)*math.sqrt(max(0,2*radius*depth-depth*depth))
    return segment(default_penetration-diametral_offset/2)/segment(default_penetration)


def roller_overlap(parameters):
    outer, inner, distance = 5.0, parameters.flex_root_radius-parameters.flex_wall, parameters.roller_axis_radius
    if distance+outer <= inner:
        return 0.0
    alpha = math.acos(max(-1,min(1,(distance*distance+outer*outer-inner*inner)/(2*distance*outer))))
    beta = math.acos(max(-1,min(1,(distance*distance+inner*inner-outer*outer)/(2*distance*inner))))
    radical = (-distance+outer+inner)*(distance+outer-inner)*(distance-outer+inner)*(distance+outer+inner)
    intersection = outer*outer*alpha+inner*inner*beta-.5*math.sqrt(max(0,radical))
    return 4*(math.pi*outer*outer-intersection)
