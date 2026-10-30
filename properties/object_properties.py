# HyprW — object properties
# SPDX-License-Identifier: GPL-3.0-or-later
#
# HyprW's own tagging of simulation objects, independent of FLIP Fluids.
# Every Blender object can be given a role in the simulation.

import bpy


def _anzeige_neu(context):
    from ..sim import anzeige
    try:
        anzeige.frame_laden(context.scene)
    except Exception:
        pass


def _linse_an_neu(self, context):
    """Switching on: show existing lens drops if they belong to the current
    bake, otherwise compute them — in the background, step by step. Doing it
    right here froze Blender for a minute on a 300-frame scene."""
    from ..sim import linse
    try:
        cam = context.scene.camera
        if self.linse_an and cam is not None and cam.type == 'CAMERA':
            linse.tiefenschaerfe_einrichten(cam, self.id_data)
            if not linse.ist_aktuell(context.scene):
                from ..operators.sim_ops import linse_im_hintergrund_starten
                linse_im_hintergrund_starten()
    except Exception:
        pass
    _anzeige_neu(context)


def _aussehen_neu(self, context):
    """Make appearance sliders visible right away — without a new bake."""
    from ..sim import aussehen
    try:
        aussehen.anwenden(context)
    except Exception:
        pass


class HydroKitObjectProperties(bpy.types.PropertyGroup):

    typ: bpy.props.EnumProperty(
        name="Role",
        description="Which role this object plays in the simulation",
        items=[
            ('NONE',     "None",       "Not part of the simulation"),
            ('DOMAIN',   "Domain",     "The region that is simulated"),
            ('FLUID',    "Water",      "Starts as water"),
            ('OBSTACLE', "Obstacle",   "Water flows around it"),
            ('INFLOW',   "Inflow",     "Keeps emitting water"),
            ('OUTFLOW',  "Outflow",    "Removes water"),
            ('MIST_DOMAIN', "Mist Domain",
             "Only mist keeps flying beyond the domain here — without a "
             "flow grid, so without the cost of simulating water"),
            ('SPRAY_DOMAIN', "Spray Domain",
             "Spray keeps flying beyond the domain here until it lands or "
             "reaches the boundary — without the cost of simulating water"),
        ],
        default='NONE',
    )

    # --- Domain ---------------------------------------------------------
    aufloesung: bpy.props.IntProperty(
        name="Resolution",
        description=("Voxels along the LONGEST side. Memory and compute time grow "
                     "with its cube: twice as high = eight times as expensive"),
        default=65, min=10, max=1024, soft_max=300,
    )
    cache_pfad: bpy.props.StringProperty(
        name="Cache",
        description="Where the baked frames are written",
        default="//hydrokit_cache", subtype='DIR_PATH',
        # Without this option Blender 5 paints every // path red ("invalid"),
        # even though HyprW resolves relative paths.
        options={'PATH_SUPPORTS_BLEND_RELATIVE'},
    )
    # --- Compute device (GPU support of HyprW's engine, engine_patch/
    # README.md section 8). Only steps with a GPU version can run there; the
    # GPU results are bit-identical to the CPU ones.
    rechengeraet: bpy.props.EnumProperty(
        name="Compute Device",
        description="Where the simulation is computed",
        items=[
            ('CPU', "CPU", "Everything on the processor"),
            ('GPU', "GPU", "Every step that has a GPU version on the NVIDIA graphics card, "
                           "the rest on the processor"),
            ('BEIDES', "Both", "Choose for each step where it is computed"),
        ],
        default='GPU',
    )
    rechengeraet_messung: bpy.props.StringProperty(
        name="Measured Devices",
        description=("Result of the last 'Pick the Fastest per Step': device name and the "
                     "GPU and CPU time of every step (JSON)"),
        default="",
    )
    geraet_mesh: bpy.props.EnumProperty(
        name="Surface Mesh",
        description=("Where the distance field of the water surface mesh is computed "
                     "(three quarters of the mesh time). Both give exactly the same mesh"),
        items=[('CPU', "CPU", "On the processor"), ('GPU', "GPU", "On the graphics card")],
        default='GPU',
    )
    geraet_gischt: bpy.props.EnumProperty(
        name="Whitewater",
        description=("Where the whitewater step is computed. On the GPU so far: the surface "
                     "curvature that decides where whitewater forms (over half of the "
                     "whitewater time). Both give exactly the same result"),
        items=[('CPU', "CPU", "On the processor"), ('GPU', "GPU", "On the graphics card")],
        default='GPU',
    )
    geraet_partikel: bpy.props.EnumProperty(
        name="Water Particles",
        description=("Where the water particle step is computed. On the GPU so far: the "
                     "liquid level set from the particles (about 70 % of the step). Both "
                     "give exactly the same result"),
        items=[('CPU', "CPU", "On the processor"), ('GPU', "GPU", "On the graphics card")],
        default='GPU',
    )
    geraet_advektion: bpy.props.EnumProperty(
        name="Advection",
        description=("Where the particle velocities are transferred to the grid. The GPU adds "
                     "the same contributions in another order: equal up to float rounding "
                     "(measured: at most 0.000002 m/s apart)"),
        items=[('CPU', "CPU", "On the processor"), ('GPU', "GPU", "On the graphics card")],
        default='GPU',
    )
    geraet_druck: bpy.props.EnumProperty(
        name="Pressure",
        description=("Where the pressure is solved. Same method and preconditioner on the GPU; "
                     "meets the same convergence test and agreed with the CPU in every test"),
        items=[('CPU', "CPU", "On the processor"), ('GPU', "GPU", "On the graphics card")],
        default='GPU',
    )
    cfl: bpy.props.IntProperty(
        name="CFL",
        description=("How many voxels a particle may jump per simulation step. "
                     "Smaller is more accurate and slower. 2 for fast water"),
        default=3, min=1, max=10,
    )
    vorschau_aufloesung: bpy.props.IntProperty(
        name="Preview",
        description=("Resolution of the coarse preview mesh for the viewport. "
                     "The full mesh is always rendered. 0 = no preview"),
        default=60, min=0, max=400, soft_max=150,
    )
    vorschau_im_viewport: bpy.props.BoolProperty(
        name="Preview in Viewport",
        description=("Show the coarse mesh in the viewport; the full one is "
                     "rendered. Only needed for very fine bakes (subdivision 2+): "
                     "thin jets break up into single blobs in it"),
        default=False,
    )
    oberflaechen_unterteilung: bpy.props.IntProperty(
        name="Subdivision",
        description="Detail of the generated surface mesh",
        default=1, min=0, max=3,
    )
    gischt: bpy.props.BoolProperty(
        name="Whitewater",
        description="Simulate foam, bubbles, spray and dust",
        default=False,
    )
    sheeting: bpy.props.BoolProperty(
        name="Sheeting",
        description=("Keeps thin water sheets together by filling them with new "
                     "particles. WARNING: this adds water. In tall waterfalls the "
                     "whole jet is a thin sheet — measured, an 8 m pool filled "
                     "halfway in 3 seconds with it"),
        default=False,
    )
    offene_raender: bpy.props.BoolVectorProperty(
        name="Open Borders",
        description="Sides where water drains instead of piling up",
        size=6, default=(False,)*6,
    )

    # --- Particle types: size sets the physics --------------------------
    # One slider instead of two: fall speed AND wind response both follow
    # from the droplet size, so they can never contradict each other.
    # See sim/partikel.py for the measured values behind this.
    # Size parameter d50 of the gamma distribution (shape 2, scale 0.5 d50)
    # after the spray model of Lian et al. 2019 (Nazixia, IJERPH 16(3):316).
    # Every particle gets its own diameter from it.
    nebel_groesse: bpy.props.FloatProperty(
        name="Mist d50",
        description=("Size parameter of mist droplets in mm (most common size "
                     "= half the value). 0.26 mm: fine fraction from impacting "
                     "jets, measured 40 to 400 micrometres, most common "
                     "100 to 156. Smaller = floats longer"),
        default=0.26, min=0.01, max=1.0, soft_max=0.6, precision=3,
    )
    spray_groesse: bpy.props.FloatProperty(
        name="Spray d50",
        description=("Size parameter of spray droplets in mm. 3 mm after the "
                     "dam atomisation spray model (Lian et al. 2019); "
                     "droplets between 0.125 and 8.5 mm (their measured range)"),
        default=3.0, min=0.1, max=8.0, soft_max=5.0, precision=2,
    )
    foam_groesse: bpy.props.FloatProperty(
        name="Foam",
        description="Bubble diameter in mm. Floats on the surface",
        default=2.00, min=0.005, max=5.0, soft_max=3.0, precision=3,
    )
    bubble_groesse: bpy.props.FloatProperty(
        name="Bubbles",
        description="Bubble diameter in mm. Inside the water — not affected by wind",
        default=1.50, min=0.005, max=5.0, soft_max=3.0, precision=3,
    )
    nebel_an: bpy.props.BoolProperty(
        name="Simulate Mist",
        description=("Very light particle type. Forms where water hits an "
                     "obstacle and stays in the air for a long time"),
        default=True,
    )
    # --- Lens drops (sim/linse.py) --------------------------------------
    linse_an: bpy.props.BoolProperty(
        name="Lens Drops",
        description=("Whatever flies onto the camera stays on the glass as drops: "
                     "small ones stick, large ones slowly run down"),
        default=False, update=_linse_an_neu,
    )
    linse_glas: bpy.props.EnumProperty(
        name="Glass",
        description="Surface of the front lens — sets how strongly drops stick",
        items=[
            ('GLAS', "Uncoated",
             "Water on glass, contact angles 60/44 degrees (Quere 1998). "
             "Drops up to ~15 µl stick to vertical glass"),
            ('FLUOR', "Fluorine-coated",
             "Water-repellent lens coating like on modern lenses and filters: contact "
             "angle 115 degrees, sliding angle 16 degrees (Daikin OPTOOL datasheet), "
             "hysteresis 11 degrees (low end of 11-15, patent). Drops up to ~4 µl stick "
             "to vertical glass. Hysteresis value is an APPROXIMATION within that range"),
            ('EIGEN', "Custom",
             "Set advancing and receding angle yourself"),
        ],
        # Modern lenses and protective filters almost always carry a
        # water-repellent fluorine top coat, so that is the default
        default='FLUOR',
    )
    linse_winkel_vor: bpy.props.FloatProperty(
        name="Advancing Angle",
        description=("Contact angle at which the drop edge advances (degrees). "
                     "Only with Glass = Custom. Water on float glass: 60 (Quere 1998)"),
        default=60.0, min=1.0, max=179.0)
    linse_winkel_rueck: bpy.props.FloatProperty(
        name="Receding Angle",
        description=("Contact angle at which the edge recedes (degrees), always "
                     "smaller than the advancing angle. Only with Custom. "
                     "Water on float glass: 44 (Quere 1998)"),
        default=44.0, min=0.5, max=178.0)

    # --- Lens drops: impact ---------------------------------------------
    linse_spritzen: bpy.props.BoolProperty(
        name="Splashing", default=True,
        description="Fast, large spray drops splash on impact (Mundo et al. 1995)")
    linse_spritz_schwelle: bpy.props.FloatProperty(
        name="Splash Threshold K",
        description=("A drop splashes from K = We^0.5 Re^0.25 upwards. Measured: 57.7 "
                     "(Mundo et al. 1995). Higher = fewer splashes"),
        default=57.7, min=1.0, max=1000.0)
    linse_ausbreiten: bpy.props.BoolProperty(
        name="Spread on Impact", default=True,
        description=("An impact briefly spreads out to D_max (Laan et al. 2014) and "
                     "merges with everything it reaches"))
    linse_ausbreit_faktor: bpy.props.FloatProperty(
        name="Spreading",
        description=("Factor on D_max from Laan et al. 2014 (measured: 1). Larger = "
                     "impacts merge more neighbours"),
        default=1.0, min=0.0, max=5.0)
    linse_wasser_groesse: bpy.props.FloatProperty(
        name="Water Drops (mm)",
        description=("Diameter of the drop the water surface leaves per glass cell; "
                     "also the cell size. APPROXIMATION (modelling choice): 2 mm"),
        default=2.0, min=0.1, max=10.0)

    # --- Lens drops: sliding --------------------------------------------
    linse_rutschen: bpy.props.BoolProperty(
        name="Sliding", default=True,
        description="Drops above the sticking limit run down (Quere 1998)")
    linse_haft_faktor: bpy.props.FloatProperty(
        name="Adhesion",
        description=("Factor on the holding force of the contact line (Quere 1998, "
                     "measured: 1). Smaller = drops start sliding at a smaller size"),
        default=1.0, min=0.05, max=10.0)
    linse_tempo_faktor: bpy.props.FloatProperty(
        name="Sliding Speed",
        description=("Factor on the speed after Le Grand et al. 2005 (1 = their value for "
                     "silicone oil; not measured for water)"),
        default=1.0, min=0.0, max=10.0)

    # --- Lens drops: trails ---------------------------------------------
    linse_spuren: bpy.props.BoolProperty(
        name="Trails", default=True,
        description="Fast drops leave droplets behind (Le Grand et al. 2005)")
    linse_perlen_schwelle: bpy.props.FloatProperty(
        name="Trail from Ca (×10⁻³)",
        description=("Capillary number Ca = eta U / gamma above which droplets are left "
                     "behind. Measured on silicone oil: 7.2 (Le Grand et al. 2005). For "
                     "water 7.2 corresponds to about 0.5 m/s"),
        default=7.2, min=0.1, max=100.0)
    linse_spur_streuung: bpy.props.FloatProperty(
        name="Size Spread",
        description="How much the trail droplets vary in size (+-). APPROXIMATION",
        default=60.0, min=0.0, max=95.0, subtype='PERCENTAGE')
    linse_spur_abstand: bpy.props.FloatProperty(
        name="Spacing",
        description="Mean spacing of the trail droplets in droplet diameters. APPROXIMATION",
        default=2.0, min=0.5, max=20.0)
    linse_spur_abstand_streuung: bpy.props.FloatProperty(
        name="Spacing Spread",
        description="How much the spacing varies (+-). APPROXIMATION",
        default=80.0, min=0.0, max=95.0, subtype='PERCENTAGE')
    linse_spur_satelliten: bpy.props.FloatProperty(
        name="Satellites",
        description="Share of trail droplets with a small neighbour (as from a dripping tap). APPROXIMATION",
        default=50.0, min=0.0, max=100.0, subtype='PERCENTAGE')
    linse_spur_satellit_groesse: bpy.props.FloatProperty(
        name="Satellite Size",
        description="Diameter of a satellite in percent of its droplet (+-1/3). APPROXIMATION",
        default=37.5, min=5.0, max=90.0, subtype='PERCENTAGE')
    linse_spur_streckung_min: bpy.props.FloatProperty(
        name="Elongation from",
        description="Smallest aspect ratio of the elongated trail droplets. APPROXIMATION",
        default=1.5, min=1.0, max=6.0)
    linse_spur_streckung_max: bpy.props.FloatProperty(
        name="Elongation to",
        description="Largest aspect ratio of the elongated trail droplets. APPROXIMATION",
        default=3.0, min=1.0, max=6.0)
    linse_spur_wackeln: bpy.props.FloatProperty(
        name="Sideways Wobble",
        description="Sideways offset of the trail in drop radii (+-half). APPROXIMATION",
        default=0.5, min=0.0, max=3.0)
    linse_spur_wischen: bpy.props.BoolProperty(
        name="Wipe Mist", default=True,
        description="Sliding drops pick up the mist along their whole path")

    # --- Lens drops: mist -----------------------------------------------
    linse_nebel_fang: bpy.props.BoolProperty(
        name="Collection Efficiency", default=True,
        description=("Some fine droplets fly around the camera (Langmuir & "
                     "Blodgett). Off = everything that arrives stays"))
    linse_nebel_verschmelzen: bpy.props.BoolProperty(
        name="Merge Droplets", default=True,
        description=("Touching mist droplets grow into larger drops "
                     "that eventually run down"))
    linse_nebel_zu_tropfen: bpy.props.FloatProperty(
        name="Becomes Drop at Width (mm)",
        description=("From this width a grown droplet becomes a normal drop: "
                     "drawn in detail, can slide. Purely a drawing choice, not physics — "
                     "small drops do not slide anyway"),
        default=1.0, min=0.1, max=10.0)
    linse_nebel_menge: bpy.props.FloatProperty(
        name="Mist Amount",
        description=("Factor on the deposited mist (1 = derived from the calibrated "
                     "mist density)"),
        default=1.0, min=0.0, max=100.0)

    # --- Lens drops: limits (drawing, not physics) ----------------------
    linse_max_tropfen: bpy.props.IntProperty(
        name="Max Drops", default=6000, min=10, max=100000,
        description="More drops get expensive to draw; the smallest are dropped")
    linse_max_nebel: bpy.props.IntProperty(
        name="Max Mist Droplets", default=20000, min=0, max=500000,
        description="More fine droplets get expensive to draw; the oldest are dropped")
    linse_durchmesser: bpy.props.FloatProperty(
        name="Lens (mm)",
        description=("Diameter of the front lens in mm; it fills the image width. "
                     "72 mm is a common filter thread"),
        default=72.0, min=10.0, max=200.0,
    )
    linse_spritz_rest: bpy.props.FloatProperty(
        name="Remainder after Splash",
        description=("Fast, large spray drops splash on impact (Mundo et al. "
                     "1995). Share of their water that stays on the glass as a drop. "
                     "APPROXIMATION — no measured value was found. "
                     "Takes effect after \"Compute Lens Drops\""),
        default=50.0, min=0.0, max=100.0, subtype='PERCENTAGE',
    )
    linse_spur_groesse: bpy.props.FloatProperty(
        name="Trail Droplets",
        description=("Fast drops leave droplets behind (Le Grand et al. "
                     "2005). Their diameter in percent of the drop width; 0 = no trail. "
                     "APPROXIMATION — the size is not quantified. "
                     "Takes effect after \"Compute Lens Drops\""),
        default=10.0, min=0.0, max=40.0, subtype='PERCENTAGE',
    )
    linse_unregelmaessig: bpy.props.FloatProperty(
        name="Irregular Edge",
        description=("How far the drop edge deviates from a circle. Real drops are never "
                     "round: defects in the glass pin the edge (Cubaud & Fermigier "
                     "2001). APPROXIMATION — there are no measurements for lens glass"),
        default=0.15, min=0.0, max=0.6, subtype='FACTOR',
        update=lambda self, context: _anzeige_neu(context),
    )
    linse_fangradius: bpy.props.FloatProperty(
        name="Capture Range",
        description=("How close a simulation particle must get to the camera to count "
                     "as a hit. One particle stands for a cloud of many drops — "
                     "not a constant of nature, but a scale for it"),
        default=0.5, min=0.01, max=5.0, unit='LENGTH',
    )
    # Per type: whether hits stick and which share of them (in percent). Which
    # hits belong to the share is fixed by a seeded draw (sim/linse.py) —
    # same settings, same result. New names instead of the former
    # linse_*_an/_anteil (0..1), so old values are not read as percent.
    linse_wasser_haftet: bpy.props.BoolProperty(
        name="sticks", default=True,
        description="Whether water reaching the camera leaves drops on the glass")
    linse_spray_haftet: bpy.props.BoolProperty(
        name="sticks", default=True,
        description="Whether spray hitting the camera sticks to the glass")
    linse_nebel_haftet: bpy.props.BoolProperty(
        name="sticks", default=True,
        description="Whether mist hitting the camera settles on the glass")
    linse_wasser_haftanteil: bpy.props.FloatProperty(
        name="how much sticks", default=100.0, min=0.0, max=100.0, subtype='PERCENTAGE',
        description=("Share of water hits that stays on the glass. The rest counts as "
                     "bounced off or run off"))
    linse_spray_haftanteil: bpy.props.FloatProperty(
        name="how much sticks", default=100.0, min=0.0, max=100.0, subtype='PERCENTAGE',
        description=("Share of spray hits that stays on the glass. The rest counts as "
                     "bounced off"))
    linse_nebel_haftanteil: bpy.props.FloatProperty(
        name="how much sticks", default=100.0, min=0.0, max=100.0, subtype='PERCENTAGE',
        description=("Share of mist hits that stays on the glass. The rest counts as "
                     "blown past"))

    nebel_dichte_anzeige: bpy.props.FloatProperty(
        name="Mist Density",
        description=("How dense the mist is rendered. Calibrated: radius times "
                     "density should come to about 0.16. "
                     "Higher density makes it DARKER, not brighter"),
        default=2.0, min=0.0, max=20.0, soft_max=6.0,
        update=_aussehen_neu,
    )
    # Foam texture: bubbles in breaking waves have radii from about 0.1 mm
    # to a few millimetres (Deane & Stokes 2002, Nature 418:839), far below the drawn particle
    # size (voxel based, see sim/aussehen.py) — so the texture shows the
    # bubble structure at the drawn size, not the real bubble size.
    schaum_blasen: bpy.props.IntProperty(
        name="Foam Bubbles",
        description=("How many bubbles across one foam particle in the render: thin bright "
                     "walls with a highlight on every bubble. 0 = plain foam without texture. "
                     "A look setting — real foam bubbles are far smaller than the drawn "
                     "particles"),
        default=4, min=0, max=12,
        update=_aussehen_neu,
    )
    schaum_luftanteil: bpy.props.FloatProperty(
        name="Air in Foam", subtype='PERCENTAGE', min=1.0, max=60.0, default=20.0,
        description=("Air content of the foam (aerated water). More air scatters more: "
                     "denser, whiter; less is clearer. Breaking waves measured up to 24 % "
                     "(Lamarre & Melville 1992)"),
        update=_aussehen_neu,
    )
    # Colour of the spray. Physically water drops of spray size are
    # colourless: water absorbs red at 0.34 /m (650 nm, Pope & Fry 1997,
    # Appl. Opt. 36:8710), over 1 mm of drop that is 0.03 %. A blue tint
    # is a look choice — set it to white for the physical colour.
    spray_farbe: bpy.props.FloatVectorProperty(
        name="Spray Colour", subtype='COLOR', size=3, min=0.0, max=1.0,
        default=(0.78, 0.89, 1.0),
        description=("Tint of the spray drops (light passing through them). A look setting: "
                     "real drops this small are colourless — white is the physical colour"),
        update=_aussehen_neu,
    )
    # MIST MODEL after literature values (engine_patch/README.md):
    #  - In the air a drop only breaks up above Weber number 12 (Pilch &
    #    Erdman). For 0.8 mm spray that is ~30 m/s — an 8 m fall reaches
    #    12.5. So NO mist forms in the middle of the fall.
    #  - Mist forms on impact: splashing from K = We*Oh^-0.4 > 2100
    #    (Cossali 1997), the fine fraction grows with K (Okawa 2006).
    nebel_spritz_anteil: bpy.props.FloatProperty(
        name="Fine Fraction",
        description=("Largest share of splash water that atomises into mist on a hard "
                     "impact. It is only reached on very hard impacts; just above "
                     "the splash threshold it is almost nothing"),
        default=0.3, min=0.0, max=1.0, subtype='FACTOR',
    )
    nebel_spritz_schwelle: bpy.props.FloatProperty(
        name="Splash Threshold K",
        description=("From this K = We*Oh^-0.4 a drop splashes when it hits "
                     "water (Cossali 1997: 2100). For 0.8 mm drops that equals "
                     "an impact speed of 4.6 m/s"),
        default=2100.0, min=100.0, max=50000.0, soft_max=10000.0,
    )
    # The four rules below only apply to bakes WITHOUT atomisation in the
    # engine (old caches, engine without the HyprW patch).
    nebel_tempo: bpy.props.FloatProperty(
        name="Mist below",
        description=("Older bakes only: below this speed (m/s) a drop counts "
                     "as mist instead of spray"),
        default=0.6, min=0.0, max=20.0, soft_max=3.0,
    )
    nebel_ab_alter: bpy.props.FloatProperty(
        name="Mist from Age",
        description=("From which share of its used-up lifetime a drop counts "
                     "as mist. 0.5 means: after half of its time "
                     "in the air. This covers the long free fall"),
        default=0.8, min=0.0, max=1.0, subtype='FACTOR',
    )
    nebel_aufprall_tempo: bpy.props.FloatProperty(
        name="Impact from",
        description=("Only spray flying faster than this value (m/s) counts as "
                     "impact spray and can atomise into mist at once. "
                     "Slow spray at a calm edge cannot"),
        default=3.0, min=0.0, max=30.0, soft_max=10.0,
    )
    nebel_feinanteil: bpy.props.FloatProperty(
        name="Fine Share",
        description=("Share of the spray that already forms as a fine fraction on "
                     "impact. On hard impacts water atomises into many sizes — "
                     "the engine knows only one, this slider makes up for that"),
        default=0.05, min=0.0, max=1.0, subtype='FACTOR',
    )
    nebel_staerke: bpy.props.FloatProperty(
        name="Mist Amount",
        description=("How much mist the obstacles emit. The engine only "
                     "creates mist near them — for a waterfall that is "
                     "the impact zone"),
        default=1.0, min=0.0, max=10.0, soft_max=3.0,
    )
    nebel_rate: bpy.props.FloatProperty(
        name="Mist Rate",
        description="How often mist particles are emitted",
        default=175.0, min=0.0, max=2000.0, soft_max=1000.0,
    )
    # The range (maxDustEmitterToObstacleDistance) is a fixed constant in the
    # engine and cannot be set from outside — so there is deliberately no
    # slider for it here.

    # --- Where whitewater forms -----------------------------------------
    # The engine only emits whitewater where the water is fast enough,
    # curved enough or turbulent enough. If the thresholds are too high,
    # nothing forms — without any error message. If they are too LOW,
    # every calmly flowing stream foams instead.
    #
    # Measured on a 5 m waterfall with the earlier values (speed 0.3,
    # curvature 0.02, turbulence 5): 173,495 foam particles at frame 60,
    # whitewater everywhere instead of only at the impact zones. The defaults
    # here are raised accordingly — whitewater should form where it crashes.
    gischt_tempo_min: bpy.props.FloatProperty(
        name="from Speed", description="From this flow speed (m/s)",
        default=2.0, min=0.0, max=50.0, soft_max=5.0)
    gischt_tempo_max: bpy.props.FloatProperty(
        name="to Speed", description="No extra whitewater above this",
        default=8.0, min=0.0, max=100.0, soft_max=20.0)
    gischt_kruemmung_min: bpy.props.FloatProperty(
        name="from Curvature", description="How sharp a wave crest must be",
        default=0.25, min=0.0, max=5.0, soft_max=1.0, precision=3)
    gischt_kruemmung_max: bpy.props.FloatProperty(
        name="to Curvature", default=1.0, min=0.0, max=10.0, soft_max=2.0)
    gischt_turbulenz_min: bpy.props.FloatProperty(
        name="from Turbulence", default=60.0, min=0.0, max=1000.0, soft_max=200.0)
    gischt_turbulenz_max: bpy.props.FloatProperty(
        name="to Turbulence", default=200.0, min=0.0, max=2000.0, soft_max=500.0)
    gischt_rate_wellen: bpy.props.FloatProperty(
        name="Rate Wave Crests", default=175.0, min=0.0, max=2000.0, soft_max=1000.0)
    gischt_rate_turbulenz: bpy.props.FloatProperty(
        name="Rate Turbulence", default=175.0, min=0.0, max=2000.0, soft_max=1000.0)
    gischt_hoechstzahl: bpy.props.IntProperty(
        name="Max Count",
        description="Upper limit for whitewater particles alive at the same time",
        default=2000000, min=1000, max=50000000, soft_max=10000000)
    gischt_an_waenden: bpy.props.BoolProperty(
        name="also at Walls",
        description=("Also emit whitewater right at the domain walls. "
                     "Off looks calmer, because otherwise foam rises where "
                     "there is no obstacle at all"),
        default=False,
    )
    # How closely foam and bubbles follow the water. Both are relaxation
    # factors of the engine without a measured counterpart, so FLIP Fluids'
    # defaults. Before, they came from the terminal velocity of a DROP IN
    # AIR (partikel.nebel_kopplung) — 0.05 for foam, so foam hung almost
    # still in the falling jet (measured 0.19 m/s) and looked like hail.
    # Physically foam floats on the surface and drifts with it.
    schaum_mitfuehrung: bpy.props.FloatProperty(
        name="Foam Follows Water",
        description=("How closely foam moves with the water surface it floats on "
                     "(1 = fully, FLIP Fluids' default). Lower values spread foam out"),
        default=1.0, min=0.0, max=1.0)
    blasen_mitfuehrung: bpy.props.FloatProperty(
        name="Bubbles Follow Water",
        description=("How quickly air bubbles are carried along by the water "
                     "(1 = at once; 0.8 is FLIP Fluids' default)"),
        default=0.8, min=0.0, max=1.0)
    gischt_lebensdauer: bpy.props.FloatProperty(
        name="Lifetime",
        description=("Lifetime of whitewater in seconds. Deliberately without spread: "
                     "only then can a drop's age be read off, "
                     "which the mist rules rely on"),
        default=2.5, min=0.1, max=60.0, soft_max=10.0)

    # --- Amount per type: takes effect AFTER baking ----------------------
    # Filtered by the particle ID (one byte, 0..255), which stays the same
    # across frames. So the selection does not flicker while scrubbing.
    # Bake generously once, then dose.
    menge_nebel: bpy.props.FloatProperty(
        name="Mist", description="Share of the baked mist particles that is shown",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR')
    menge_spray: bpy.props.FloatProperty(
        name="Spray", description="Share of the baked spray particles",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR')
    menge_foam: bpy.props.FloatProperty(
        name="Foam", description="Share of the baked foam particles",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR')
    menge_bubble: bpy.props.FloatProperty(
        name="Bubbles", description="Share of the baked bubbles",
        default=1.0, min=0.0, max=1.0, subtype='FACTOR')

    # --- Wind on the water itself ---------------------------------------
    # Wind acts on the interface with the air. So this force only works in
    # the topmost cells below the water surface and fades out with depth.
    # A pool stays calm at the bottom, a thin jet is deflected
    # slightly.
    #
    #     dv = factor * (wind - v) * dt
    #
    # So the wind can never make the water faster than itself.
    wasser_wind: bpy.props.FloatProperty(
        name="Wind on Water",
        description=("How strongly the wind drags the water surface along. "
                     "0 = not at all, 0.05 = barely noticeable, 0.5 = clearly. "
                     "Whitewater and mist are independent of it"),
        default=0.05, min=0.0, max=5.0, soft_max=1.0, precision=3,
    )
    wasser_wind_tiefe: bpy.props.FloatProperty(
        name="Penetration Depth",
        description=("How many grid cells below the surface the wind still "
                     "acts. Small = only the skin, large = a thicker layer"),
        default=2.0, min=0.5, max=10.0, soft_max=5.0,
    )

    # --- Inflow ---------------------------------------------------------
    zufluss_geschwindigkeit: bpy.props.FloatVectorProperty(
        name="Velocity", size=3, default=(0.0, 0.0, 0.0),
        subtype='VELOCITY',
        description=("Velocity of the water leaving the inflow (m/s). Together with the "
                     "inflow's size it sets how much water flows. With 0 almost nothing "
                     "leaves the inflow"),
    )
    zufluss_erzwingen: bpy.props.BoolProperty(
        name="Constrain Velocity", default=True,
        description=("Water inside the inflow is forced to the velocity above, giving a "
                     "defined flow. Off: the inflow is refilled every step and the water "
                     "squirts out under that pressure"),
    )

    zufluss_substeps: bpy.props.IntProperty(
        name="Substep Emissions", default=1, min=0, soft_max=8,
        description=("How often water is emitted per simulation substep. Raise it against "
                     "stuttering streams from fast inflows; 0 = only on the first substep "
                     "of a frame (FLIP Fluids' setting and default)"),
    )

    # --- Water ----------------------------------------------------------
    start_geschwindigkeit: bpy.props.FloatVectorProperty(
        name="Initial Velocity", size=3, default=(0.0, 0.0, 0.0),
        subtype='VELOCITY',
        description="Velocity the water starts with (m/s)",
    )

    # --- All roles except the domains -------------------------------------
    rolle_aktiv: bpy.props.BoolProperty(
        name="Enabled", default=True,
        description=("The object takes part in the simulation. Off: it keeps its role "
                     "but is left out of the next bake"),
    )

    # --- Obstacle (FLIP Fluids' obstacle settings, same defaults) --------
    hindernis_umkehren: bpy.props.BoolProperty(
        name="Inverse", default=False,
        description=("Turn the obstacle inside-out: its inside becomes free space, everything "
                     "outside solid. Makes a closed shape a leak-proof container. Not for open "
                     "shapes like a glass the water has to leave, and only for closed meshes"),
    )
    hindernis_reibung: bpy.props.FloatProperty(
        name="Friction", default=0.0, min=0.0, max=1.0, precision=2,
        description=("How much the water sticks to the surface (0 = slides freely, 1 = "
                     "stops along it). An engine factor, not a measured friction coefficient"),
    )
    hindernis_gischt: bpy.props.FloatProperty(
        name="Whitewater Influence", default=1.0, min=0.0, soft_max=5.0, precision=2,
        description=("Scales how much foam, bubbles and spray form near this obstacle "
                     "(1 = normal, more above, less below)"),
    )
    hindernis_nebel: bpy.props.FloatProperty(
        name="Mist Emission", default=1.0, min=0.0, soft_max=5.0, precision=2,
        description=("Scales how much mist forms near this obstacle, on top of the domain's "
                     "mist strength (1 = normal, 0 = none here). Needs mist on the domain"),
    )
    hindernis_sheeting: bpy.props.FloatProperty(
        name="Sheeting Strength", default=1.0, min=0.0, soft_max=5.0, precision=2,
        description=("Scales the sheeting against this obstacle — thin water films that "
                     "would otherwise tear. Needs Sheeting on the domain"),
    )
    hindernis_ausdehnung: bpy.props.FloatProperty(
        name="Expand Geometry", default=0.0, soft_min=-0.05, soft_max=0.05,
        step=0.01, precision=5, subtype='DISTANCE',
        description=("Grows the obstacle by this distance, against water slipping through "
                     "small cracks between touching parts. Only small values; it does not fix "
                     "leaks in thin walls"),
    )

    # --- Outflow (FLIP Fluids' outflow settings, same defaults) ----------
    abfluss_wasser: bpy.props.BoolProperty(
        name="Remove Water", default=True,
        description="Water entering the outflow is removed",
    )
    abfluss_gischt: bpy.props.BoolProperty(
        name="Remove Whitewater", default=True,
        description="Foam, bubbles, spray and mist entering the outflow are removed",
    )
    abfluss_umkehren: bpy.props.BoolProperty(
        name="Inverse", default=False,
        description="Remove what is OUTSIDE the outflow mesh instead of inside",
    )
    abfluss_allmaehlich: bpy.props.BoolProperty(
        name="Gradual Outflow", default=False,
        description=("Remove water at a limited rate instead of at once on entering — "
                     "for a drain that should not swallow everything instantly"),
    )
    abfluss_rate: bpy.props.FloatProperty(
        name="Outflow Rate", default=8.0, min=0.0, precision=2,
        description=("Gradual outflow: at most this many outflow volumes of water removed "
                     "per second (half submerged = half of it)"),
    )


classes = (HydroKitObjectProperties,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.hydrokit = bpy.props.PointerProperty(
        type=HydroKitObjectProperties)


def unregister():
    if hasattr(bpy.types.Object, "hydrokit"):
        del bpy.types.Object.hydrokit
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
