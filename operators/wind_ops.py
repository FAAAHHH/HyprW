# HyprW — Wind
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Wind acts on whitewater in the air. FLIP Fluids used to compute air drag
# against STILL air:
#
#     dragvec = -dragCoefficient * dp.velocity * dt
#
# HyprW extends the engine so that it is computed against MOVING air
# (see engine_patch/ in the project folder):
#
#     relv    = dp.velocity - windVelocity
#     dragvec = -dragCoefficient * relv * dt
#
# Everything else follows by itself: a drop flying with the wind feels no
# drag; a drop at rest is accelerated to wind speed. And because this only
# applies to particles in the AIR, the water in the pool stays untouched —
# no weights, no helper objects.
#
# Rejected approaches (all measured, twelve test bakes):
#   tilting gravity        works, but tilts the whole scene including the pool
#   curve flow_strength    no effect, does not scale
#   curve strength         pulls towards a point instead of in a direction
#   surface strength       strong, but WITHOUT direction (mirror test: wind to
#                          the left and to the right gave the same image)

import json
import os
import bpy
from mathutils import Vector
from ..utils import flip_bridge

DATEI = "hydrokit_wind.json"
ERDE = Vector((0.0, 0.0, -9.81))


def finde_windobjekte(context):
    """All native Blender force fields that work as wind."""
    return [obj for obj in context.scene.objects
            if getattr(obj, "field", None) is not None
            and obj.field.type in {'WIND', 'FORCE'}
            and not obj.name.startswith("HK_Wind_")]


def windvektor(context, faktor=1.0):
    """Summed wind vector in m/s. Blender's WIND blows along its +Z."""
    gesamt = Vector((0.0, 0.0, 0.0))
    for wind in finde_windobjekte(context):
        richtung = (wind.matrix_world.to_quaternion()
                    @ Vector((0.0, 0.0, 1.0))).normalized()
        gesamt += richtung * wind.field.strength
    return gesamt * faktor


def windpfad(context):
    cache = flip_bridge.get_cache_directory(context)
    return os.path.join(cache, DATEI) if cache else None


def gespeicherter_wind(context):
    """What takes effect at the next bake — or None."""
    pfad = windpfad(context)
    if not pfad or not os.path.isfile(pfad):
        return None
    try:
        with open(pfad) as f:
            d = json.load(f)
        return Vector((d.get("x", 0.0), d.get("y", 0.0), d.get("z", 0.0)))
    except (OSError, ValueError):
        return None


class HYDROKIT_OT_sync_wind(bpy.types.Operator):
    bl_idname = "hydrokit.sync_wind"
    bl_label = "Apply Wind"
    bl_description = (
        "Transfers Blender's wind objects to the simulation. Acts on whitewater "
        "in the air, not on the water in the pool"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain(context) is not None

    def execute(self, context):
        props = context.scene.hydrokit
        winde = finde_windobjekte(context)
        if not winde:
            self.report({'WARNING'},
                        "No wind object found. Add -> Force Field -> Wind")
            return {'CANCELLED'}

        pfad = windpfad(context)
        if pfad is None:
            self.report({'ERROR'}, "Domain has no cache directory")
            return {'CANCELLED'}

        wind = windvektor(context, props.wind_faktor)
        os.makedirs(os.path.dirname(pfad), exist_ok=True)
        with open(pfad, "w") as f:
            json.dump({"x": wind.x, "y": wind.y, "z": wind.z}, f)

        # Clean up leftovers of earlier approaches
        d = flip_bridge.get_domain_props(context)
        if d is not None:
            d.world.gravity_type = 'GRAVITY_TYPE_SCENE'
            d.world.gravity = ERDE
        for o in [x for x in bpy.data.objects if x.name.startswith("HK_Wind_")]:
            bpy.data.objects.remove(o, do_unlink=True)

        self.report({'INFO'},
                    f"Wind {wind.length:.1f} m/s applied "
                    f"({wind.x:+.1f}, {wind.y:+.1f}, {wind.z:+.1f}) — "
                    "needs a new bake")
        return {'FINISHED'}


class HYDROKIT_OT_remove_wind(bpy.types.Operator):
    bl_idname = "hydrokit.remove_wind"
    bl_label = "Remove Wind"
    bl_description = "Takes the wind out of the simulation"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain(context) is not None

    def execute(self, context):
        pfad = windpfad(context)
        if pfad and os.path.isfile(pfad):
            os.remove(pfad)
        d = flip_bridge.get_domain_props(context)
        if d is not None:
            d.world.gravity_type = 'GRAVITY_TYPE_SCENE'
            d.world.gravity = ERDE
        weg = [o for o in bpy.data.objects if o.name.startswith("HK_Wind_")]
        for o in weg:
            bpy.data.objects.remove(o, do_unlink=True)
        self.report({'INFO'}, "Wind removed — needs a new bake")
        return {'FINISHED'}


classes = (HYDROKIT_OT_sync_wind, HYDROKIT_OT_remove_wind)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
