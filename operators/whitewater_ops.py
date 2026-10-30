# HyprW — automatic whitewater size
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from ..utils import flip_bridge


def compute_scales(context):
    """Computes the display size per particle type from the voxel size.

    Returns {'foam': 0.032, ...}, or None if there is no domain.
    """
    voxel = flip_bridge.get_voxel_size(context)
    if not voxel:
        return None
    props = context.scene.hydrokit
    faktoren = {
        "foam":   props.scale_foam,
        "bubble": props.scale_bubble,
        "spray":  props.scale_spray,
        "dust":   props.scale_dust,
    }
    gesamt = props.whitewater_scale_multiplier
    return {art: voxel * f * gesamt for art, f in faktoren.items()}


class HYDROKIT_OT_auto_whitewater_scale(bpy.types.Operator):
    bl_idname = "hydrokit.auto_whitewater_scale"
    bl_label = "Compute Whitewater Size"
    bl_description = (
        "Sets the display size of all whitewater particles to match the "
        "voxel size of the domain. Does not need a new bake"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain(context) is not None

    def execute(self, context):
        skalen = compute_scales(context)
        if skalen is None:
            self.report({'ERROR'}, "No domain, or resolution not readable")
            return {'CANCELLED'}

        objekte = flip_bridge.get_whitewater_objects(context)
        if not objekte:
            self.report({'WARNING'},
                        "No whitewater objects found. Bake with whitewater first")
            return {'CANCELLED'}

        gesetzt, fehlgeschlagen = [], []
        for art, obj in objekte.items():
            wert = skalen.get(art)
            if wert is None:
                continue
            if flip_bridge.set_whitewater_particle_scale(obj, wert):
                gesetzt.append(f"{art} {wert * 100:.1f} cm")
            else:
                fehlgeschlagen.append(art)

        if fehlgeschlagen:
            self.report({'WARNING'},
                        "Cannot be set: " + ", ".join(fehlgeschlagen))
        if gesetzt:
            self.report({'INFO'}, "Set: " + " | ".join(gesetzt))
            return {'FINISHED'}
        return {'CANCELLED'}


# --------------------------------------------------------------- Look
# By default FLIP Fluids displays whitewater as a "Point Cloud". Cycles
# renders a point cloud as equally sized spheres.
#
# CAREFUL, MEASURED: the "white little balls" impression usually does NOT come
# from the display mode but from the framing. From 1.7 m at 85 mm a single
# 5 mm drop is centimetres large in the image — there is no fine whitewater
# left. From 3.4 m or further away the same simulation reads perfectly.
# So only use this function once the framing is right.
#
# In instance mode the same number for the size looks clearly LARGER than
# with a point cloud. So the multiplier is compensated here as well,
# otherwise you get chunks instead of whitewater.
#
# In instance mode, on the other hand, several controls take effect that FLIP
# ships but never sets. The most important is "Align Instance to Velocity":
# particles are stretched along their motion, spheres become streaks. Plus
# size variation, so not everything is the same size, and shrinking over
# the lifetime, so whitewater fades instead of vanishing.
AUSGLEICH = 0.35      # instances look larger than points

AUSSEHEN = {
    "foam": {
        # Foam lies on the surface, moves sluggishly, clumps
        flip_bridge.SOCKET_DISPLAY_MODE:   "Instancing",
        flip_bridge.SOCKET_INSTANCING:     "Icosphere",
        flip_bridge.SOCKET_SCALE_RANDOM:   0.75,
        flip_bridge.SOCKET_RANDOM_BIAS:    1.0,
        flip_bridge.SOCKET_ROT_RANDOM:     1.0,
        flip_bridge.SOCKET_ALIGN_VELOCITY: 0.25,
        flip_bridge.SOCKET_DUPLICATE:      True,
        flip_bridge.SOCKET_NUM_DUPLICATES: 2,
        flip_bridge.SOCKET_DIST_RADIUS:    0.6,
        flip_bridge.SOCKET_LIFETIME_SCALE: True,
        flip_bridge.SOCKET_FINAL_SCALE:    0.25,
        flip_bridge.SOCKET_SCALE_DURATION: 0.6,
        flip_bridge.SOCKET_SHADE_SMOOTH:   True,
    },
    "bubble": {
        # Bubbles are inside the water, round, barely stretched
        flip_bridge.SOCKET_DISPLAY_MODE:   "Instancing",
        flip_bridge.SOCKET_INSTANCING:     "Icosphere",
        flip_bridge.SOCKET_SCALE_RANDOM:   0.6,
        flip_bridge.SOCKET_ROT_RANDOM:     1.0,
        flip_bridge.SOCKET_ALIGN_VELOCITY: 0.1,
        flip_bridge.SOCKET_LIFETIME_SCALE: True,
        flip_bridge.SOCKET_FINAL_SCALE:    0.4,
        flip_bridge.SOCKET_SHADE_SMOOTH:   True,
    },
    "spray": {
        # Spray flies fast: the stretching has the strongest effect here
        flip_bridge.SOCKET_DISPLAY_MODE:   "Instancing",
        flip_bridge.SOCKET_INSTANCING:     "Icosphere",
        flip_bridge.SOCKET_SCALE_RANDOM:   0.9,
        flip_bridge.SOCKET_RANDOM_BIAS:    1.2,
        flip_bridge.SOCKET_ROT_RANDOM:     1.0,
        flip_bridge.SOCKET_ALIGN_VELOCITY: 1.0,
        flip_bridge.SOCKET_LIFETIME_SCALE: True,
        flip_bridge.SOCKET_FINAL_SCALE:    0.15,
        flip_bridge.SOCKET_SCALE_DURATION: 0.5,
        flip_bridge.SOCKET_SHADE_SMOOTH:   True,
    },
    "dust": {
        # Dust stays a point cloud: very many, very small particles, and
        # instances would be a pure waste of computation here
        flip_bridge.SOCKET_DISPLAY_MODE:   "Point Cloud",
        flip_bridge.SOCKET_SCALE_RANDOM:   1.0,
        flip_bridge.SOCKET_LIFETIME_SCALE: True,
        flip_bridge.SOCKET_FINAL_SCALE:    0.1,
    },
}


class HYDROKIT_OT_whitewater_look(bpy.types.Operator):
    bl_idname = "hydrokit.whitewater_look"
    bl_label = "Set Whitewater Look"
    bl_description = (
        "Switches whitewater from equally sized spheres to stretched, "
        "variously sized particles. Does not need a new bake"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(flip_bridge.get_whitewater_objects(context))

    def execute(self, context):
        objekte = flip_bridge.get_whitewater_objects(context)
        if not objekte:
            self.report({'WARNING'}, "No whitewater objects found")
            return {'CANCELLED'}

        bericht = []
        for art, obj in objekte.items():
            werte = dict(AUSSEHEN.get(art, {}))
            if not werte:
                continue
            # Size compensation: instances look larger than points
            if werte.get(flip_bridge.SOCKET_DISPLAY_MODE) == "Instancing":
                werte[flip_bridge.SOCKET_PARTICLE_SCALE_MULT] = AUSGLEICH
            n = flip_bridge.set_sockets(obj, werte)
            bericht.append(f"{art} {n}/{len(werte)}")

        # Without motion blur every particle is a frozen sphere.
        # FLIP writes the motion data anyway, so it costs little.
        context.scene.render.use_motion_blur = True

        self.report({'INFO'}, "Set: " + ", ".join(bericht)
                    + " — motion blur on")
        return {'FINISHED'}


class HYDROKIT_OT_whitewater_look_reset(bpy.types.Operator):
    bl_idname = "hydrokit.whitewater_look_reset"
    bl_label = "Back to Point Cloud"
    bl_description = "Restore FLIP's default display (faster)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(flip_bridge.get_whitewater_objects(context))

    def execute(self, context):
        for art, obj in flip_bridge.get_whitewater_objects(context).items():
            flip_bridge.set_sockets(obj, {
                flip_bridge.SOCKET_DISPLAY_MODE: "Point Cloud",
                flip_bridge.SOCKET_DUPLICATE: False,
                flip_bridge.SOCKET_LIFETIME_SCALE: False,
                flip_bridge.SOCKET_PARTICLE_SCALE_MULT: 1.0,
            })
        self.report({'INFO'}, "Back to point cloud")
        return {'FINISHED'}


classes = (HYDROKIT_OT_auto_whitewater_scale,
           HYDROKIT_OT_whitewater_look,
           HYDROKIT_OT_whitewater_look_reset)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
