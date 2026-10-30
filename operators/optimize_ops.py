# HyprW — optimisation
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Compute time and memory of a FLIP simulation depend on a few knobs that
# you only learn about after many lost hours. This module makes them
# visible and offers three levels.
#
# The most important number is the resolution: it counts CUBICALLY. Halving
# it means an eighth of the memory and roughly an eighth of the compute time.

import bpy
from ..utils import flip_bridge
from . import doctor_ops

# Level -> (title, description, list of (path, value))
STUFEN = {
    'VORSCHAU': (
        "Preview",
        "Quickly see whether the motion is right. Not for rendering.",
        [
            ("simulation.resolution", 45),
            ("surface.subdivisions", 0),
            ("whitewater.enable_whitewater_simulation", False),
            ("advanced.CFL_condition_number", 5),
        ],
    ),
    'ARBEIT': (
        "Working",
        "Good balance for tuning material and camera.",
        [
            ("simulation.resolution", 110),
            ("surface.subdivisions", 1),
            ("whitewater.enable_whitewater_simulation", True),
            ("advanced.CFL_condition_number", 3),
        ],
    ),
    'FINAL': (
        "Final",
        "Full quality. Check the memory requirement first.",
        [
            ("simulation.resolution", 200),
            ("surface.subdivisions", 2),
            ("whitewater.enable_whitewater_simulation", True),
            ("advanced.CFL_condition_number", 2),
        ],
    ),
}


def kosten_schaetzen(context, aufloesung=None):
    """Memory and rough compute time for a resolution.

    Returns: (voxel, gigabyte, faktor_gegen_jetzt) or None.
    """
    d = flip_bridge.get_domain_props(context)
    dom = flip_bridge.get_domain(context)
    if d is None or dom is None:
        return None
    jetzt = d.simulation.resolution
    ziel = aufloesung if aufloesung else jetzt
    if jetzt <= 0:
        return None

    laengste = max(dom.dimensions)
    if laengste <= 0:
        return None
    kante = laengste / ziel
    voxel = 1
    for mass in dom.dimensions:
        voxel *= max(int(round(mass / kante)), 1)

    mit_gischt = d.whitewater.enable_whitewater_simulation
    pro = (doctor_ops.BYTES_PRO_VOXEL_WHITEWATER if mit_gischt
           else doctor_ops.BYTES_PRO_VOXEL)
    gb = voxel * pro / 1024 ** 3
    faktor = (ziel / jetzt) ** 3
    return voxel, gb, faktor


def _setzen(d, pfad, wert):
    teile = pfad.split(".")
    ziel = d
    for t in teile[:-1]:
        ziel = getattr(ziel, t, None)
        if ziel is None:
            return False
    if not hasattr(ziel, teile[-1]):
        return False
    try:
        setattr(ziel, teile[-1], wert)
        return True
    except (TypeError, ValueError):
        return False


class HYDROKIT_OT_apply_quality(bpy.types.Operator):
    bl_idname = "hydrokit.apply_quality"
    bl_label = "Apply Level"
    bl_description = "Sets resolution, subdivision, whitewater and CFL in one go"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain_props(context) is not None

    def execute(self, context):
        props = context.scene.hydrokit
        d = flip_bridge.get_domain_props(context)
        titel, _, werte = STUFEN[props.qualitaet]

        gesetzt = sum(1 for pfad, wert in werte if _setzen(d, pfad, wert))
        schaetzung = kosten_schaetzen(context)
        doctor_ops.auffrischen(context)       # memory warning up to date right away

        text = f"{titel}: {gesetzt} settings"
        if schaetzung:
            text += f", estimated {schaetzung[1]:.1f} GB"
        self.report({'INFO'}, text + " — needs a new bake")
        return {'FINISHED'}


class HYDROKIT_OT_clean_cache(bpy.types.Operator):
    bl_idname = "hydrokit.clean_cache"
    bl_label = "Delete Empty Cache Files"
    bl_description = (
        "A cancelled bake leaves 0-byte files behind. "
        "FLIP takes them as finished and skips those frames"
    )
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_cache_directory(context) is not None

    def execute(self, context):
        import os
        pfad = flip_bridge.get_cache_directory(context)
        bakefiles = os.path.join(pfad, "bakefiles")
        if not os.path.isdir(bakefiles):
            self.report({'INFO'}, "No cache present")
            return {'FINISHED'}
        weg = 0
        for name in os.listdir(bakefiles):
            voll = os.path.join(bakefiles, name)
            try:
                if os.path.getsize(voll) == 0:
                    os.remove(voll)
                    weg += 1
            except OSError:
                pass
        doctor_ops.auffrischen(context)
        self.report({'INFO'}, f"{weg} empty files deleted"
                    if weg else "No empty files found")
        return {'FINISHED'}


classes = (HYDROKIT_OT_apply_quality, HYDROKIT_OT_clean_cache)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
