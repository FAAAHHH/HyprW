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


def _wasser_variante(self, context):
    """A variant fills the two measured sliders. The water itself changes
    only with Apply Water (or Apply Look), never foam, bubbles, spray, mist."""
    from ..operators.materials_ops import WASSER_VARIANTEN
    _, sicht, geloest = WASSER_VARIANTEN[self.wasser_variante]
    self.wasser_sichtweite = sicht
    self.wasser_geloest = geloest


def _ergebnisse_waehlbar(self, context):
    from ..sim import anzeige
    anzeige.alle_waehlbar_machen(context.scene)


def _groesse_neu(self, context):
    """Overall Size resizes HyprW's whitewater at once (no new bake); the
    water body stays as it is."""
    from ..sim import aussehen
    try:
        aussehen.anwenden(context, wasser=False)
    except Exception:
        pass


def _gitter_neu_zeichnen(self, context):
    """The grid is drawn by a viewport handler (ui/gitter.py). An update
    callback must return None — a lambda returning the list of tag_redraw()
    results made Blender print a ValueError on every click."""
    for w in context.window_manager.windows:
        for a in w.screen.areas:
            if a.type == 'VIEW_3D':
                a.tag_redraw()


def _andere_pausieren_neu(self, context):
    """Switched while the timeline plays: pauses or sets back at once."""
    from ..sim import szene_pause
    try:
        szene_pause.einstellung_geaendert(self.id_data)
    except Exception:
        pass


def _nass_neu(props, context):
    """Wet Obstacles switched: compute and insert, or take out."""
    from ..sim import nass
    try:
        nass.schalten(context.scene, props.nass_an)
    except Exception as e:      # a material the group cannot handle
        print("HyprW Wet Obstacles:", e)


