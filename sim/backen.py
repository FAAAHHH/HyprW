# HyprW — the bake loop
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Builds a FluidSimulation from the Blender scene, computes frame by frame and
# stores the result in the cache. Runs without FLIP Fluids.

import os
import time

from . import cache, geometrie, partikel, partikel


# ENGINE EXTENSIONS (engine_patch/). The LOADED library is checked, not just
# the Python wrapper: if fluidsimulation.py is new but the .so old, the call
# would otherwise only fail in the middle of the bake. Earlier a try/except
# swallowed the error here — then the wind sliders silently did nothing.
ERWEITERUNGEN = (
    ("wind",         "Wind on whitewater",  "FluidSimulation_set_diffuse_wind_velocity"),
    ("wasser_wind",  "Wind on the water",   "FluidSimulation_set_surface_wind_velocity"),
    ("zerstaeubung", "Mist model",          "FluidSimulation_set_diffuse_spray_mist_model"),
    ("nebel_domain", "Mist domain",         "FluidSimulation_set_diffuse_dust_outer_bounds"),
    ("spritzer_domain", "Spray domain",     "FluidSimulation_set_diffuse_spray_outer_bounds"),
    ("tropfen",      "Droplet physics",     "FluidSimulation_set_diffuse_spray_droplet_model"),
    ("gpu",          "GPU support",         "HKGPU_is_available"),
    ("gpu_gischt",   "Whitewater on the GPU", "FluidSimulation_enable_gpu_whitewater"),
    ("gpu_partikel", "Water particles on the GPU", "FluidSimulation_enable_gpu_water_particles"),
    ("gpu_advektion", "Advection on the GPU", "FluidSimulation_enable_gpu_advection"),
    ("gpu_druck",    "Pressure on the GPU", "FluidSimulation_enable_gpu_pressure"),
    ("adaptiv",      "Adaptive particles",  "FluidSimulation_enable_adaptive_particles"),
)

# The steps of a frame and their GPU versions (engine_patch/README.md section
# 8): (key, name, engine property that switches the GPU on, engine extension
# needed, GPU step in ffengine.gpu.counts). None = no GPU version yet.
# Measured on the waterfall (1.17 s per frame): whitewater 22 %, mesh 20 %,
# particles 18 %, advection 18 %, pressure 14 %.
STUFEN = (
    ("mesh",      "Surface Mesh",    "enable_gpu_surface_meshing", "gpu",        "mesh"),
    ("gischt",    "Whitewater",      "enable_gpu_whitewater",      "gpu_gischt", "curvature"),
    ("partikel",  "Water Particles", "enable_gpu_water_particles", "gpu_partikel", "water"),
    ("advektion", "Advection",       "enable_gpu_advection",       "gpu_advektion", "advection"),
    ("druck",     "Pressure",        "enable_gpu_pressure",        "gpu_druck",    "pressure"),
)

# Mesh cells per piece of the surface mesh, as FLIP Fluids' Auto mode
# (domain_surface_properties.default_cells_per_compute_chunk, 15 million)
NETZ_ZELLEN_PRO_STUECK = 15_000_000


def hat_gpu(stufe):
    """The loaded engine has a GPU version of this step."""
    eintrag = next(e for e in STUFEN if e[0] == stufe)
    return eintrag[2] is not None and engine_erweiterungen().get(eintrag[3], False)
_gpu_info = None


def gpu_info():
    """(available, device name, reason if not) — probed once per session.
    The probe opens the CUDA driver but creates no context: no GPU memory."""
    global _gpu_info
    if _gpu_info is None:
        if not engine_erweiterungen().get("gpu"):
            _gpu_info = (False, "", "Engine without GPU support")
        else:
            try:
                from ..ffengine import gpu
                _gpu_info = (gpu.is_available(), gpu.device_name(), gpu.status_message())
            except Exception as e:
                _gpu_info = (False, "", f"{type(e).__name__}: {e}")
    return _gpu_info


def geraet(props, stufe):
    """'GPU' or 'CPU': where this step runs under the domain's setting."""
    if not hat_gpu(stufe) or props.rechengeraet == 'CPU':
        return 'CPU'
    if props.rechengeraet == 'GPU':
        return 'GPU'
    return getattr(props, "geraet_" + stufe, 'CPU')
