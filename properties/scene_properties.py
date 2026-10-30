# HyprW — scene settings
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy


def _domains_sichtbar(self, context):
    """Hides the domain objects themselves — switching off only the coloured
    outline did nothing, because Blender keeps drawing the box underneath as
    a wireframe."""
    for obj in context.scene.objects:
        rolle = getattr(getattr(obj, "hydrokit", None), "typ", 'NONE')
        if rolle in ('DOMAIN', 'MIST_DOMAIN', 'SPRAY_DOMAIN'):
            try:
                obj.hide_set(not self.domains_anzeigen)
            except RuntimeError:
                pass        # not in the current view layer
    for w in context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == 'VIEW_3D':
                a.tag_redraw()


def _wasser_neu(self, context):
    """Water sliders act at once — no new bake needed."""
    from ..sim import aussehen
    try:
        aussehen.anwenden(context)
    except Exception:
        pass


def _wasser_variante(self, context):
    """A variant fills the two measured sliders, which then apply it."""
    from ..operators.materials_ops import WASSER_VARIANTEN
    _, sicht, geloest = WASSER_VARIANTEN[self.wasser_variante]
    self["wasser_sichtweite"] = sicht        # without update: apply once below
    self["wasser_geloest"] = geloest
    _wasser_neu(self, context)


def _ergebnisse_waehlbar(self, context):
    from ..sim import anzeige
    anzeige.alle_waehlbar_machen(context.scene)


