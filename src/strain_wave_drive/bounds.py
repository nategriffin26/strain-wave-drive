"""Analytic bounds do not change when OCCT caches a display triangulation."""
from cadquery import BoundBox
from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib


def brep_bounds(shape):
    box = Bnd_Box()
    BRepBndLib.AddOptimal_s(shape.wrapped, box, False, False)
    return BoundBox(box)
