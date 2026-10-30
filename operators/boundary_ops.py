# HyprW — banks and domain borders
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The most common annoyance with streams and rivers: the water climbs up the
# invisible domain walls, piles up there and overflows. The reason is that
# by default the domain is a solid wall on ALL SIX sides
# (fluid_boundary_collisions = six times True).
#
# Remedy: open the side in flow direction — then it acts as an outflow
# instead of a dam — and set the wall friction to 0.
#
# Order of the six values (from ui/domain_simulation_ui.py):
#     0 = X-   1 = X+   2 = Y-   3 = Y+   4 = Z-   5 = Z+

import bpy
from mathutils import Vector
from ..utils import flip_bridge

SEITEN = ("X-", "X+", "Y-", "Y+", "Z-", "Z+")

def _index_fuer(achse, vorzeichen):
    """achse: 0=X, 1=Y, 2=Z; vorzeichen: -1 or +1."""
    return achse * 2 + (1 if vorzeichen > 0 else 0)


def fliessrichtung(context):
    """Main horizontal flow direction as (achse, vorzeichen).

    Taken from the inflow objects first, otherwise from the horizontal part
    of gravity (which HyprW uses for wind). None if nothing fits.
    """
    gesamt = Vector((0.0, 0.0, 0.0))
    for obj in context.scene.objects:
        ff = getattr(obj, "flip_fluid", None)
        if ff is None or getattr(ff, "object_type", "") != "TYPE_INFLOW":
            continue
        v = getattr(ff.inflow, "inflow_velocity", None)
        if v is not None:
            gesamt += Vector((v[0], v[1], 0.0))

    if gesamt.length < 1e-4:
        d = flip_bridge.get_domain_props(context)
        if d is not None:
            g = d.world.gravity
            gesamt = Vector((g[0], g[1], 0.0))

    if gesamt.length < 1e-4:
        return None
    achse = 0 if abs(gesamt.x) >= abs(gesamt.y) else 1
    vorzeichen = 1 if gesamt[achse] > 0 else -1
    return achse, vorzeichen


class HYDROKIT_OT_open_banks(bpy.types.Operator):
    bl_idname = "hydrokit.open_banks"
    bl_label = "Open Banks"
    bl_description = (
        "Opens the domain side in flow direction as an outflow and sets the "
        "wall friction to 0 — against water that climbs up the sides and "
        "overflows"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain_props(context) is not None

    def execute(self, context):
        props = context.scene.hydrokit
        d = flip_bridge.get_domain_props(context)

        richtung = fliessrichtung(context)
        if richtung is None:
            self.report({'WARNING'},
                        "No flow direction detectable — the inflow needs a "
                        "horizontal velocity, or set wind")
            return {'CANCELLED'}
        achse, vorzeichen = richtung

        kollisionen = list(d.simulation.fluid_boundary_collisions)
        offen = [_index_fuer(achse, vorzeichen)]
        if props.beide_ufer:
            offen.append(_index_fuer(achse, -vorzeichen))
        for i in offen:
            kollisionen[i] = False
        d.simulation.fluid_boundary_collisions = kollisionen

        # No wall friction: otherwise the domain slows the water at the sides
        if hasattr(d.world, "boundary_friction"):
            d.world.boundary_friction = 0.0

        namen = ", ".join(SEITEN[i] for i in offen)
        self.report({'INFO'},
                    f"Side {namen} opened, wall friction 0 — needs a new bake")
        return {'FINISHED'}


class HYDROKIT_OT_close_banks(bpy.types.Operator):
    bl_idname = "hydrokit.close_banks"
    bl_label = "Close Borders"
    bl_description = "All six domain sides solid walls again"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain_props(context) is not None

    def execute(self, context):
        d = flip_bridge.get_domain_props(context)
        d.simulation.fluid_boundary_collisions = (True,) * 6
        self.report({'INFO'}, "All borders closed — needs a new bake")
        return {'FINISHED'}


classes = (HYDROKIT_OT_open_banks, HYDROKIT_OT_close_banks)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