_faehig = None


def engine_erweiterungen():
    """{schluessel: bool} — what the loaded engine can do. Checked once."""
    global _faehig
    if _faehig is None:
        try:
            from ..ffengine.ffengine import ffengine as lib
            _faehig = {k: hasattr(lib, sym) for k, _, sym in ERWEITERUNGEN}
        except Exception:
            _faehig = {k: False for k, _, _ in ERWEITERUNGEN}
    return _faehig


_neustart = None


def neustart_noetig():
    """True if the engine file ON DISK can do what the loaded one lacks.
    Then the engine was updated while Blender was running — a loaded
    library cannot be swapped. Checked once."""
    global _neustart
    if _neustart is None:
        import glob, os
        kann = engine_erweiterungen()
        libdir = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                              "ffengine", "lib")
        inhalt = b""
        for pfad in glob.glob(os.path.join(libdir, "libffengine*")):
            with open(pfad, "rb") as f:
                inhalt += f.read()
        _neustart = any(not kann[k] and sym.encode() in inhalt
                        for k, _, sym in ERWEITERUNGEN)
    return _neustart


def fehlende_erweiterungen():
    """Readable names of the extensions the loaded engine lacks."""
    kann = engine_erweiterungen()
    return [name for k, name, _ in ERWEITERUNGEN if not kann[k]]


def zweites_netz(props):
    """Which second water mesh a bake with these domain settings writes:
    "grob" (Coarse Preview), "leicht" (Lighter Viewport below the Mesh
    Detail) or None. The panel compares it with the cache's."""
    if props.vorschau_im_viewport:
        return "grob" if props.vorschau_aufloesung > 0 else None
    if (props.viewport_eigenes_netz
            and props.unterteilung_viewport < max(1, props.oberflaechen_unterteilung)):
        return "leicht"
    return None