class HydroKitSceneProperties(bpy.types.PropertyGroup):

    whitewater_scale_multiplier: bpy.props.FloatProperty(
        name="Overall Size",
        description=(
            "Common factor for all whitewater particles. "
            "1.0 equals the size computed from the voxel size"
        ),
        default=1.0, min=0.05, max=10.0, soft_max=3.0,
    )

    # Relative size per particle type. Foam clumps at the surface and looks
    # larger, spray are single flying drops, dust is fine mist.
    scale_foam: bpy.props.FloatProperty(
        name="Foam", description="Size relative to the voxel size",
        default=0.24, min=0.0, max=3.0)
    scale_bubble: bpy.props.FloatProperty(
        name="Bubbles", description="Size relative to the voxel size",
        default=0.20, min=0.0, max=3.0)
    scale_spray: bpy.props.FloatProperty(
        name="Spray", description="Size relative to the voxel size",
        default=0.14, min=0.0, max=3.0)
    scale_dust: bpy.props.FloatProperty(
        name="Dust", description="Size relative to the voxel size",
        default=0.10, min=0.0, max=3.0)


    wasser_variante: bpy.props.EnumProperty(
        name="Water",
        description="Which water variant is created when applying",
        items=[
            ('KLAR',        "Clear",        "Visibility 10.75 m, the clearest of 96 rivers measured"),
            ('SAUBER_BLAU', "Clean Blue",   "Visibility 6 m, no dissolved matter: the blue of water itself"),
            ('GEBIRGSBACH', "Mountain Stream", "Visibility 3 m, a little dissolved matter"),
            ('ALGEN',       "Algae",        "Visibility 1.6 m, much dissolved matter (algae pigments not modelled)"),
            ('OZEAN',       "Ocean",        "Visibility 20 m (outside the river data, approximation)"),
            ('TRUEBE',      "Murky",        "Visibility 0.25 m, the murkiest of 96 rivers measured"),
        ],
        default='GEBIRGSBACH',
        update=_wasser_variante,
    )

    wasser_sichtweite: bpy.props.FloatProperty(
        name="Visibility", subtype='DISTANCE', unit='LENGTH',
        description=("How far a black object can be seen under water. Sets how murky the water "
                     "is: attenuation = 4.8 / visibility (Davies-Colley 1988; Zaneveld & Pegau "
                     "2003, within 10 %). Rivers at low flow: 0.25 to 10.75 m "
                     "(Davies-Colley & Close 1990)"),
        default=3.0, min=0.05, max=80.0, soft_min=0.2, soft_max=20.0,
        update=_wasser_neu,
    )
    wasser_geloest: bpy.props.FloatProperty(
        name="Dissolved Matter",
        description=("Absorption of dissolved organic matter at 440 nm in 1/m: a yellow-brown to "
                     "green tint in deep water. Mountain waters 0.063 to 0.35 (EGU 2020); "
                     "bog and forest streams much more"),
        default=0.15, min=0.0, max=10.0, soft_max=2.0, precision=3,
        update=_wasser_neu,
    )

    show_material: bpy.props.BoolProperty(name="Material", default=True)

    wind_faktor: bpy.props.FloatProperty(
        name="Wind Strength",
        description=("Conversion of Blender's wind strength to m/s. "
                     "1.0 means: strength 5 equals 5 m/s of wind"),
        default=1.0, min=0.0, max=20.0, soft_max=10.0,
    )

    beide_ufer: bpy.props.BoolProperty(
        name="Open Both Sides",
        description=("Also open the side against the flow direction. "
                     "Useful for a stream flowing through, not for a pool"),
        default=False,
    )

    nebel_dichte: bpy.props.FloatProperty(
        name="Mist Density",
        description="How dense the mist gets. Too high looks like cotton wool",
        default=2.0, min=0.0, max=20.0, soft_max=6.0,
    )
    nebel_radius: bpy.props.FloatProperty(
        name="Radius per Particle",
        description=("How far a single spray particle spreads into mist. "
                     "Larger joins the particles into a cloud"),
        default=0.08, min=0.001, max=2.0, soft_max=0.5, unit='LENGTH',
    )
    nebel_hoehe_an: bpy.props.BoolProperty(
        name="Only above a Height",
        description=("Only create mist above this height — useful at a waterfall "
                     "so none lies over calm water"),
        default=False,
    )
    nebel_hoehe: bpy.props.FloatProperty(
        name="From Height", default=0.0, unit='LENGTH',
    )

    tropfen_radius: bpy.props.FloatProperty(
        name="Splash Radius",
        description=("How close a spray particle must get to the camera to "
                     "count as a hit"),
        default=1.5, min=0.05, max=50.0, soft_max=5.0, unit='LENGTH',
    )
    tropfen_staerke: bpy.props.FloatProperty(
        name="Strength",
        description="How wet the lens gets in the densest spray",
        default=0.8, min=0.0, max=1.0,
    )
    tropfen_traegheit: bpy.props.FloatProperty(
        name="Drying",
        description=("How slowly the drops disappear. 0.9 means: per "
                     "frame 90 percent remain. Higher = slower"),
        default=0.92, min=0.0, max=0.999,
    )
    tropfen_abstand: bpy.props.FloatProperty(
        name="Distance to Lens",
        description="How far in front of the camera the pane sits",
        default=0.12, min=0.01, max=2.0, unit='LENGTH',
    )

    preset: bpy.props.EnumProperty(
        name="Water Type",
        description="Bundled settings for one kind of water body",
        items=[
            ('GEBIRGSBACH', "Mountain Stream", "Shallow, fast, lots of whitewater"),
            ('WASSERFALL',  "Waterfall",   "Drop from height, whitewater and mist"),
            ('OZEANWELLE',  "Ocean Wave",  "Large sluggish mass, foam"),
            ('GLAS',        "Tipping a Glass", "Small scale"),
        ],
        default='GEBIRGSBACH',
    )

    qualitaet: bpy.props.EnumProperty(
        name="Level",
        description="Compute cost against quality",
        items=[
            ('VORSCHAU', "Preview",     "Fast, only for checking the motion"),
            ('ARBEIT',   "Working",     "Balance for tuning"),
            ('FINAL',    "Final",       "Full quality"),
        ],
        default='ARBEIT',
    )

    wasser_kraeuselung: bpy.props.FloatProperty(
        name="Ripples",
        description=("Fine roughness of the water surface. At 0 it is a "
                     "perfect mirror and looks like liquid metal"),
        default=0.035, min=0.0, max=0.5, soft_max=0.12,
        update=_wasser_neu,
    )

    # No longer used (the water is measured now, see materials_ops); kept so
    # that older files still load their value without errors
    wasser_tiefenwirkung: bpy.props.FloatProperty(
        name="Depth Effect", default=1.0, min=0.0, max=20.0,
        options={'HIDDEN'},
    )

    domains_anzeigen: bpy.props.BoolProperty(
        name="Domains",
        description=("Show and hide the domain boxes, coloured: domain yellow, "
                     "spray domain blue, mist domain white. Changes nothing "
                     "in the simulation"),
        default=True,
        update=_domains_sichtbar,
    )
    ergebnisse_waehlbar: bpy.props.BoolProperty(
        name="Selectable Results",
        description=("Water, foam, spray, mist and lens drops can be clicked in the "
                     "viewport. Off (default): clicks go through them to the objects "
                     "inside the domain — the mist volume covers the whole scene"),
        default=False,
        update=_ergebnisse_waehlbar,
    )
    gitter_anzeigen: bpy.props.BoolProperty(
        name="Show Grid",
        description=("Shows the simulation cells on the domains in the viewport: "
                     "that is how finely the water (and the mist) is resolved. Does "
                     "not appear in the render"),
        default=False,
        update=lambda self, context: [a.tag_redraw() for w in context.window_manager.windows
                                      for a in w.screen.areas if a.type == 'VIEW_3D'],
    )

    gitter_seiten: bpy.props.EnumProperty(
        name="Grid on",
        description="On which domain walls the cell grid is drawn",
        items=[
            ('VORNE',  "Visible Walls", "The walls facing the viewer"),
            ('HINTEN', "Back Walls",    "The walls behind — you look into the box"),
            ('ALLE',   "All Walls",     "All six sides"),
        ],
        default='VORNE',
        update=lambda self, context: [a.tag_redraw() for w in context.window_manager.windows
                                      for a in w.screen.areas if a.type == 'VIEW_3D'],
    )

    backt: bpy.props.BoolProperty(default=False)
    linse_rechnet: bpy.props.BoolProperty(default=False)
    linse_fortschritt: bpy.props.FloatProperty(
        name="Progress", default=0.0, min=0.0, max=1.0, subtype='FACTOR')
    bake_fortschritt: bpy.props.FloatProperty(
        name="Progress", default=0.0, min=0.0, max=1.0, subtype='FACTOR')

    doctor_lief: bpy.props.BoolProperty(default=False)

    show_whitewater: bpy.props.BoolProperty(name="Whitewater", default=True)
    show_info: bpy.props.BoolProperty(name="Scene", default=True)


classes = (HydroKitSceneProperties,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.hydrokit = bpy.props.PointerProperty(type=HydroKitSceneProperties)


def unregister():
    if hasattr(bpy.types.Scene, "hydrokit"):
        del bpy.types.Scene.hydrokit
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