class HydroKitSceneProperties(bpy.types.PropertyGroup):

    whitewater_scale_multiplier: bpy.props.FloatProperty(
        name="Overall Size",
        description=(
            "Common factor for all whitewater particles. "
            "1.0 equals the size computed from the voxel size"
        ),
        default=1.0, min=0.05, max=10.0, soft_max=3.0,
        update=_groesse_neu,
    )

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
    )
    wasser_geloest: bpy.props.FloatProperty(
        name="Dissolved Matter",
        description=("Absorption of dissolved organic matter at 440 nm in 1/m: a yellow-brown to "
                     "green tint in deep water. Mountain waters 0.063 to 0.35 (EGU 2020); "
                     "bog and forest streams much more"),
        default=0.15, min=0.0, max=10.0, soft_max=2.0, precision=3,
    )

    show_material: bpy.props.BoolProperty(name="Material", default=True)

    wind_faktor: bpy.props.FloatProperty(
        name="Wind Strength",
        description=("Conversion of Blender's wind strength to m/s. "
                     "1.0 means: strength 5 equals 5 m/s of wind"),
        default=1.0, min=0.0, max=20.0, soft_max=10.0,
    )

    wasser_kraeuselung: bpy.props.FloatProperty(
        name="Ripples",
        description=("Fine roughness of the water surface. At 0 it is a "
                     "perfect mirror and looks like liquid metal"),
        default=0.035, min=0.0, max=0.5, soft_max=0.12,
    )

    wasser_rauheit_wind: bpy.props.BoolProperty(
        name="Ripples From Wind",
        description=("Roughen the surface by the wind of the scene's wind force fields, "
                     "as measured on the sea by Cox & Munk 1954: 2 m/s gives 0.32, 5 m/s "
                     "0.40, 10 m/s 0.48. Ripples stays the value for calm water"),
        default=True,
    )
    wasser_kaustik_echt: bpy.props.BoolProperty(
        name="Traced Caustics",
        description=("Let Cycles trace the light the water surface refracts onto the ground "
                     "(Shadow Caustics) instead of the caustic pattern: the network comes "
                     "from the simulated waves. Sharper network, but measured 31 % darker on "
                     "average and slower to render; marks the water, the other meshes and "
                     "the lights for it"),
        default=False,
    )

    luft_an: bpy.props.BoolProperty(
        name="Aerated Falling Water",
        description=("Falling water takes up air and turns white the further it falls, "
                     "fully white at its breakup length (Ervine et al. 1997, Castillo; "
                     "measured air in falling jets: Carrillo et al. 2020). The bake writes "
                     "the surface's velocities for it, which also give motion blur"),
        default=True,
    )
    luft_aufreisslaenge: bpy.props.FloatProperty(
        name="Breakup Length",
        description=("Fall height after which the falling water is fully aerated and white. "
                     "0 = computed from the largest inflow (its speed and thickness)"),
        default=0.0, min=0.0, soft_max=50.0, unit='LENGTH',
    )
    nass_an: bpy.props.BoolProperty(
        name="Wet Obstacles",
        description=("Obstacles turn darker with a water film where the water has touched "
                     "them, from that frame on (Lekner & Dorf 1988: an albedo of 0.3 becomes "
                     "0.18). Adds a node group in front of the Principled BSDF of the "
                     "obstacles' materials; off takes it out again"),
        default=False,
        update=lambda self, context: _nass_neu(self, context),
    )

    # --- Studio look (materials_ops.baue_wasser): the principle of studio
    # water materials with water's measured values
    wasser_schatten: bpy.props.FloatProperty(
        name="Light Through Surface",
        description=("Share of the sunlight that reaches the ground through the water. "
                     "Cycles can hardly find the light paths refracted by a wavy surface "
                     "(caustics), so the ground under water stays dark and noisy. At 1 "
                     "shadow rays pass the surface, less its Fresnel reflection; the water "
                     "body still absorbs and scatters them as measured. Render approximation"),
        default=1.0, min=0.0, max=1.0, subtype='FACTOR',
    )
    wasser_kaustik: bpy.props.FloatProperty(
        name="Caustics",
        description=("Bright network on the ground where waves focus the light. "
                     "APPROXIMATION: a pattern, not traced light; the mean brightness "
                     "stays the same"),
        default=0.5, min=0.0, max=1.0, subtype='FACTOR',
    )
    wasser_kaustik_groesse: bpy.props.FloatProperty(
        name="Caustic Size",
        description=("Cell size of the caustic network, set by the waves that focus the "
                     "light. APPROXIMATION: 15 cm suits short gravity waves"),
        default=0.15, min=0.01, max=2.0, unit='LENGTH',
    )
    wasser_wellen: bpy.props.FloatProperty(
        name="Capillary Ripples",
        description=("Height of the fine ripples below the simulation resolution, at the "
                     "1.71 cm wavelength where capillary and gravity waves meet (surface "
                     "tension 72.75 mN/m at 20 °C, Vargaftik et al. 1983). 1 = 0.5 mm "
                     "(APPROXIMATION)"),
        default=1.0, min=0.0, max=4.0,
    )
    wasser_dispersion: bpy.props.BoolProperty(
        name="Dispersion",
        description=("Splits light into colours like a prism, with water's refractive "
                     "index per colour (Daimon & Masumura 2007, 20 °C: red 1.3319, green "
                     "1.3347, blue 1.3387). Barely visible in water, renders slower"),
        default=False,
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
        description=("Foam, spray, mist and lens drops can be clicked in the viewport. "
                     "Off (default): clicks go through them to the objects inside the "
                     "domain — the mist volume covers the whole scene. The water can "
                     "always be clicked, for Apply Water"),
        default=False,
        update=_ergebnisse_waehlbar,
    )
    # Read by sim/abspielen.py on every frame while the timeline plays and by
    # sim/abspielen_aussehen.py (the light mist); no update callback: paused
    # frames always show the full result anyway
    abspielen_leicht: bpy.props.BoolProperty(
        name="Light Playback",
        description=("While the timeline plays, the viewport shows lighter versions of the "
                     "baked results: fewer foam, bubble and spray points, a lighter water "
                     "mesh and a coarser mist. Paused frames and every render show the full "
                     "result"),
        default=True,
    )
    gitter_anzeigen: bpy.props.BoolProperty(
        name="Show Grid",
        description=("Shows the simulation cells on the domains in the viewport: "
                     "that is how finely the water (and the mist) is resolved. Does "
                     "not appear in the render"),
        default=False,
        update=_gitter_neu_zeichnen,
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
        update=_gitter_neu_zeichnen,
    )

    # Playback: heavy animation that is not HyprW's holds still while the
    # timeline plays (sim/szene_pause.py)
    abspielen_andere_pausieren: bpy.props.BoolProperty(
        name="Pause Other Animation",
        description=("While the timeline plays, heavy animation that is not HyprW's (trees "
                     "swaying in the wind, for example) holds still in the pose of the frame "
                     "where playback started, so the water plays smoothly. Stopped, in every "
                     "render and in every saved file everything moves as before"),
        default=False,
        update=_andere_pausieren_neu,
    )
    # What Pause Other Animation holds still right now (JSON), so that an
    # undo step, an autosave or a crash file can be set back; unset otherwise
    abspielen_pause_stand: bpy.props.StringProperty(options={'HIDDEN'})

    # The running bake is module state (operators/sim_ops.py, BAKE): an undo
    # step brought back an older value of a scene property
    linse_rechnet: bpy.props.BoolProperty(default=False)
    linse_fortschritt: bpy.props.FloatProperty(
        name="Progress", default=0.0, min=0.0, max=1.0, subtype='FACTOR')

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
