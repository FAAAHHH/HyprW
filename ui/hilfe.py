# HyprW — the small (i) next to every setting
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Hovering shows the short version as a tooltip, clicking opens the whole
# explanation. Every text has three levels, so a beginner can stop after the
# first and a professional finds the formula and the source in the last:
#
#   In short        what it does, in plain words
#   How to use it   what higher and lower do, when to touch it
#   Technical       the model, formulas, sources; what is only approximated
#
# Facts here must match the code and the property descriptions. Sources are
# the ones given in properties/, sim/ and engine_patch/README.md.

import textwrap

import bpy

BAKE = "Takes effect with the next bake."
JETZT = "Acts at once, no new bake."
WASSER = "Takes effect when you press Apply Water."
LINSE = "Takes effect after Compute Lens Drops."

# Topic -> (title, in short, how to use it, technical, when it takes effect)
HILFE = {
    # --- Roles -----------------------------------------------------------
    "rolle": ("Object Role",
              "A role tells the simulation what an object is: the box the water lives in "
              "(Domain), water at the start (Water), something solid (Obstacle), a source "
              "(Inflow) or a drain (Outflow).",
              "Select an object and click a role. You need one Domain and at least one Water "
              "or Inflow. Mist Domain and Spray Domain are optional large boxes that let mist "
              "or spray keep flying beyond the Domain.",
              "One domain per scene. The object's mesh defines the shape. Mist and spray "
              "domains have no flow grid, so they cost no water simulation.",
              BAKE),
    "rolle_aktiv": ("Enabled",
                    "Switches this object on or off for the simulation without losing its role "
                    "or settings.",
                    "Handy for comparing: turn an obstacle or inflow off, bake, and see the "
                    "difference, instead of deleting it.",
                    "Objects that are off are left out when the bake collects the scene "
                    "(obstacles too, so water passes through their place).",
                    BAKE),
    "zufluss_geschwindigkeit": ("Inflow Velocity",
                                "How fast and in which direction water leaves this inflow.",
                                "The values are metres per second along X, Y and Z. A faster "
                                "inflow and a larger inflow object both mean more water. With "
                                "0 almost nothing comes out.",
                                "Flow rate = velocity x cross-section of the inflow.",
                                BAKE),
    "zufluss_erzwingen": ("Constrain Velocity",
                          "Forces the water inside the inflow to exactly the velocity above, so "
                          "the stream is steady and predictable.",
                          "Leave it on. Off, the inflow is simply refilled every step and water "
                          "squirts out under the pressure that builds up, which is much harder "
                          "to control.",
                          "Sets the engine's constrained fluid velocity on the inflow. Tried "
                          "off in a test: water shot out under pressure.",
                          BAKE),
    "zufluss_substeps": ("Substep Emissions",
                         "How often the inflow adds water within one simulation step.",
                         "Raise it (2 to 8) if a fast inflow produces a stuttering stream with "
                         "gaps. Otherwise leave it at 1.",
                         "0 emits only on the first substep of a frame. Same setting and default "
                         "as FLIP Fluids.",
                         BAKE),
    "start_geschwindigkeit": ("Initial Velocity",
                              "The speed this water already has in the very first frame, as if "
                              "it had been thrown.",
                              "Leave it at 0 for water at rest. Use it for a wave that is "
                              "already moving, or water poured from a glass.",
                              "Metres per second along X, Y and Z, applied once when the water "
                              "is added to the simulation.",
                              BAKE),
    "hindernis_umkehren": ("Inverse",
                           "Turns the obstacle inside-out: its inside becomes free space and "
                           "everything outside becomes solid.",
                           "Use it to make a leak-proof container from a closed shape, such as "
                           "a closed tank. Not for open shapes like a glass the water must "
                           "leave, and only for closed meshes.",
                           "Inverted obstacles skip the minimum thickness of 3 cells that normal "
                           "obstacles get.",
                           BAKE),
    "hindernis_reibung": ("Friction",
                          "How much water sticks to this surface: 0 slides freely, 1 stops "
                          "along it.",
                          "Rough rock might take a small value. Leave it at 0 if you are unsure; "
                          "high values make water crawl along the wall.",
                          "An engine factor, not a measured friction coefficient.",
                          BAKE),
    "hindernis_ausdehnung": ("Expand Geometry",
                             "Makes the obstacle a little fatter for the simulation, without "
                             "changing how it looks.",
                             "Use tiny values when water slips through small cracks between "
                             "touching parts. It does not repair leaks in thin walls: make those "
                             "walls thicker.",
                             "Distance in metres, the engine's mesh expansion. Obstacles need "
                             "about 3 cells of thickness to hold water reliably.",
                             BAKE),
    "hindernis_gischt": ("Whitewater Influence",
                         "How much foam, bubbles and spray form near this obstacle.",
                         "1 is normal, 0 none, above 1 more. Calm a wall that foams too much, or "
                         "strengthen the rock where the water hits. Needs Whitewater on the "
                         "domain.",
                         "Scales the engine's emission locally (whitewater influence of the "
                         "obstacle). A factor, not a physical quantity.",
                         BAKE),
    "hindernis_nebel": ("Mist Emission",
                        "How much mist forms near this obstacle.",
                        "1 is normal, 0 none here. Useful to keep mist at the impact zone only. "
                        "Needs Whitewater and Mist on the domain.",
                        "Multiplies with the domain's Mist Strength: dust emission strength = "
                        "Mist Strength x this value.",
                        BAKE),
    "hindernis_sheeting": ("Sheeting Strength",
                           "How strongly thin water films are held together on this obstacle.",
                           "Only does something with Sheeting on the domain, and Sheeting adds "
                           "water. 1 is normal.",
                           "Scales the engine's sheet seeding against this obstacle.",
                           BAKE),
    "abfluss_wasser": ("Remove Water",
                       "Water that enters this outflow disappears.",
                       "Leave it on for a drain. Turn it off if the outflow should only remove "
                       "whitewater.",
                       "The outflow's fluid_outflow switch in the engine.",
                       BAKE),
    "abfluss_gischt": ("Remove Whitewater",
                       "Foam, bubbles, spray and mist that enter this outflow disappear.",
                       "Leave it on to clean up at the edge of the scene. Turn it off if "
                       "whitewater should fly on through.",
                       "The outflow's diffuse_outflow switch in the engine.",
                       BAKE),
    "abfluss_umkehren": ("Inverse",
                         "Removes everything OUTSIDE this object instead of inside.",
                         "Use it to keep water only within a shape, for example a basin: what "
                         "leaves the shape is gone.",
                         "The engine's outflow_inverse.",
                         BAKE),
    "abfluss_allmaehlich": ("Gradual Outflow",
                            "Removes water bit by bit instead of all at once on entering.",
                            "For a drain that should not swallow everything instantly: the water "
                            "level sinks slowly. Set the speed with Outflow Rate.",
                            "The engine's enable_gradual_outflow.",
                            BAKE),
    "abfluss_rate": ("Outflow Rate",
                     "How quickly a Gradual Outflow removes water.",
                     "Lower is a slower drain. It only acts with Gradual Outflow on.",
                     "At most this many outflow volumes of water per second; half submerged "
                     "means half of it.",
                     BAKE),

    # --- Bake ------------------------------------------------------------
    "zwischenstand_an": ("Saved States",
                         "Keeps the state of the water during a bake, so a stopped or crashed "
                         "bake can go on instead of starting over.",
                         "Leave it on. Every sets how many frames may be lost at most after a "
                         "crash. Cancelling with Esc saves its last frame too. Bake Again and "
                         "Clear Cache delete the state.",
                         "Water particles with velocity and adaptive level, whitewater and the "
                         "frame, after FLIP Fluids' savestates. 25 bytes per water particle "
                         "(measured: 24 position and velocity, 1 level) and 30 per whitewater "
                         "particle; only the newest state stays, also after the bake ends.",
                         BAKE),
    "zwischenstand_weiter": ("Resume",
                             "Goes on from the saved state instead of starting over. Frames after "
                             "it are computed anew.",
                             "Raise End first to bake further than before. Materials, whitewater "
                             "and force settings may change; Domain, Resolution and Start may not, "
                             "the button then says why it is off.",
                             "The particles are the full state of a FLIP simulation, the grid "
                             "velocities are rebuilt from them on the next step. The result can "
                             "differ slightly from an uninterrupted bake.",
                             BAKE),
    "cache_pfad": ("Cache Folder",
                   "The folder where the baked simulation is stored on your disk.",
                   "Use a drive with plenty of free space. // means next to the .blend file, so "
                   "save the file first. The trash icon next to it empties the cache.",
                   "Holds meshes and particles per frame plus info.json. Measured on a waterfall "
                   "with 5.3 cm cells over 110 frames: 58 MB of mesh at Mesh Detail 1, 235 MB at "
                   "2, 551 MB at 3.",
                   BAKE),
    "frame_start": ("Frame Range",
                    "The first and last frame of the simulation.",
                    "Test with a short range first: 50 frames tell you a lot and cost little. "
                    "This is the same range as the timeline and F12 use, so changing it here "
                    "changes it there.",
                    "Blender's own scene frame range. Bake always starts over from the first "
                    "frame; a cancelled bake does not continue.",
                    BAKE),
    "rechengeraet": ("Compute Device",
                     "Whether the simulation runs on the processor (CPU), on the graphics card "
                     "(GPU), or a mix you choose per step (Both).",
                     "Take GPU if you have an NVIDIA card of the RTX 20 series or newer. If the "
                     "card runs out of memory or very small scenes feel slow, try Both and press "
                     "Pick the Fastest per Step: it measures instead of guessing.",
                     "Via CUDA. Any GPU problem falls back to the CPU. Mesh, whitewater and water "
                     "particles are bit-identical to the CPU, advection agrees to float rounding, "
                     "pressure agreed in every test. Waterfall: CPU 244 s, GPU 146 s for 120 "
                     "frames.",
                     BAKE),
    "geraet_mesh": ("Device: Surface Mesh",
                    "Where the water's surface is built from the particles.",
                    "GPU is usually faster. In tiny domains the CPU can win; the measuring button "
                    "tells you.",
                    "Distance field, marching cubes, smoothing and the lighter viewport mesh. "
                    "Both devices give exactly the same mesh (verify mode).",
                    BAKE),
    "geraet_gischt": ("Device: Whitewater",
                      "Where the step that creates foam, bubbles and spray runs.",
                      "GPU is usually much faster here (measured 12x on the waterfall).",
                      "On the GPU so far: the surface curvature that decides where whitewater "
                      "forms, over half of the whitewater time. Exactly the same result as the "
                      "CPU.",
                      BAKE),
    "geraet_partikel": ("Device: Water Particles",
                        "Where the shape of the water is worked out from its particles.",
                        "GPU is usually much faster here (measured 8x on the waterfall).",
                        "On the GPU so far: the liquid level set from the particles, about 70 % "
                        "of the step. Exactly the same result as the CPU.",
                        BAKE),
    "geraet_advektion": ("Device: Advection",
                         "Where the particles hand their speed over to the simulation grid.",
                         "GPU is usually faster (measured 5x on the waterfall).",
                         "The FLIP velocity transfer. The GPU adds the same contributions in "
                         "another order, so it agrees up to float rounding: at most 0.000002 m/s "
                         "apart (measured).",
                         BAKE),
    "geraet_druck": ("Device: Pressure",
                     "Where the pressure is solved, which is what keeps water from being "
                     "squeezed together.",
                     "GPU gains least here (measured 2x on the waterfall) and very small domains "
                     "are faster on the CPU.",
                     "Same PCG method and MIC(0) preconditioner, the same convergence test. "
                     "Agreed with the CPU in every test.",
                     BAKE),

    # --- Water look ------------------------------------------------------
    "wasser_variante": ("Water Type",
                        "A starting point for the look of the water, from crystal clear to "
                        "murky.",
                        "Pick one, click the water in the viewport and press Apply Water. It "
                        "fills Visibility and Dissolved Matter under Fine-tune. The type shows "
                        "in deep water; thin falling water stays clear for most types.",
                        "Points inside measured ranges: Clear 10.75 m, Clean Blue 6 m, Mountain "
                        "Stream 3 m, Algae 1.6 m, Murky 0.25 m. Ocean 20 m lies outside the river "
                        "data (approximation). Algae pigments are not modelled.",
                        WASSER),
    "wasser_sichtweite": ("Visibility",
                          "How far you could see a black object under water. Low values mean "
                          "murky water, high values clear water.",
                          "1 m looks like a river after rain, 3 m like a mountain stream, 10 m "
                          "and more like very clear water. If your water looks like glass, lower "
                          "it.",
                          "Beam attenuation c = 4.8 / visibility (Davies-Colley 1988; Zaneveld & "
                          "Pegau 2003, within 10 %). What is not absorbed at 550 nm is scattering "
                          "by particles, g = 0.924 (Petzold 1972). Rivers at low flow: 0.25 to "
                          "10.75 m (Davies-Colley & Close 1990).",
                          WASSER),
    "wasser_geloest": ("Dissolved Matter",
                       "Natural substances dissolved in the water, such as from leaves or soil. "
                       "They tint deep water yellow-brown to green.",
                       "0 keeps the pure blue of water. Mountain water is around 0.1 to 0.3. "
                       "Bog and forest streams go well above 1.",
                       "Absorption at 440 nm in 1/m (CDOM), falling towards red as "
                       "exp(-0.017 (lambda - 440)), on top of pure water (Pope & Fry 1997). "
                       "Mountain waters 0.063 to 0.35 (EGU 2020).",
                       WASSER),
    "wasser_kraeuselung": ("Ripples",
                           "How rough the water surface is at a very fine scale. It softens "
                           "reflections.",
                           "Small values such as the default 0.035 look right. At 0 the surface "
                           "is a perfect mirror and looks like liquid metal.",
                           "Roughness of the surface shader. A look setting without a measured "
                           "value.",
                           WASSER),
    "wasser_schatten": ("Light Through Surface",
                        "How much sunlight reaches the ground under the water. Raise it if the "
                        "bottom looks dark and noisy.",
                        "Leave it at 1 for most scenes. Lower it only if you want a darker "
                        "bottom.",
                        "Cycles can hardly find light paths refracted by a wavy surface. At 1 "
                        "shadow rays pass the surface less its Fresnel reflection, and the water "
                        "body still absorbs and scatters them as measured. A render "
                        "approximation.",
                        WASSER),
    "wasser_kaustik": ("Caustics",
                       "The bright, dancing net of light on the bottom of a pool.",
                       "Raise it for sunny, shallow water, set it to 0 for overcast light. Needs "
                       "Light Through Surface above 0.",
                       "APPROXIMATION: a pattern, not traced light. The mean brightness stays "
                       "the same, so it does not make the scene brighter overall.",
                       WASSER),
    "wasser_kaustik_groesse": ("Caustic Size",
                               "How large the cells of the light net on the bottom are.",
                               "Larger cells suit big waves, smaller cells small ripples. The "
                               "default of 15 cm fits most water.",
                               "APPROXIMATION: 15 cm suits short gravity waves, which do the "
                               "focusing.",
                               WASSER),
    "wasser_wellen": ("Capillary Ripples",
                      "Tiny waves too small for the simulation grid, so the surface does not "
                      "look unnaturally smooth up close.",
                      "1 is the default, 0 switches them off. Raise it for a breezy surface. "
                      "Hardly visible from far away.",
                      "Wavelength 1.71 cm, where capillary and gravity waves meet (surface "
                      "tension 72.75 mN/m at 20 °C, Vargaftik et al. 1983). They move with the "
                      "scene time. 1 = 0.5 mm height (APPROXIMATION).",
                      WASSER),
    "wasser_dispersion": ("Dispersion",
                          "Splits light into its colours like a prism, for a faint rainbow edge.",
                          "Leave it off unless you render close-ups of clear water: it is barely "
                          "visible in water and renders slower.",
                          "One glass shader per colour with water's measured refractive index "
                          "(Daimon & Masumura 2007, 20 °C): red 1.3319, green 1.3347, blue 1.3387.",
                          WASSER),

    # --- Whitewater look -------------------------------------------------
    "menge_foam": ("Foam Amount",
                   "How much of the baked foam you see.",
                   "Bake generously once, then dose it here. 100 % shows everything.",
                   "Filters by each particle's ID, which stays the same across frames, so the "
                   "selection does not flicker while scrubbing.",
                   JETZT),
    "menge_bubble": ("Bubbles Amount",
                     "How much of the baked underwater bubbles you see.",
                     "Bake generously once, then dose it here. 100 % shows everything.",
                     "Filters by each particle's ID, which stays the same across frames, so the "
                     "selection does not flicker while scrubbing.",
                     JETZT),
    "menge_spray": ("Spray Amount",
                    "How much of the baked spray you see.",
                    "Bake generously once, then dose it here. 100 % shows everything.",
                    "Filters by each particle's ID, which stays the same across frames, so the "
                    "selection does not flicker while scrubbing.",
                    JETZT),
    "menge_nebel": ("Mist Amount",
                    "How much of the baked mist you see.",
                    "Bake generously once, then dose it here. 100 % shows everything.",
                    "Filters by each particle's ID, which stays the same across frames, so the "
                    "selection does not flicker while scrubbing.",
                    JETZT),
    "nebel_dichte_anzeige": ("Mist Density",
                             "How thick the mist looks in the render.",
                             "Raise it for heavier mist, but not too far: very high density "
                             "makes mist DARKER, not brighter, because it then absorbs more than "
                             "it scatters.",
                             "Calibrated on a waterfall: radius x density should come to about "
                             "0.16 (the default 2.0). Radius 1.5 voxels.",
                             JETZT),
    "schaum_blasen": ("Foam Bubbles",
                      "How many little bubbles you see across one foam particle in the render.",
                      "0 is plain foam without texture. Around 4 looks good. Only visible up "
                      "close.",
                      "A look setting: thin bright walls with a highlight per bubble. Real foam "
                      "bubbles (0.1 mm to a few mm, Deane & Stokes 2002) are far smaller than the "
                      "drawn particles.",
                      JETZT),
    "schaum_luftanteil": ("Air in Foam",
                          "How much air is mixed into the foam. More air looks denser and "
                          "whiter, less looks clearer and more like milky water.",
                          "20 % is a good default. Raise it for bright white foam, lower it for "
                          "translucent foam.",
                          "Scattering sigma = 1.5 phi / a with 1 mm bubbles (Q = 2, van de Hulst "
                          "1957), solved in closed form in the shader: same on CPU and GPU. "
                          "Breaking waves measured up to 24 % (Lamarre & Melville 1992). At 20 %, "
                          "through a 13 mm particle: 15 % passes straight, 30 % is reflected, "
                          "55 % leaves diffusely.",
                          JETZT),
    "spray_farbe": ("Spray Colour",
                    "A tint of the light passing through spray drops.",
                    "The default is a faint blue that many like. White is the physically correct "
                    "colour.",
                    "A look setting: water absorbs red at 0.34 /m (Pope & Fry 1997), over 1 mm of "
                    "drop that is 0.03 %, so real drops this small are colourless.",
                    JETZT),
    "whitewater_scale_multiplier": ("Overall Size",
                                    "Makes all whitewater particles bigger or smaller together.",
                                    "1 is the computed size. If single balls look like hail, "
                                    "lower it and bake more, finer particles.",
                                    "Drawn radius = voxel size x factor per type (foam 0.24, "
                                    "bubbles 0.20, spray 0.14, mist 0.10) x this value x each "
                                    "particle's size spread. Real drops would be invisibly small "
                                    "in a large scene.",
                                    JETZT),
    "use_motion_blur": ("Motion Blur",
                        "Smears fast drops into streaks, like a real camera does.",
                        "Leave it on for whitewater: without it every drop is a sharp ball and "
                        "spray looks like hail. It only shows in renders, not in the viewport.",
                        "Blender's motion blur. HyprW writes each particle's velocity as an "
                        "attribute for Cycles.",
                        JETZT),
    "motion_blur_shutter": ("Shutter",
                            "How long the camera shutter stays open. Longer means longer streaks.",
                            "0.5 frames is typical for film. Lower gives crisper drops, higher "
                            "gives smoother, more flowing water.",
                            "In frames; Blender's setting.",
                            JETZT),

    # --- Domain ----------------------------------------------------------
    "aufloesung": ("Resolution",
                   "How fine the simulation is. Higher values show smaller details, and cost "
                   "much more.",
                   "Start low (around 65) to test the motion, raise it only when you like it. "
                   "Doubling it means about eight times the time and memory, so try small steps. "
                   "The numbers below the slider show cell size and memory.",
                   "Voxels along the longest domain side; both memory and time grow with its "
                   "cube. Memory measured at 300 and 600: about 150 bytes per voxel with little "
                   "water; up to roughly 1 KB per voxel full of water (estimate). The engine "
                   "handles about 2.1 billion cells at most (32-bit sizes); baking above that "
                   "is refused.",
                   BAKE),
    "cfl": ("CFL",
            "How far water may move in one calculation step. Smaller is more careful and slower.",
            "The default 3 is fine for most scenes. Try 2 if fast water explodes or passes "
            "through thin obstacles.",
            "Courant number: cells a particle may jump per step. A lower value forces more "
            "substeps per frame.",
            BAKE),
    "sheeting": ("Sheeting",
                 "Keeps very thin water sheets from tearing into holes by filling them with new "
                 "particles.",
                 "Leave it off for waterfalls: it ADDS water. It can help thin films running "
                 "down a surface.",
                 "The engine's sheet seeding. Measured: in an 8 m fall, where the whole jet is a "
                 "thin sheet, the pool filled halfway in 3 seconds and the bake took 377 instead "
                 "of 152 s.",
                 BAKE),
    "offene_raender": ("Open Borders",
                       "Which sides of the domain let water out instead of acting like solid "
                       "walls.",
                       "Open the side a stream flows towards, otherwise it piles up there. Open "
                       "only where water should leave: an open side beside a pool drains the "
                       "pool. An overflow outflow above the water level is often better there.",
                       "Sides -X, +X, -Y, +Y, -Z, +Z in world axes. All closed by default.",
                       BAKE),
    "oberflaechen_unterteilung": ("Mesh Detail",
                                  "How smooth and detailed the water surface is, independent of "
                                  "the simulation itself.",
                                  "2 is the right default. 1 makes thin falling water break into "
                                  "blobs with holes. 3 is the finest and takes far more disk "
                                  "space.",
                                  "Splits per simulation cell for the mesh. Waterfall, 110 "
                                  "frames, measured (detail: loose pieces, bake time, mesh "
                                  "cache): 1: 104, 49 s, 58 MB; 2: 24, 64 s, 235 MB; 3: 21, "
                                  "101 s, 551 MB. FLIP Fluids' default equals 2.",
                                  BAKE),
    "viewport_eigenes_netz": ("Lighter Viewport",
                              "Stores a second, lighter water mesh just for the viewport, so "
                              "scrubbing stays smooth. Renders keep the full mesh.",
                              "Switch it on if the timeline stutters. Detail sets how fine the "
                              "viewport mesh is and must stay below Mesh Detail, otherwise the "
                              "viewport simply shows the render mesh.",
                              "Costs some cache space; the bake computes both from the same "
                              "field.",
                              BAKE),
    "netz_anisotrop": ("Anisotropic Surface",
                       "Builds the water surface like dedicated liquid tools do: sharp "
                       "sheets and a clear pool instead of round blobs that swell like gel.",
                       "Switch it on and bake. Smoothness sets how calm the surface looks; "
                       "the amount of water barely changes with it. Mesh Detail 2 or 3 keep "
                       "it crisp; at 1 it looks soft.",
                       "Yu & Turk (SCA 2010): every particle's kernel is stretched along the "
                       "surface (4 : 1 at most, 25 neighbours needed). The field is smoothed "
                       "and cut where it holds exactly the particles' volume (dx^3 / 8 "
                       "each), then smoothed without shrinking (Taubin). Learning scene, "
                       "frames 106-120: spheres 10.1 m3 for 8.44 m3 of particles; this "
                       "+1.0 % at Mesh Detail 2, +0.4 % at 3, +3.6 % at 1, with no "
                       "flicker between frames (at most 0.2 % per frame).",
                       BAKE),
    "netz_glaettung": ("Smoothness",
                       "How calm the anisotropic surface looks.",
                       "0 keeps every ripple and drop, 0.5 is the default, above 1 small "
                       "drops melt into the water. The amount of water barely changes.",
                       "Gaussian smoothing of the field before the surface is cut, sigma in "
                       "simulation cells. The cut is chosen afterwards for the particles' "
                       "volume. Learning scene, Mesh Detail 2: +4.4 % at 0, +0.9 % at 0.5, "
                       "+0.2 % at 1.5; loose pieces 115, 47 and 2.",
                       BAKE),
    "vorschau_im_viewport": ("Coarse Preview",
                             "Shows a much coarser water mesh in the viewport, for very large "
                             "bakes. Renders still use the full mesh.",
                             "Only for scenes too heavy even for Lighter Viewport. Cells sets "
                             "how coarse it is. Thin jets break up into single blobs in this "
                             "preview, which is only how it looks here.",
                             "Cells along the longest domain side; 0 = no preview. Replaces the "
                             "viewport detail while on.",
                             BAKE),
    "gischt": ("Whitewater",
               "Simulates foam, bubbles, spray and mist on top of the water.",
               "Switch it on for anything lively. For a first test of the motion, leave it off: "
               "the bake is faster.",
               "The engine's diffuse particle simulation, with HyprW's droplet physics and "
               "wind. Roughly 22 % of the engine time on the waterfall.",
               BAKE),
    "schaum_mitfuehrung": ("Foam Follows Water",
                           "How closely foam moves with the water it floats on.",
                           "Leave it at 1. Lower values let foam fall behind and spread out.",
                           "A relaxation factor of the engine without a measured counterpart; "
                           "FLIP Fluids' default is 1. At 0.05 foam hung still in a falling jet "
                           "(measured 0.19 m/s) and looked like hail.",
                           BAKE),
    "blasen_mitfuehrung": ("Bubbles Follow Water",
                           "How quickly air bubbles are carried along by the water.",
                           "The default 0.8 suits most scenes. 1 means they go with the water at "
                           "once.",
                           "A relaxation factor of the engine without a measured counterpart; "
                           "FLIP Fluids' default is 0.8.",
                           BAKE),
    "nebel_an": ("Mist",
                 "Very fine droplets that form where water crashes and hang in the air.",
                 "Switch it on for waterfalls. Add a Mist Domain so the mist can drift beyond "
                 "the domain; without one it ends at the border.",
                 "Runs on the engine's dust channel with sinking from droplet physics instead of "
                 "sediment.",
                 BAKE),
    "nebel_spritz_anteil": ("Fine Fraction",
                            "How much of the splashing water can turn into mist at most.",
                            "Raise it for a misty waterfall. It is only reached on very hard "
                            "impacts; gentle splashes make almost none.",
                            "Fine fraction grows with the impact number K (Okawa 2006); this is "
                            "the maximum. Default 0.3.",
                            BAKE),
    "nebel_spritz_schwelle": ("Splash Threshold K",
                              "How hard a drop must hit the water before it splashes into mist.",
                              "Leave the measured value unless you need less mist from weak "
                              "impacts: higher means fewer drops splash.",
                              "K = We * Oh^-0.4 (Cossali 1997: 2100). For 0.8 mm drops that is "
                              "an impact speed of 4.6 m/s. In the air, drops only break up above "
                              "Weber number 12 (Pilch & Erdman).",
                              BAKE),
    "nebel_staerke": ("Mist Strength",
                      "How much mist the obstacles give off.",
                      "1 is normal. Raise it if the impact zone looks too dry. Per obstacle you "
                      "can scale it further with Mist Emission.",
                      "The engine only creates this mist near obstacles, for a waterfall that is "
                      "the impact zone.",
                      BAKE),
    "nebel_rate": ("Mist Rate",
                   "How often mist particles are created.",
                   "Raise it for a denser cloud in the simulation, at the cost of more particles "
                   "and bake time.",
                   "The engine's dust emission rate. An engine factor, not a physical quantity.",
                   BAKE),
    "gischt_tempo_min": ("Speed Range",
                         "How fast water must flow to make whitewater. Below 'from' nothing "
                         "forms, above 'to' it does not get any stronger.",
                         "If every calm stream foams, raise 'from'. If nothing forms, lower it. "
                         "A fall of 1 m reaches about 4.4 m/s.",
                         "Metres per second, passed to the engine as energy E = 0.5 v^2. Tuned "
                         "on a waterfall: with speed 0.3, curvature 0.02 and turbulence 5 there "
                         "were 173,495 foam particles at frame 60, whitewater everywhere.",
                         BAKE),
    "gischt_kruemmung_min": ("Curvature Range",
                             "How sharp a wave crest must be to make whitewater.",
                             "Lower 'from' so that gentler waves foam, raise it to keep only "
                             "sharp crests. Too low and every ripple foams.",
                             "Surface curvature thresholds of the engine (wave crest emission), "
                             "tuned on a waterfall.",
                             BAKE),
    "gischt_turbulenz_min": ("Turbulence Range",
                             "How much the water must swirl to make whitewater.",
                             "Lower 'from' for more foam in rough water, raise it for foam only "
                             "where it really churns.",
                             "Turbulence thresholds of the engine, tuned on a waterfall. Without "
                             "a physical unit.",
                             BAKE),
    "gischt_rate_wellen": ("Rate Wave Crests",
                           "How much whitewater forms at sharp wave crests.",
                           "Higher means more particles, and more bake time and memory.",
                           "The engine's wavecrest emission rate. An engine factor.",
                           BAKE),
    "gischt_rate_turbulenz": ("Rate Turbulence",
                              "How much whitewater forms where water swirls.",
                              "Higher means more particles, and more bake time and memory.",
                              "The engine's turbulence emission rate. An engine factor.",
                              BAKE),
    "gischt_hoechstzahl": ("Max Count",
                           "An upper limit for whitewater particles alive at the same time, "
                           "which protects your memory.",
                           "Lower it if a bake fills your RAM. Raise it if whitewater visibly "
                           "stops forming in a big scene.",
                           "The engine's max_num_diffuse_particles. Default 2 million.",
                           BAKE),
    "gischt_lebensdauer": ("Lifetime",
                           "How many seconds a whitewater particle lives.",
                           "Longer means more foam stays around, and more particles. Shorter "
                           "cleans up faster.",
                           "Deliberately without spread: only then can a drop's age be read off, "
                           "which the older mist rules rely on.",
                           BAKE),
    "gischt_an_waenden": ("Also at Walls",
                          "Allows whitewater right at the domain walls.",
                          "Leave it off, otherwise foam can rise where there is no obstacle at "
                          "all.",
                          "Off insets the emission region by 3.5 cells, as FLIP Fluids does.",
                          BAKE),
    "nebel_groesse": ("Mist d50",
                      "How large mist droplets are. Smaller droplets float longer.",
                      "The default 0.26 mm is a measured value for impacting jets; lower it for "
                      "mist that hangs longer.",
                      "Size parameter in mm of a gamma distribution (shape 2, scale 0.5 d50); "
                      "most common size = half the value. Measured 40 to 400 micrometres. Motion "
                      "after Lian et al. 2019 with Schiller-Naumann drag.",
                      BAKE),
    "spray_groesse": ("Spray d50",
                      "How large spray drops are. Larger drops fall faster and feel less wind.",
                      "The default of 3 mm is measured; lower it for finer spray that the wind "
                      "carries further.",
                      "Size parameter in mm of a gamma distribution, after the dam atomisation "
                      "spray model of Lian et al. 2019. Droplets between 0.125 and 8.5 mm, their "
                      "measured range.",
                      BAKE),
    "fluessigkeit": ("Liquid",
                     "Which liquid: water, olive oil, glycerol, honey or lava. It sets "
                     "viscosity, density and surface tension to measured values.",
                     "Pick a liquid, then switch on Viscosity and, for small scenes, Surface "
                     "Tension. You can change the three values by hand; the title then says "
                     "custom.",
                     "At about 20 °C. Water 1.0 mPa·s, 998 kg/m³, 72.7 mN/m. Olive oil 84 mPa·s, "
                     "915 kg/m³, 32 mN/m (30.7-33 measured). Glycerol 1.412 Pa·s, 1261 kg/m³, "
                     "64 mN/m. Honey 10 Pa·s (0.42-23.4 measured, Yanniotis et al. 2006, with "
                     "water content and temperature), 1420 kg/m³; its surface tension is an "
                     "APPROXIMATION (glycerol's). Lava: basalt 1000 Pa·s (10^3-10^4 in the "
                     "field, Chevrel et al. 2019), 2620 kg/m³ as a melt, surface tension "
                     "APPROXIMATION 0.35 N/m; cooling and setting are not simulated.",
                     BAKE),
    "zaehigkeit_an": ("Viscosity",
                      "Makes the liquid thick: it flows slower, piles up and folds.",
                      "Switch it on for honey, oil or lava. For water leave it off: its "
                      "viscosity is far too small to show at the size of a scene.",
                      "Variational viscosity after Batty & Bridson 2008, solved implicitly each "
                      "step, so even lava stays stable. The engine takes the kinematic "
                      "viscosity, viscosity divided by density. Costs a solve per step: the "
                      "more viscous, the longer. Off: bit-identical to before.",
                      BAKE),
    "zaehigkeit": ("Viscosity (Pa·s)",
                   "How thick the liquid is, in pascal seconds.",
                   "Water 0.001, olive oil 0.084, glycerol 1.4, honey around 10, lava 1000. "
                   "Choosing a liquid sets it.",
                   "Dynamic viscosity mu. The engine gets mu / density in m²/s.",
                   BAKE),
    "dichte": ("Density (kg/m³)",
               "How heavy the liquid is per cubic metre.",
               "Choosing a liquid sets it. It only matters together with viscosity or "
               "surface tension: both act per kilogram.",
               "The engine solves with density 1 and gets viscosity and surface tension "
               "divided by this.",
               BAKE),
    "oberflaechenspannung_an": ("Surface Tension",
                                "Pulls the surface together: drops round off, thin streams "
                                "hold together.",
                                "Worth it in small scenes, drops and splashes of centimetres. "
                                "At the size of a waterfall the cells are far larger than "
                                "anything it shapes; the panel then says so.",
                                "A pressure jump of surface tension times curvature at the "
                                "surface. Explicit, so each step is limited to 0.4875 sqrt(dx³ "
                                "rho / sigma) (FLIP Fluids' default accuracy): fine cells cost "
                                "many extra steps, the panel shows how many.",
                                BAKE),
    "oberflaechenspannung": ("Surface Tension (N/m)",
                             "How strongly the surface pulls together, in newtons per metre.",
                             "Water 0.0727, glycerol and honey 0.064, olive oil 0.032. Choosing "
                             "a liquid sets it.",
                             "sigma. The engine gets sigma / density in m³/s². FLIP Fluids "
                             "scales its own value by 0.1; HyprW passes the measured one.",
                             BAKE),
    "adaptiv_an": ("Hybrid Water",
                   "Deep water merges into bigger particles, and it stays fine near the air. A "
                   "big lake behind a fine waterfall then costs far fewer particles.",
                   "Switch it on for scenes with deep, calm water. A waterfall alone gains little. "
                   "The grid stays as fine as your Resolution, so the surface looks the same.",
                   "Adaptive particles after Adams, Pauly, Keiser & Guibas 2007. Level L = the volume of 2^L "
                   "standard particles. Merged particles stay out of the level set, the FLIP "
                   "transfer and the mesher. Off: bit-identical to before. Measured, 30 frames on "
                   "the GPU: 2.76 million carried by 1.39 million, 75 s to 64 s, same volume.",
                   BAKE),
    "adaptiv_hebel": ("Hybrid Strength",
                      "One lever for Hybrid Water: how coarse deep water may become. At 1 only "
                      "a little and only deep down, at 6 up to 64 particles merge into one. Near "
                      "the air and obstacles the water always stays fine.",
                      "Start at 3, the default. Higher saves more particles and time in deep, "
                      "calm water; lower keeps more of the water fine. Where the water is "
                      "shallower than the fine layer, as in most waterfall pools, nothing "
                      "merges at any position. The lines under the lever "
                      "show what a position means for your domain: the resolution of deep water, "
                      "the largest particle, how far below the surface and from obstacles the "
                      "water always stays fine, and from which depth the largest particles form. "
                      "Advanced holds the values the lever sets. 'custom' there means they were "
                      "changed by hand or come from an older file; they stay until you move the "
                      "lever.",
                      "Position L = the largest level: 2^L standard particles of dx^3/8, so deep "
                      "water keeps 2^(-L/3) of the resolution per axis: 79, 63, 50, 40, 31, 25 %. "
                      "Split Depth / Merge Depth / Obstacle Distance: 3 / 4.5 / 5 at 1, "
                      "2.5 / 3.75 / 4 at 2, and from 3 on 2 / 3 / 3, the values of Adams et al. "
                      "2007, Sec. 4.2 and the engine's collision band; position 3 is the old "
                      "default. Fine layer = merge depth of level 1 = beta R + 1 cells, "
                      "R = 0.866 dx 2^(1/3); moving water stays fine deeper (look-ahead over "
                      "speed x split time). Split Time is left as it is. Measured on a tank 28 "
                      "cells deep (Resolution 64, 3 s): 395k particles without, 375k / 321k / "
                      "314k at 1 / 3 / 6, the surface no further off than with another random "
                      "seed, the time per frame within the noise.",
                      BAKE),
    "adaptiv_max_stufe": ("Max Size",
                          "How large a merged particle may become.",
                          "Hybrid Strength sets it together with the depths; change it here to "
                          "set the level alone. The default 3 is one particle per cell. Higher "
                          "saves more particles in deep water, lower is more careful.",
                          "In halvings: every step doubles the volume. 3 = 8 standard particles.",
                          BAKE),
    "adaptiv_teilungsdauer": ("Split Time",
                              "How many seconds each halving or merge step takes. It spreads the "
                              "change out in time so that nothing jumps.",
                              "Leave it at 0.25 s. A particle about to reach the air or an "
                              "obstacle splits at once regardless.",
                              "APPROXIMATION: there is no measured value. Passes run once per "
                              "split time, one halving per particle.",
                              BAKE),
    "adaptiv_teil_faktor": ("Split Depth",
                            "How close to the surface a big particle may come before it splits "
                            "again.",
                            "Hybrid Strength sets it. Larger keeps fine particles further below "
                            "the surface: safer, but saves less.",
                            "Splits when depth < this many of its radii. Adams et al. 2007, Sec. "
                            "4.2: 2.",
                            BAKE),
    "adaptiv_verschmelz_faktor": ("Merge Depth",
                                  "How deep particles must be before they may merge.",
                                  "Hybrid Strength sets it. Keep it above Split Depth, otherwise "
                                  "particles flicker between merging and splitting.",
                                  "Merges only if the merged particle is deeper than this many of "
                                  "its radii. Adams et al. 2007, Sec. 4.2: 3.",
                                  BAKE),
    "adaptiv_hindernis_abstand": ("Obstacle Distance",
                                  "How far merged particles stay away from obstacles and walls.",
                                  "Hybrid Strength sets it: 5 at position 1, 4 at 2, 3 from 3 on. "
                                  "Lower than 3 saves slightly more particles and risks less "
                                  "accurate flow along walls.",
                                  "In cells. The engine resolves collisions within 3 cells.",
                                  BAKE),
    "wind_faktor": ("Wind Strength",
                    "Converts the strength of Blender's wind objects into real wind speed.",
                    "Leave it at 1: strength 5 is then 5 m/s. A typo here turns a breeze into a "
                    "hurricane that shreds every drop, so watch the Beaufort value in the panel.",
                    "m/s per unit of Blender's force field strength. Add wind with Add > Force "
                    "Field > Wind.",
                    BAKE),
    "wasser_wind": ("Wind on Water",
                    "How strongly the wind pulls on the water's surface.",
                    "0 is not at all, 0.05 barely noticeable, 0.5 clear. Whitewater and mist feel "
                    "the wind regardless of this slider.",
                    "dv = factor x (wind - v) x dt in the cells below the surface, so the wind can "
                    "never make the water faster than itself.",
                    BAKE),
    "wasser_wind_tiefe": ("Penetration Depth",
                          "How deep below the surface the wind still acts.",
                          "Small means only the skin moves, large means a thicker layer. The "
                          "bottom of a pool stays calm either way.",
                          "In grid cells, weighted per axis by the share of the wind direction.",
                          BAKE),
    "domains_anzeigen": ("Domains",
                         "Shows or hides the coloured boxes of the domains in the viewport.",
                         "Hide them when they get in your way. Nothing changes in the simulation.",
                         "Domain yellow, spray domain blue, mist domain white.",
                         JETZT),
    "gitter_anzeigen": ("Show Grid",
                        "Draws the simulation cells on the domain walls: this is how finely your "
                        "water is computed.",
                        "Use it to check whether an obstacle is thick enough: about 3 cells or "
                        "more.",
                        "Viewport only, never in the render. Shows the mist domain's cells too.",
                        JETZT),
    "gitter_seiten": ("Grid on",
                      "On which walls of the domain the grid is drawn.",
                      "Visible Walls suits most views. Back Walls lets you look into the box.",
                      "Viewport only.",
                      JETZT),
    "ergebnisse_waehlbar": ("Selectable Results",
                            "Whether foam, spray, mist and lens drops can be clicked in the "
                            "viewport.",
                            "Leave it off, then clicks reach the objects inside the domain. Turn "
                            "it on only to select or inspect those results.",
                            "The mist volume covers the whole scene, so it would catch every "
                            "click. The water can always be clicked, for Apply Water.",
                            JETZT),
    "abspielen_leicht": ("Light Playback",
                         "While the timeline plays, the viewport shows a lighter version of the "
                         "bake: fewer whitewater points, a lighter water mesh and a coarser mist, "
                         "so playback stays fluid. Paused, you see the full result.",
                         "Leave it on. Turn it off only to watch the full result move, which "
                         "plays much slower on a large bake. Renders always use the full "
                         "result, whatever this says.",
                         "Only while playing: foam, bubbles and spray together about 150,000 "
                         "points, chosen by their ID like the amount sliders, with one share "
                         "for the whole bake, so the same particles stay visible from frame to "
                         "frame; mist points stay complete for the mist's own light look; no "
                         "velocity (only motion blur needs it). The water is the bake's lighter "
                         "viewport mesh if it has one, otherwise the full mesh merged on a grid "
                         "of 1.5 simulation cells (vertex clustering). The mist is drawn from at "
                         "most 50,000 of its points, each drop larger, in coarser cells: "
                         "measured on a waterfall, 12-19 ms instead of 170-560 ms per frame; "
                         "it is a viewport-only modifier on HK_Nebel that is removed again. When "
                         "playback stops, before saving and after opening a file everything is "
                         "full again.",
                         JETZT),
    "abspielen_andere_pausieren": (
        "Pause Other Animation",
        "While the timeline plays, heavy animation that is not HyprW's (trees swaying in the "
        "wind, for example) holds still, so the water plays smoothly. Stopped or rendered, "
        "everything moves as before.",
        "Switch it on when playback is slow and the panel shows other animation costing much "
        "time per frame. The line under the switch says how many objects it holds still. "
        "They keep the pose of the frame where you pressed play and move again the moment "
        "you stop. Renders and saved files never see the pause. Off, the line shows what the "
        "other animation costs per frame, and the closed section says Slow playback once it "
        "takes longer than a frame at 24 fps.",
        "On every frame Blender evaluates every animated object again; a botaniq tree with "
        "wind runs eleven modifiers over up to 0.6 M vertices. Measured on a scene with seven "
        "such trees: 556 ms per frame, 1 ms paused. Hiding the trees does not help, because "
        "their wind helpers are their children and pull them in. The pause unassigns the "
        "action slot of the heavy objects and of the objects their modifiers follow, keeping "
        "the action and its users, and assigns it again on stopping, before saving, loading "
        "and rendering, and after undo. Their keys vanish from the timeline while it plays. "
        "Linked data, library overrides, cameras, lights, HyprW's own objects and whatever "
        "they or the camera follow are never touched; objects with drivers, NLA or "
        "simulations keep moving.",
        JETZT),

    # --- Lens drops ------------------------------------------------------
    "linse_an": ("Lens Drops",
                 "Whatever flies onto the camera stays on the glass as drops: small ones stick, "
                 "large ones slowly run down.",
                 "Switch it on when the camera is close to the water. It computes on its own in "
                 "the background, and after every bake. It needs a scene camera.",
                 "Driven by the simulation: drops appear only where spray, mist or water really "
                 "reach the camera.",
                 JETZT),
    "linse_glas": ("Glass",
                   "The surface of the front lens, which decides how strongly drops stick.",
                   "Fluorine-coated is typical for modern lenses and filters: drops roll off "
                   "early. Uncoated glass holds much larger drops.",
                   "Uncoated: contact angles 60/44 degrees (Quere 1998), up to about 15 µl stick "
                   "to vertical glass. Coated: 115 degrees, sliding angle 16 degrees (Daikin "
                   "OPTOOL datasheet), hysteresis 11 degrees (low end of 11-15, an "
                   "APPROXIMATION), about 4 µl.",
                   LINSE),
    "linse_winkel_vor": ("Advancing Angle",
                         "How steep a drop's edge is when it is about to move forward.",
                         "Only for Glass = Custom. Larger angles mean more water-repellent "
                         "glass.",
                         "In degrees. Water on float glass: 60 (Quere 1998).",
                         LINSE),
    "linse_winkel_rueck": ("Receding Angle",
                           "How steep a drop's edge is when it is about to pull back.",
                           "Only for Glass = Custom, always below the advancing angle. The bigger "
                           "the gap between the two, the harder the drop sticks.",
                           "In degrees. Water on float glass: 44 (Quere 1998). Holding force pi r "
                           "gamma (cos receding - cos advancing).",
                           LINSE),
    "linse_durchmesser": ("Lens Diameter",
                          "How large the front lens is in reality. A smaller lens makes the same "
                          "drops look bigger in the image.",
                          "If drops look too big, raise it. 72 mm is a common filter thread.",
                          "In mm. The lens fills the image width.",
                          LINSE),
    "linse_fangradius": ("Capture Range",
                         "How close a simulation particle must get to the camera to count as a "
                         "hit.",
                         "Raise it for more drops, lower it for fewer, without any new bake.",
                         "One particle stands for a cloud of many drops, so this is not a "
                         "constant of nature but a scale for it.",
                         LINSE),
    "linse_unregelmaessig": ("Irregular Edge",
                             "How uneven the outline of a drop is. Real drops are never perfectly "
                             "round.",
                             "The default looks natural. 0 gives perfect circles.",
                             "Defects in the glass pin the edge (Cubaud & Fermigier 2001). "
                             "APPROXIMATION: there are no measurements for lens glass.",
                             JETZT),
    "linse_wasser_haftet": ("Water Sticks to Lens",
                            "Whether water reaching the camera leaves drops on the glass.",
                            "Turn it off if only spray and mist should land on the lens.",
                            "A hit is the water surface reaching the camera within Capture Range.",
                            LINSE),
    "linse_wasser_haftanteil": ("Water: Sticking Share",
                                "How much of the water reaching the lens stays on it. The rest "
                                "counts as bounced or run off.",
                                "Lower it for fewer drops.",
                                "Which hits stay is fixed by a seeded draw: same settings, same "
                                "result.",
                                LINSE),
    "linse_spray_haftet": ("Spray Sticks to Lens",
                           "Whether spray hitting the camera sticks to the glass.",
                           "Turn it off for a lens that only gets wet from mist or water.",
                           "Counts spray particles within Capture Range.",
                           LINSE),
    "linse_spray_haftanteil": ("Spray: Sticking Share",
                               "How much of the spray hitting the lens stays on it. The rest "
                               "counts as bounced off.",
                               "Lower it for fewer drops.",
                               "Which hits stay is fixed by a seeded draw: same settings, same "
                               "result.",
                               LINSE),
    "linse_spritzen": ("Splashing",
                       "Fast, large drops burst on impact instead of staying whole.",
                       "Leave it on. Off, every hit stays as one drop, with all its water.",
                       "After Mundo et al. 1995; threshold below.",
                       LINSE),
    "linse_spritz_schwelle": ("Splash Threshold K",
                              "How hard a drop must hit the lens before it bursts.",
                              "Higher means fewer splashes. Leave the measured value unless you "
                              "have a reason.",
                              "K = We^0.5 Re^0.25, measured 57.7 (Mundo et al. 1995).",
                              LINSE),
    "linse_spritz_rest": ("Remainder after Splash",
                          "How much of a bursting drop's water stays on the glass.",
                          "Lower means smaller, finer drops after a hit.",
                          "APPROXIMATION: no measured value was found. Default 50 %.",
                          LINSE),
    "linse_ausbreiten": ("Spread on Impact",
                         "A hitting drop briefly spreads out and merges with everything it "
                         "reaches.",
                         "Leave it on: hits then gather nearby drops instead of leaving them "
                         "side by side.",
                         "Spreading to D_max after Laan et al. 2014.",
                         LINSE),
    "linse_ausbreit_faktor": ("Spreading",
                              "How far an impact reaches when it spreads out.",
                              "Larger means impacts merge more neighbours. 1 is the measured "
                              "value.",
                              "A factor on D_max from Laan et al. 2014.",
                              LINSE),
    "linse_wasser_groesse": ("Water Drops",
                             "How big the drops are that the water surface leaves behind on the "
                             "glass.",
                             "Lower gives finer, more numerous drops.",
                             "Diameter in mm, also the cell size on the glass. APPROXIMATION "
                             "(a modelling choice): 2 mm.",
                             LINSE),
    "linse_rutschen": ("Sliding",
                       "Drops too heavy to hold on run down the glass.",
                       "Leave it on. Off, all drops stay where they land.",
                       "Slides when weight exceeds the holding force of the contact line (Quere "
                       "1998).",
                       LINSE),
    "linse_haft_faktor": ("Adhesion",
                          "How strongly drops hold on to the glass.",
                          "Smaller means drops start to slide at a smaller size.",
                          "A factor on the holding force pi r gamma dcos (Quere 1998); measured 1.",
                          LINSE),
    "linse_tempo_faktor": ("Sliding Speed",
                           "How fast drops run down.",
                           "Raise it for quicker runs. 1 is the published value.",
                           "A factor on the speed after Le Grand et al. 2005. Their value is for "
                           "silicone oil, not measured for water.",
                           LINSE),
    "linse_spuren": ("Trails",
                     "Fast drops leave a line of tiny droplets behind them.",
                     "Leave it on for a natural look. Needs Sliding.",
                     "Pearling after Le Grand et al. 2005.",
                     LINSE),
    "linse_perlen_schwelle": ("Trail from Ca",
                              "How fast a drop must run before it leaves a trail.",
                              "Lower means trails appear earlier, also behind slow drops.",
                              "Capillary number Ca = eta U / gamma in units of 10^-3. Measured on "
                              "silicone oil: 7.2 (Le Grand et al. 2005). For water about 0.5 m/s.",
                              LINSE),
    "linse_spur_groesse": ("Trail Droplets",
                           "How big the droplets in a trail are, compared with the drop that "
                           "leaves them.",
                           "0 means no trail. 10 % is the default.",
                           "Percent of the drop width. APPROXIMATION: the size is not quantified "
                           "in the source.",
                           LINSE),
    "linse_spur_streuung": ("Size Spread",
                            "How much the trail droplets differ in size.",
                            "Higher looks more natural. Low values look like a row of beads.",
                            "Plus or minus, in percent. APPROXIMATION.",
                            LINSE),
    "linse_spur_abstand": ("Spacing",
                           "How far apart the trail droplets are.",
                           "Higher means a sparser trail.",
                           "Mean, in droplet diameters. APPROXIMATION.",
                           LINSE),
    "linse_spur_abstand_streuung": ("Spacing Spread",
                                    "How irregular the gaps between trail droplets are.",
                                    "Higher looks more natural.",
                                    "Plus or minus, in percent. APPROXIMATION.",
                                    LINSE),
    "linse_spur_wackeln": ("Sideways Wobble",
                           "How much a trail wanders left and right instead of a straight line.",
                           "A little looks natural; 0 is a ruler-straight trail.",
                           "In drop radii, plus or minus half. APPROXIMATION.",
                           LINSE),
    "linse_spur_satelliten": ("Satellites",
                              "How many trail droplets get a tiny neighbour, as from a dripping "
                              "tap.",
                              "0 means none. A detail you see only up close.",
                              "Share in percent. APPROXIMATION.",
                              LINSE),
    "linse_spur_satellit_groesse": ("Satellite Size",
                                    "How big those tiny neighbours are.",
                                    "The default looks natural.",
                                    "Percent of the droplet's diameter, plus or minus a third. "
                                    "APPROXIMATION.",
                                    LINSE),
    "linse_spur_streckung_min": ("Elongation",
                                 "How stretched the trail droplets are along their path.",
                                 "1 is round, higher is longer. The range sets the shortest and "
                                 "longest.",
                                 "Aspect ratio from - to. APPROXIMATION.",
                                 LINSE),
    "linse_spur_wischen": ("Wipe Mist",
                           "Running drops clear the fine mist from the glass along their path, "
                           "like a raindrop on a misted window.",
                           "Leave it on for the clear streaks this gives.",
                           "Drops pick up the mist along their whole path.",
                           LINSE),
    "linse_nebel_haftet": ("Mist on the Lens",
                           "Whether mist settles on the glass as tiny droplets.",
                           "Switch it on when the camera stands in the mist. The lens then fogs "
                           "up over time.",
                           "Deposit = liquid water content x speed x lens area x collection "
                           "efficiency x time. One mist particle stands for a cloud of droplets.",
                           LINSE),
    "linse_nebel_haftanteil": ("Mist: Sticking Share",
                               "How much of the arriving mist stays on the glass. The rest counts "
                               "as blown past.",
                               "Lower it for a lens that fogs up less.",
                               "A factor on the deposit.",
                               LINSE),
    "linse_nebel_fang": ("Collection Efficiency",
                         "Fine droplets flow around the camera with the air, so only part of "
                         "them hits the glass.",
                         "Leave it on. Off, everything that arrives stays and the lens fogs up "
                         "faster.",
                         "eta = St / (St + pi / 2), St = rho d^2 U / (18 eta_air D/2) (Langmuir "
                         "& Blodgett).",
                         LINSE),
    "linse_nebel_menge": ("Mist Amount",
                          "How much mist lands on the lens overall.",
                          "Raise it for a quicker fog on the glass.",
                          "A factor on the deposit; 1 is derived from the calibrated mist "
                          "density.",
                          LINSE),
    "linse_nebel_verschmelzen": ("Merge Droplets",
                                 "Touching mist droplets grow into larger drops that eventually "
                                 "run down.",
                                 "Leave it on. Off, the mist stays a fine fog.",
                                 "Fast numpy neighbour search per cell in sim/linse.py.",
                                 LINSE),
    "linse_nebel_zu_tropfen": ("Becomes Drop at Width",
                               "From this size a grown mist droplet counts as a normal drop.",
                               "The default of 1 mm is fine.",
                               "Purely a drawing choice, not physics: drawn in detail, can "
                               "slide. Small drops do not slide anyway.",
                               LINSE),
    "linse_max_tropfen": ("Max Drops",
                          "An upper limit of drops on the lens, so that drawing stays fast.",
                          "Lower it if scrubbing gets slow. The smallest drops are dropped first.",
                          "A drawing limit, not physics.",
                          LINSE),
    "linse_max_nebel": ("Max Mist Droplets",
                        "An upper limit of fine droplets on the lens, so that drawing stays fast.",
                        "Lower it if scrubbing gets slow. The oldest droplets are dropped first.",
                        "A drawing limit, not physics.",
                        LINSE),
    "use_dof": ("Depth of Field",
                "Blurs what is out of focus, as a real lens does. It is what makes drops on the "
                "glass look soft or sharp.",
                "Switch it on for realism with lens drops. These settings change the scene "
                "camera itself.",
                "Blender's camera setting, shown here for convenience.",
                JETZT),
    "focus_object": ("Focus on Object",
                     "Keeps the focus on this object, even when it or the camera moves.",
                     "Pick the main object of your shot, or leave it empty and use Focus "
                     "Distance.",
                     "Blender's camera setting. Overrides Focus Distance.",
                     JETZT),
    "focus_distance": ("Focus Distance",
                       "How far from the camera things are sharp.",
                       "The eyedropper picks a distance by clicking in the viewport. Only used "
                       "without a focus object.",
                       "Blender's camera setting, in metres.",
                       JETZT),
    "aperture_fstop": ("F-Stop",
                       "How strong the blur is. A low number blurs a lot, a high number little.",
                       "Try 2.8 for strong blur, 16 for nearly everything sharp.",
                       "Blender's camera setting: focal length divided by aperture diameter.",
                       JETZT),
    "aperture_blades": ("Blades",
                        "The shape of blurred highlights.",
                        "0 is round. 5 to 8 gives the polygons of a real lens.",
                        "Blender's camera setting: the number of aperture blades.",
                        JETZT),
    "aperture_rotation": ("Rotation",
                          "Turns the polygon shape of the highlights.",
                          "Only visible with Blades above 0.",
                          "Blender's camera setting.",
                          JETZT),
    "aperture_ratio": ("Ratio",
                       "Stretches blurred highlights into ovals, like an anamorphic film lens.",
                       "1 keeps them round.",
                       "Blender's camera setting.",
                       JETZT),

    # --- Add-on preferences ----------------------------------------------
    "scroll_schritte": ("Sidebar Scroll Speed",
                        "How many times faster the HyprW tab scrolls, because it is long.",
                        "1 is Blender's normal speed, 8 the default.",
                        "Wheel steps per notch and touchpad distance per swipe. Other tabs and "
                        "editors keep Blender's own scrolling.",
                        JETZT),
}