def _sim_aufbauen(scene, domain, bericht=None):
    """FluidSimulation from the scene. Returns (sim, info, halter).

    halter keeps the MeshObject instances alive — if it is released,
    Python cleans them up and the engine reaches into nothing.
    """
    from ..ffengine import FluidSimulation, MeshObject

    props = domain.hydrokit
    masse = geometrie.gitter_masse(domain, props.aufloesung)
    if masse is None:
        return None, None, None
    i, j, k, dx = masse
    versatz, groesse = geometrie.domain_bereich(domain)

    sim = FluidSimulation(i, j, k, dx)
    # GPU: only if asked for and a GPU is there; any GPU problem during the
    # bake falls back to the CPU frame by frame (Backer.gpu_bericht)
    gpu_stufen = []
    for key, _, eigenschaft, _, _ in STUFEN:
        if hat_gpu(key):
            an = geraet(props, key) == 'GPU' and gpu_info()[0]
            setattr(sim, eigenschaft, an)
            if an:
                gpu_stufen.append(key)
    objekte = geometrie.sammle_objekte(scene)
    halter = []

    verdickt = []

    def mesh_objekt(obj, mindestdicke=0.0, umkehren=False):
        tm = geometrie.als_trianglemesh(obj, versatz, mindestdicke=mindestdicke)
        if tm is None:
            return None
        if getattr(tm, "hk_verdickt", False):
            verdickt.append(obj.name)
        mo = MeshObject(i, j, k, dx)
        mo.inverse = umkehren           # before the mesh, as FLIP Fluids does
        mo.update_mesh_static(tm)
        halter.append(mo)
        return mo

    # Switched-off objects keep their role but stay out of the bake
    for rolle in ('OBSTACLE', 'FLUID', 'INFLOW', 'OUTFLOW'):
        objekte[rolle] = [o for o in objekte[rolle] if o.hydrokit.rolle_aktiv]

    # Open obstacles (a floor plane, a terrain sheet) get 3 cells of thickness —
    # the same minimum the scene doctor asks for. Not inverted ones: those must
    # be closed containers anyway.
    for obj in objekte['OBSTACLE']:
        h = obj.hydrokit
        mo = mesh_objekt(obj, mindestdicke=0.0 if h.hindernis_umkehren else 3.0 * dx,
                         umkehren=h.hindernis_umkehren)
        if mo is not None:
            mo.friction = h.hindernis_reibung
            mo.whitewater_influence = h.hindernis_gischt
            mo.sheeting_strength = h.hindernis_sheeting
            mo.mesh_expansion = h.hindernis_ausdehnung
            # In the engine mist only forms near obstacles that allow it
            # (isDustEmissionEnabled). That fits a waterfall well: spray mist
            # forms where the water hits — not evenly throughout the space.
            if props.gischt and props.nebel_an:
                mo.dust_emission_strength = props.nebel_staerke * h.hindernis_nebel
            sim.add_mesh_obstacle(mo)

    for obj in objekte['FLUID']:
        mo = mesh_objekt(obj)
        if mo is not None:
            v = obj.hydrokit.start_geschwindigkeit
            sim.add_mesh_fluid(mo, v[0], v[1], v[2])

    zufluesse = []
    for obj in objekte['INFLOW']:
        tm = geometrie.als_trianglemesh(obj, versatz)
        if tm is None:
            continue
        from ..ffengine import MeshFluidSource
        quelle = MeshFluidSource(i, j, k, dx)
        quelle.update_mesh_static(tm)
        quelle.enable = True
        quelle.inflow = True        # inflow/outflow are properties, not methods
        quelle.set_velocity(*obj.hydrokit.zufluss_geschwindigkeit)
        # Constrained (default): the inflow delivers its velocity times its
        # cross-section — a defined flow. Not constrained, the engine refills
        # the inflow volume every step and the water squirts out under that
        # artificial pressure (seen 29.10.2026: water shooting forward, mist
        # the whole time). With velocity 0 and constrained the water barely
        # leaves the inflow — the panel warns about that.
        quelle.enable_constrained_fluid_velocity = bool(obj.hydrokit.zufluss_erzwingen)
        quelle.substep_emissions = obj.hydrokit.zufluss_substeps
        sim.add_mesh_fluid_source(quelle)
        halter.append(quelle)
        zufluesse.append(obj.name)

    for obj in objekte['OUTFLOW']:
        tm = geometrie.als_trianglemesh(obj, versatz)
        if tm is None:
            continue
        from ..ffengine import MeshFluidSource
        h = obj.hydrokit
        quelle = MeshFluidSource(i, j, k, dx)
        quelle.outflow = True
        quelle.outflow_inverse = h.abfluss_umkehren     # before the mesh, as FLIP Fluids does
        quelle.update_mesh_static(tm)
        quelle.enable = True
        quelle.inflow = False       # False = outflow
        quelle.fluid_outflow = h.abfluss_wasser
        quelle.diffuse_outflow = h.abfluss_gischt
        quelle.enable_gradual_outflow = h.abfluss_allmaehlich
        quelle.outflow_rate = h.abfluss_rate
        sim.add_mesh_fluid_source(quelle)
        halter.append(quelle)

    g = scene.gravity if scene.use_gravity else (0.0, 0.0, -9.81)
    sim.add_body_force(g[0], g[1], g[2])

    # WIND. Acts through air drag on whitewater in the air, not on the
    # water — the engine is extended for that (see engine_patch/):
    #     relv = dp.velocity - windVelocity
    # A drop flying with the wind feels no drag; a drop at rest is
    # accelerated to wind speed.
    wind = geometrie.windvektor(scene, scene.hydrokit.wind_faktor)
    kann = engine_erweiterungen()
    if wind.length > 1e-6:
        if kann["wind"]:
            sim.set_diffuse_wind_velocity(wind.x, wind.y, wind.z)
        # Wind on the WATER SURFACE — only where there is contact with air, and
        # deliberately very weak. A water surface is much more sluggish than a
        # flying drop. The pool below stays untouched.
        if kann["wasser_wind"]:
            sim.set_surface_wind_velocity(wind.x, wind.y, wind.z)
            sim.set_surface_wind_factor(props.wasser_wind)
            sim.set_surface_wind_depth(props.wasser_wind_tiefe)

    # Water mesh detail (object_properties.oberflaechen_unterteilung). At
    # least 1: the engine refuses 0, which older files could still hold.
    render_stufe = max(1, props.oberflaechen_unterteilung)
    sim.surface_subdivision_level = render_stufe
    # The mesh in pieces like FLIP Fluids' Auto mode: detail 2 and 3 have 8
    # and 27 times the mesh cells, and in one piece a large domain runs out
    # of memory. At most NETZ_ZELLEN_PRO_STUECK cells per piece.
    zellen = i * j * k * render_stufe ** 3
    sim.num_polygonizer_slices = max(1, min(-(-zellen // NETZ_ZELLEN_PRO_STUECK),
                                            min(i, j, k) * render_stufe, render_stufe ** 3))
    sim.CFL_condition_number = props.cfl
    sim.enable_sheet_seeding = props.sheeting

    # ADAPTIVE PARTICLE SIZES (engine_patch/README.md section 9): deep water
    # merges into bigger particles, the surface stays at the standard size.
    if props.adaptiv_an and kann["adaptiv"]:
        sim.adaptive_particle_max_level = props.adaptiv_max_stufe
        sim.adaptive_particle_split_time = props.adaptiv_teilungsdauer
        sim.adaptive_particle_split_factor = props.adaptiv_teil_faktor
        sim.adaptive_particle_merge_factor = max(props.adaptiv_verschmelz_faktor,
                                                 props.adaptiv_teil_faktor)
        sim.adaptive_particle_solid_distance = props.adaptiv_hindernis_abstand
        sim.enable_adaptive_particles = True

    sim.enable_diffuse_material_output = props.gischt
    zerstaeubung = False
    nebel_grenzen = None
    if props.gischt:
        # WITHOUT THIS SWITCH NOTHING FORMS. The four enable_diffuse_* only
        # decide which types are output — emission only happens once the
        # emission itself is switched on. Measured: with everything else set
        # and emission rates at 175 there were still 0 particles.
        sim.enable_diffuse_particle_emission = True
        sim.enable_diffuse_foam = True
        sim.enable_diffuse_bubbles = True
        sim.enable_diffuse_spray = True

        # Thresholds from which whitewater forms at all. FLIP computes the
        # energy from a velocity range: E = 0.5 * v^2.
        v_min, v_max = props.gischt_tempo_min, props.gischt_tempo_max
        sim.min_diffuse_emitter_energy = 0.5 * v_min * v_min
        sim.max_diffuse_emitter_energy = 0.5 * v_max * v_max
        sim.min_diffuse_wavecrest_curvature = props.gischt_kruemmung_min
        sim.max_diffuse_wavecrest_curvature = props.gischt_kruemmung_max
        sim.min_diffuse_turbulence = props.gischt_turbulenz_min
        sim.max_diffuse_turbulence = props.gischt_turbulenz_max
        sim.diffuse_particle_wavecrest_emission_rate = props.gischt_rate_wellen
        sim.diffuse_particle_turbulence_emission_rate = props.gischt_rate_turbulenz
        sim.max_num_diffuse_particles = props.gischt_hoechstzahl

        # REGION WHERE EMISSION IS ALLOWED. Without it no whitewater forms
        # anywhere — again without an error message. FLIP takes the whole
        # domain, inset by 3.5 cells if needed, so nothing foams up at the
        # walls.
        from ..ffengine.aabb import AABB
        masse_sim = sim.get_simulation_dimensions()
        grenzen = AABB(0.0, 0.0, 0.0, masse_sim.x, masse_sim.y, masse_sim.z)
        if not props.gischt_an_waenden:
            grenzen.expand(-2.0 * 3.5 * sim.get_cell_size())
        sim.diffuse_emitter_generation_bounds = grenzen
        # GIVE ALL OF THEM THE SAME LIFETIME AT BIRTH. Otherwise the engine sets
        #     lifetime = min + energy * (max - min) + random(+-spread)
        # Then the remaining lifetime says nothing about age any more: little
        # left can mean "old" or "born short-lived". The mist rules, however,
        # need the real age — with spread, mist formed in mid free fall,
        # because newborns already counted as half used up.
        # Without spread exactly: used up = 1 - remaining life / lifetime.
        sim.min_diffuse_particle_lifetime = props.gischt_lebensdauer
        sim.max_diffuse_particle_lifetime = props.gischt_lebensdauer
        sim.diffuse_particle_lifetime_variance = 0.0
        # Mist runs on the dust channel. Its default is sinking sediment
        # (buoyancy -3.0, so it falls THREE times as fast as gravity) — exactly
        # wrong for mist. Buoyancy to 0, drag from the drop size.
        sim.enable_diffuse_dust = props.nebel_an
        if props.nebel_an:
            sim.diffuse_particle_dust_emission_rate = props.nebel_rate
            # mixing factor against the air, sinking through buoyancy
            sim.diffuse_dust_drag_coefficient = partikel.nebel_kopplung(
                props.nebel_groesse)
            sim.diffuse_dust_bouyancy_coefficient = partikel.nebel_sinken(
                props.nebel_groesse)
        # MIST MODEL in the engine: Weber breakup in the air, splashing on
        # impact. After that the particle flies on as mist (slowly sinking,
        # carried by the wind). Drop size = spray size.
        if props.nebel_an and kann["zerstaeubung"]:
            sim.set_diffuse_spray_mist_model(
                props.spray_groesse / 1000.0, props.nebel_spritz_anteil,
                props.nebel_spritz_schwelle)
            zerstaeubung = True
        # AIR PARTICLES LEAVE THE DOMAIN. The domain is only a section of the
        # world; spray and mist driven to one side by the wind really move on.
        # The engine default "bounce" piled them up at the wall into a vertical
        # wall of mist (seen 28.10.2026).
        # Ballistic: outside they keep flying instead of vanishing abruptly at
        # the edge. Only the floor (-Z) stays closed.
        # SPRAY disappears at the border. MIST flies on ballistically —
        # up to the mist domain if there is one, otherwise up to the domain.
        # Outside there is no flow grid (the engine delivers zero flow
        # there): mist only drifts with wind, drag and sinking.
        # That costs almost nothing, only moving the particles themselves.
        BALLISTISCH, ENTFERNEN = 1, 2
        seiten = [True, True, True, True, False, True]   # -X +X -Y +Y -Z +Z
        sim.diffuse_spray_active_boundary_sides = seiten
        if kann["spritzer_domain"] and geometrie.sammle_objekte(scene)['SPRAY_DOMAIN']:
            sim.diffuse_spray_limit_behaviour = BALLISTISCH
            s_mn, s_mx = geometrie.nebel_bereich(scene, domain, 'SPRAY_DOMAIN')
            a = s_mn - versatz
            g = s_mx - s_mn
            sim.set_diffuse_spray_outer_bounds(AABB(a.x, a.y, a.z, g.x, g.y, g.z))
        else:
            sim.diffuse_spray_limit_behaviour = ENTFERNEN
        sim.diffuse_dust_limit_behaviour = BALLISTISCH
        sim.diffuse_dust_active_boundary_sides = seiten
        if kann["nebel_domain"]:
            n_mn, n_mx = geometrie.nebel_bereich(scene, domain)
            a = n_mn - versatz
            g = n_mx - n_mn
            sim.set_diffuse_dust_outer_bounds(AABB(a.x, a.y, a.z, g.x, g.y, g.z))
            nebel_grenzen = [list(n_mn), list(n_mx)]
        # DROPLET PHYSICS after measured values (engine_patch/README.md): every
        # drop with its own diameter, drag after Schiller-Naumann or Atlas
        # (1973), gravity with buoyancy. Replaces the mixing drags below for
        # spray and mist — those only remain for an engine without the
        # patch. Limits: measuring ranges of the respective sources.
        if kann["tropfen"]:
            sim.set_diffuse_spray_droplet_model(
                props.spray_groesse / 1000.0, 0.125e-3, 8.5e-3)
            sim.set_diffuse_dust_droplet_model(
                props.nebel_groesse / 1000.0, 40e-6, 400e-6)
        # Spray: drag rate, engine limit 5.0
        sim.diffuse_spray_drag_coefficient = partikel.spray_widerstand(
            props.spray_groesse)
        # Bubbles and foam: how closely they follow the water (0..1); see
        # schaum_mitfuehrung in object_properties — not the drop-in-air formula
        sim.diffuse_bubble_drag_coefficient = props.blasen_mitfuehrung
        sim.diffuse_foam_advection_strength = props.schaum_mitfuehrung
    # The engine default is PLY; bobj matches TriangleMesh.from_bobj()
    sim.set_mesh_output_format_as_bobj()
    sim.enable_asynchronous_meshing = False
    sim.enable_console_output = False

    offen = list(props.offene_raender)
    if any(offen):
        sim.fluid_boundary_collisions = [not o for o in offen]

    # PREVIEW MESH for the viewport. The full surface mesh of a 5 m waterfall
    # has 167,000 vertices; creating it on every frame change costs about
    # 250 ms, and scrubbing stutters. On request the engine computes a second,
    # coarse mesh as well — the viewport shows that one, the full one is
    # rendered automatically. Lighter Viewport (a Viewport Detail below the
    # Mesh Detail) uses the same output: the engine resamples the render
    # mesh's field onto the coarser grid (ParticleMesher), so both come from
    # one bake.
    # Which second mesh the cache holds, for sim/anzeige.py
    vorschau = zweites_netz(props)
    netz = {"detail": render_stufe, "vorschau": vorschau}
    if vorschau == "grob":
        laengste = max(i, j, k) * dx
        sim.enable_preview_mesh_output = laengste / props.vorschau_aufloesung
    elif vorschau == "leicht":
        sim.enable_preview_mesh_output = dx / props.unterteilung_viewport
        netz["viewport"] = props.unterteilung_viewport

    sim.initialize()

    info = {
        "gitter": [i, j, k],
        "dx": dx,
        "versatz": [versatz.x, versatz.y, versatz.z],
        "groesse": [groesse.x, groesse.y, groesse.z],
        "aufloesung": props.aufloesung,
        "netz": netz,
        "gischt": props.gischt,
        "wind": [wind.x, wind.y, wind.z],
        "nebel": props.nebel_an and props.gischt,
        "nebel_engine": zerstaeubung,
        "erweiterungen": dict(kann),
        "gpu": {"stufen": gpu_stufen, "geraet": gpu_info()[1] if gpu_stufen else ""},
        "nebel_grenzen": nebel_grenzen,
        "groessen": {
            "nebel":  props.nebel_groesse,
            "spray":  props.spray_groesse,
            "foam":   props.foam_groesse,
            "bubble": props.bubble_groesse,
        },
        "domain": domain.name,
        "hindernisse": [o.name for o in objekte['OBSTACLE']],
        "wasser": [o.name for o in objekte['FLUID']],
        "zufluesse": zufluesse,
    }
    info["verdickt"] = verdickt
    return sim, info, halter


def cache_fehlt_text(domain):
    """Why the domain's cache path gives no folder (cache.pfad_aufloesen)."""
    if domain.hydrokit.cache_pfad.startswith("//"):
        return "Save the file first: the cache goes next to it"
    return "No cache folder set"


class Backer:
    """Bakes step by step, so Blender can breathe in between.

    One run in one go would freeze the UI. So the modal operator computes
    one frame per time step via schritt().
    """

    def __init__(self, scene):
        self.scene = scene
        self.sim = None
        self.info = None
        self._halter = None
        self.pfad = None
        self.frame = None
        self.fertig = 0
        self.gesamt = 0
        self.fehler = None
        self.start = None

    def vorbereiten(self):
        domain = geometrie.finde_domain(self.scene)
        if domain is None:
            self.fehler = "No domain in the scene"
            return False
        self.pfad = cache.pfad_aufloesen(domain.hydrokit.cache_pfad)
        if not self.pfad:
            self.fehler = cache_fehlt_text(domain)
            return False
        self.sim, self.info, self._halter = _sim_aufbauen(self.scene, domain)
        if self.sim is None:
            self.fehler = "Domain has no extent"
            return False
        cache.leeren(self.pfad)
        if engine_erweiterungen().get("gpu"):
            from ..ffengine import gpu
            gpu.reset_counters()
        self.info["frames"] = [self.scene.frame_start, self.scene.frame_end]
        cache.info_schreiben(self.pfad, self.info)
        self.frame = self.scene.frame_start
        self.gesamt = self.scene.frame_end - self.scene.frame_start + 1
        self.start = time.time()
        return True

    def schritt(self):
        """Compute one frame. True as long as there is still something to do."""
        if self.frame is None or self.frame > self.scene.frame_end:
            return False
        fps = self.scene.render.fps / self.scene.render.fps_base
        self.sim.update(1.0 / fps)
        cache.mesh_schreiben(self.pfad, self.frame, self.sim.get_surface_data())
        if self.sim.enable_preview_mesh_output:
            cache.mesh_schreiben(self.pfad, self.frame,
                                 self.sim.get_surface_preview_data(), "vorschau")
        if self.info.get("gischt"):
            # Positions, IDs and types in ONE set. The ID is the key to
            # dosing the amount later without flickering.
            try:
                cache.partikel_schreiben(
                    self.pfad, self.frame,
                    self.sim.get_diffuse_particle_position_data(),
                    self.sim.get_diffuse_particle_velocity_data(),
                    self.sim.get_diffuse_particle_lifetime_data(),
                    self.sim.get_diffuse_particle_id_data(),
                    self.sim.get_diffuse_particle_type_data())
            except Exception:
                pass
        self.fertig += 1
        self.frame += 1
        return self.frame <= self.scene.frame_end

    @property
    def dauer(self):
        return time.time() - self.start if self.start else 0.0

    def abschliessen(self):
        """Note in info.json how long the bake took (shown in the panel), and
        the End it ran to: schritt follows an End changed during the bake."""
        if self.pfad and self.info is not None and self.start:
            self.info["dauer"] = round(self.dauer, 1)
            if self.info.get("frames"):
                self.info["frames"] = [self.info["frames"][0], self.scene.frame_end]
            try:
                cache.info_schreiben(self.pfad, self.info)
            except OSError:
                pass

    def gpu_bericht(self):
        """(text, warning) about the GPU steps of this bake, or (None, False)."""
        if not engine_erweiterungen().get("gpu"):
            return None, False
        gpu_daten = (self.info or {}).get("gpu", {})
        auf_gpu = gpu_daten.get("stufen", [])
        domain = geometrie.finde_domain(self.scene)
        gewuenscht = [key for key, *_ in STUFEN
                      if domain is not None and geraet(domain.hydrokit, key) == 'GPU']
        namen = dict((e[0], e[1]) for e in STUFEN)
        if not auf_gpu:
            if gewuenscht:
                return f"everything on the CPU — no GPU: {gpu_info()[2]}", True
            return None, False
        from ..ffengine import gpu
        teile, warnung = [], False
        for key, name, _, _, schritt in STUFEN:
            if key not in auf_gpu:
                continue
            ok, zurueck = gpu.counts(schritt)
            if zurueck:
                teile.append(f"{name.lower()} {ok}x GPU, {zurueck}x CPU")
                warnung = True
            else:
                teile.append(name.lower())
        text = f"on the GPU ({gpu_daten.get('geraet')}): " + ", ".join(teile)
        if warnung:
            text += f" ({gpu.status_message()})"
        return text, warnung

    def aufraeumen(self):
        self._halter = None
        self.sim = None
        # Give the GPU memory back to the viewport and Cycles
        if engine_erweiterungen().get("gpu"):
            from ..ffengine import gpu
            gpu.release()


def backen(scene, fortschritt=None, abbruch=None):
    """Compute everything in one go — for scripts and background mode."""
    b = Backer(scene)
    if not b.vorbereiten():
        return 0, 0.0, b.fehler
    weiter = True
    while weiter:
        if abbruch is not None and abbruch():
            break
        frame = b.frame
        weiter = b.schritt()
        if fortschritt is not None:
            fortschritt(frame, b.fertig, b.gesamt)
    dauer = b.dauer
    b.abschliessen()
    b.aufraeumen()
    return b.fertig, dauer, None
