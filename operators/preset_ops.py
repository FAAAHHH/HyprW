# HyprW — presets
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The settings that make water look realistic are unremarkable one by one
# and decisive together. In FLIP Fluids they are spread over five tabs.
# These presets set them as a bundle.
#
# Deliberately NOT using FLIP's own preset format: it writes into a library
# in the add-on folder and does not survive an update. Here values are set
# directly, traceable in the source.

import bpy
from ..utils import flip_bridge

# Each entry: (path from domain, value)
# The paths are the real FLIP Fluids properties.
PRESETS = {
    'GEBIRGSBACH': {
        "titel": "Mountain Stream",
        "info": "Shallow, fast water over stones. Lots of whitewater.",
        "werte": [
            ("world.enable_sheet_seeding", True),
            ("world.sheet_fill_rate", 0.5),
            ("world.enable_surface_tension", False),
            ("advanced.CFL_condition_number", 2),
            ("surface.subdivisions", 2),
            ("whitewater.enable_whitewater_simulation", True),
            ("whitewater.wavecrest_emission_rate", 400.0),
            ("whitewater.turbulence_emission_rate", 400.0),
            ("whitewater.spray_drag_coefficient", 0.4),
        ],
        "massstab": "2 to 4 m domain length",
    },
    'WASSERFALL': {
        "titel": "Waterfall",
        "info": "Drop from height, strong whitewater and mist.",
        "werte": [
            ("world.enable_sheet_seeding", True),
            ("world.sheet_fill_rate", 0.6),
            ("advanced.CFL_condition_number", 2),
            ("surface.subdivisions", 2),
            ("whitewater.enable_whitewater_simulation", True),
            ("whitewater.wavecrest_emission_rate", 500.0),
            ("whitewater.turbulence_emission_rate", 500.0),
            ("whitewater.spray_drag_coefficient", 0.35),
            ("whitewater.enable_dust", True),
        ],
        "massstab": "3 to 8 m fall height",
    },
    'OZEANWELLE': {
        "titel": "Ocean Wave",
        "info": "Large, sluggish mass. Foam instead of spray.",
        "werte": [
            ("world.enable_sheet_seeding", True),
            ("world.sheet_fill_rate", 0.35),
            ("advanced.CFL_condition_number", 3),
            ("surface.subdivisions", 2),
            ("whitewater.enable_whitewater_simulation", True),
            ("whitewater.wavecrest_emission_rate", 250.0),
            ("whitewater.turbulence_emission_rate", 150.0),
            ("whitewater.foam_advection_strength", 1.0),
        ],
        "massstab": "from 20 m, otherwise it looks like a bathtub",
    },
    'GLAS': {
        "titel": "Tipping a Glass",
        "info": "Small scale. Surface tension becomes important.",
        "werte": [
            ("world.enable_sheet_seeding", True),
            ("world.sheet_fill_rate", 0.7),
            ("world.enable_surface_tension", True),
            ("advanced.CFL_condition_number", 2),
            ("surface.subdivisions", 2),
            ("whitewater.enable_whitewater_simulation", False),
        ],
        "massstab": "below 0.5 m",
    },
}


def _setzen(domain_props, pfad, wert):
    """Writes domain.<pfad> = wert without breaking on missing properties."""
    teile = pfad.split(".")
    ziel = domain_props
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


class HYDROKIT_OT_apply_preset(bpy.types.Operator):
    bl_idname = "hydrokit.apply_preset"
    bl_label = "Apply Preset"
    bl_description = "Sets the bundled settings for this water type"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain_props(context) is not None

    def execute(self, context):
        props = context.scene.hydrokit
        d = flip_bridge.get_domain_props(context)
        preset = PRESETS.get(props.preset)
        if preset is None:
            return {'CANCELLED'}

        gesetzt, fehlend = 0, []
        for pfad, wert in preset["werte"]:
            if _setzen(d, pfad, wert):
                gesetzt += 1
            else:
                fehlend.append(pfad)

        # the matching water colour as well
        farbe = {'GEBIRGSBACH': 'GEBIRGSBACH', 'WASSERFALL': 'GEBIRGSBACH',
                 'OZEANWELLE': 'OZEAN', 'GLAS': 'KLAR'}.get(props.preset)
        if farbe:
            props.wasser_variante = farbe

        if fehlend:
            print("[HyprW] Preset: not found:", ", ".join(fehlend))
        self.report({'INFO'},
                    f"{preset['titel']}: {gesetzt} settings applied"
                    + (f", {len(fehlend)} unknown" if fehlend else "")
                    + " — needs a new bake")
        return {'FINISHED'}


classes = (HYDROKIT_OT_apply_preset,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