# Settings that share one row, and so one (i), with another setting
GLEICH = {
    "zwischenstand_alle": "zwischenstand_an",
    "frame_end": "frame_start",
    "unterteilung_viewport": "viewport_eigenes_netz",
    "vorschau_aufloesung": "vorschau_im_viewport",
    "gischt_tempo_max": "gischt_tempo_min",
    "gischt_kruemmung_max": "gischt_kruemmung_min",
    "gischt_turbulenz_max": "gischt_turbulenz_min",
    "linse_spur_streckung_max": "linse_spur_streckung_min",
}

ABSCHNITTE = (("In short", 'INFO', 1), ("How to use it", 'TOOL_SETTINGS', 2),
              ("Technical", 'SCRIPT', 3))
BREITE = 460        # popup width
ZEICHEN = 68        # characters per line at that width, independent of UI scale


class HYDROKIT_OT_hilfe(bpy.types.Operator):
    bl_idname = "hydrokit.hilfe"
    bl_label = "About this setting"
    bl_options = {'INTERNAL'}

    thema: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'})

    @classmethod
    def description(cls, context, properties):
        eintrag = HILFE.get(properties.thema)
        if eintrag is None:
            return "About this setting"
        return f"{eintrag[1]}\n\nClick for the full explanation"

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=BREITE)

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        eintrag = HILFE.get(self.thema)
        layout = self.layout
        if eintrag is None:
            layout.label(text="No explanation for this setting", icon='ERROR')
            return
        layout.label(text=eintrag[0])
        layout.separator()
        for titel, icon, feld in ABSCHNITTE:
            layout.label(text=titel, icon=icon)
            spalte = layout.column(align=True)
            spalte.scale_y = 0.85
            for zeile in textwrap.wrap(eintrag[feld], ZEICHEN):
                spalte.label(text=zeile)
            layout.separator()
        layout.label(text=eintrag[4], icon='TIME')


classes = (HYDROKIT_OT_hilfe,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
