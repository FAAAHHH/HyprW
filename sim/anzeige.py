# HyprW — showing baked frames in Blender
# SPDX-License-Identifier: GPL-3.0-or-later
#
# One object per output type whose mesh is reloaded from the cache on every
# frame change. A handler on frame_change_post keeps it current;
# render_pre covers rendering an image sequence, where frame_change_post does
# not always fire.

import bpy
from mathutils import Vector

from . import cache, geometrie

OBERFLAECHE = "HK_Oberflaeche"

# Number from the engine (diffuseparticle.h) -> name, object name,
# property for the amount
ARTEN = {
    0: ("bubble", "HK_Blasen", "menge_bubble"),
    1: ("foam",   "HK_Schaum", "menge_foam"),
    2: ("spray",  "HK_Spray",  "menge_spray"),
    3: ("nebel",  "HK_Nebel",  "menge_nebel"),
}


# Objects that only show the bake. Click-through in the viewport: the mist
# volume covers the whole scene, and every click inside the domain picked it
# (then water, foam, spray) before the object the user meant.
ERGEBNISSE = (OBERFLAECHE,) + tuple(a[1] for a in ARTEN.values()) + ("HK_Linsentropfen",)


def waehlbar_machen(obj, scene):
    """Result objects selectable only with Selectable Results switched on."""
    soll = not getattr(scene.hydrokit, "ergebnisse_waehlbar", False)
    if obj.hide_select != soll:
        obj.hide_select = soll


def alle_waehlbar_machen(scene):
    for name in ERGEBNISSE:
        obj = bpy.data.objects.get(name)
        if obj is not None:
            waehlbar_machen(obj, scene)


def _objekt_holen(name, scene):
    obj = bpy.data.objects.get(name)
    if obj is None:
        me = bpy.data.meshes.new(name)
        obj = bpy.data.objects.new(name, me)
    if obj.name not in scene.collection.all_objects:
        scene.collection.objects.link(obj)
    waehlbar_machen(obj, scene)
    return obj


# SPEED: everything here goes through NumPy and foreach_set. The first
# version built meshes from Python lists and from_pydata — measured 2,470 ms
# per frame for a single surface with 167,000 vertices. Blender stuttered
# badly while scrubbing. foreach_set writes the same data directly into
# Blender's memory.
#
# FASTER STILL: Blender's mesh attributes ("position", ".corner_vert",
# "sharp_face") take whole arrays directly. The RNA collections
# (vertices.co, loops.vertex_index, polygons.use_smooth) go through RNA per
# element — measured on 170,000 vertices / 743,000 corners: 73 + 147 + 71 ms
# against 0.4 + 0.7 + 0.3 ms. The RNA path stays as a fallback.


def netz_fuellen(me, co, ecken, start, glatt=None):
    """Write points (flat float32 x,y,z), corner vertex indices and face start
    indices into an empty mesh. glatt: per face True = smooth shaded, or None
    to leave the shading as it is."""
    import numpy as np
    nv, nl, nf = len(co) // 3, len(ecken), len(start)
    me.vertices.add(nv)
    pos = me.attributes.get("position")
    if pos is not None:
        pos.data.foreach_set("vector", co)
    else:
        me.vertices.foreach_set("co", co)
    me.loops.add(nl)
    ecke = me.attributes.get(".corner_vert")
    if ecke is not None:
        ecke.data.foreach_set("value", ecken)
    else:
        me.loops.foreach_set("vertex_index", ecken)
    me.polygons.add(nf)
    me.polygons.foreach_set("loop_start", start)
    if glatt is not None:
        try:
            scharf = me.attributes.get("sharp_face") or me.attributes.new(
                "sharp_face", 'BOOLEAN', 'FACE')
            scharf.data.foreach_set("value", ~np.asarray(glatt, bool))
        except (RuntimeError, TypeError, AttributeError):
            me.polygons.foreach_set("use_smooth", np.asarray(glatt, bool))
    me.update(calc_edges=True)


def _mesh_setzen(obj, daten, versatz):
    """Write the surface from (vertices, triangles) into the object.

    The engine computes with origin (0,0,0); the offset brings the result
    back to where the domain stands in the scene.
    """
    import numpy as np
    me = obj.data
    me.clear_geometry()
    if daten is None:
        me.update()
        return 0
    ecken, dreiecke = daten
    nv, nt = len(ecken) // 3, len(dreiecke) // 3
    if nv == 0 or nt == 0:
        me.update()
        return 0
    co = (ecken.reshape(-1, 3) + np.asarray(versatz, np.float32)).ravel()
    # Without smoothing the water shows every single triangle and looks
    # faceted — the surface comes out of marching cubes as a triangle mesh.
    netz_fuellen(me, co.astype(np.float32, copy=False),
                 np.asarray(dreiecke, np.int32),
                 np.arange(0, nt * 3, 3, dtype=np.int32), np.ones(nt, dtype=bool))
    return nv


# DRAWN SIZE PER PARTICLE. All particles of a type at exactly one radius
# looked like hail. The spread per type, as a factor on the drawn radius
# (mean about 1), looked up by the engine ID so it stays with the particle:
#
#   spray    the engine's own drop diameters: Gamma(2, d50/2) per ID
#            (engine_patch/README.md, section 6; Lian et al. 2019), factor
#            d / d50 — so a drawn drop is as large as the drop that flies.
#   bubbles  the bubble size spectrum under breaking waves (Deane & Stokes
#            2002, Nature 418:839): number ~ a^-3/2 below the Hinze scale,
#            ~ a^-10/3 above it. The drawn radius stands for the Hinze scale;
#            the range 0.3 .. 3 times it is an APPROXIMATION.
#   foam     no measured size distribution of foam clumps was found:
#            uniform 0.7 .. 1.3, an APPROXIMATION against the uniform look.
def _groessen_tabellen(spray_d50_mm):
    import numpy as np
    u = (np.arange(256) + 0.5) / 256.0
    from .linse import _gamma_durchmesser
    d50 = spray_d50_mm / 1000.0
    spray = _gamma_durchmesser(d50, 0.125e-3, 8.5e-3, np.arange(256)) / d50
    # bubbles: quantile of the two-part power law on 0.3 .. 3 (Hinze scale = 1)
    a = np.linspace(0.3, 3.0, 4000)
    dichte = np.where(a < 1.0, a ** -1.5, a ** (-10.0 / 3.0))
    F = np.cumsum(dichte); F = (F - F[0]) / (F[-1] - F[0])
    blasen = np.interp(u, F, a)
    blasen /= blasen.mean()
    schaum = 0.7 + 0.6 * u
    return {0: blasen, 1: schaum, 2: spray, 3: np.ones(256)}


# The amount sliders thin out by ID. The spray size also hangs on the ID, so
# thinning by the raw ID would remove the smallest drops first; a fixed
# permutation (167 is odd, so a bijection on 0..255) decouples the two.
def _ausduennen_kennung(kennungen):
    import numpy as np
    return (kennungen.astype(np.int32) * 167) % 256


def _punkte_setzen(obj, punkte, zufall=None, tempo=None, groesse=None):
    """Point cloud (NumPy n x 3) as a mesh without edges and faces.

    zufall: per point 0..1 from the engine ID — it stays with the particle for
    its whole life, so the material variation does not flicker.
    tempo: velocity per point in m/s, as the attribute "velocity" that Cycles
    uses for motion blur (a real shutter turns fast drops into streaks).
    groesse: factor on the drawn radius per point ("hk_groesse").
    """
    import numpy as np
    me = obj.data
    me.clear_geometry()
    n = len(punkte)
    if n:
        me.vertices.add(n)
        pos = me.attributes.get("position")
        werte = np.ascontiguousarray(punkte, np.float32).ravel()
        if pos is not None:
            pos.data.foreach_set("vector", werte)
        else:
            me.vertices.foreach_set("co", werte)
        if zufall is not None:
            attr = me.attributes.get("hk_zufall")
            if attr is None:
                attr = me.attributes.new("hk_zufall", 'FLOAT', 'POINT')
            attr.data.foreach_set("value", zufall)
        for name, typ, werte in (("velocity", 'FLOAT_VECTOR', tempo),
                                 ("hk_groesse", 'FLOAT', groesse)):
            if werte is None:
                continue
            attr = me.attributes.get(name)
            if attr is None:
                attr = me.attributes.new(name, typ, 'POINT')
            attr.data.foreach_set("vector" if typ == 'FLOAT_VECTOR' else "value",
                                  np.ascontiguousarray(werte, np.float32).ravel())
    me.update()
    return n


def gischt_laden(scene, pfad, versatz, frame, nebel_engine=False):
    """Split particles by type and show the configured amount.

    Filtering uses the ID (one byte per particle), not the index: the ID
    stays with the particle for its lifetime, so at 50 % the same half stays
    visible while scrubbing. A filter by index would catch different particles
    on every frame and flicker visibly.
    """
    import numpy as np
    daten = cache.partikel_lesen(pfad, frame)
    if daten is None:
        return {}
    roh_pos, roh_vel, roh_leben, roh_kenn, roh_arten = daten
    n = len(roh_kenn)
    gefunden = {}
    leer = np.zeros((0, 3), np.float32)
    if n == 0:
        for _, name, _ in ARTEN.values():
            if bpy.data.objects.get(name) is not None:
                gefunden[name] = _punkte_setzen(_objekt_holen(name, scene), leer)
        return gefunden

    positionen = (np.frombuffer(roh_pos, np.float32).reshape(-1, 3)
                  + np.asarray(versatz, np.float32))
    tempo = np.frombuffer(roh_vel, np.float32).reshape(-1, 3)
    leben = np.frombuffer(roh_leben, np.float32)
    kennungen = np.frombuffer(roh_kenn, np.uint8)
    arten = np.frombuffer(roh_arten, np.uint8).copy()
    domain = geometrie.finde_domain(scene)
    dp = domain.hydrokit if domain else None

    # MIST FROM SPRAY — three ways it forms, all readable from the data:
    #
    #   1. HARD IMPACT: only FAST spray comes from a real impact. A fixed
    #      share of it atomises into mist at once (size distribution; the
    #      engine knows only one drop size).
    #   2. LONG FREE FALL: the more of its lifetime a drop has spent in the
    #      air, the more likely it has atomised. The transition is
    #      STAGGERED: from the threshold on, a growing share becomes mist,
    #      selected by ID. A hard cut would flip all drops born at the same
    #      time at the same moment — a sudden cloud in mid-fall.
    #   3. SLOWED DOWN AFTER FLIGHT: a drop that has reached air speed after
    #      a while floats. Freshly born slow drops do NOT count — they form
    #      at a calm edge.
    #
    # EARLIER BUG, measured: the age rule was "remaining life < 0.8 x
    # maximum life". Because drops are born with 0.5 to 1.5 x lifetime,
    # about 70 % were already "old" AT BIRTH. Result: a cloud after a few
    # metres of fall. And a higher value made it worse instead of better.
    #
    # The engine does NOT deliver mist on its dust channel (measured in
    # eight configurations, always zero); this way it is in the data.
    tempo_schwelle = getattr(dp, "nebel_tempo", 0.6) if dp else 0.6
    langsam2 = tempo_schwelle * tempo_schwelle
    ab_alter = getattr(dp, "nebel_ab_alter", 0.8) if dp else 0.8
    ab_alter = min(max(ab_alter, 0.0), 0.99)
    mittel_leben = getattr(dp, "gischt_lebensdauer", 2.5) if dp else 2.5
    fein = getattr(dp, "nebel_feinanteil", 0.05) if dp else 0.05
    aufprall = getattr(dp, "nebel_aufprall_tempo", 3.0) if dp else 3.0
    aufprall2 = aufprall * aufprall

    v2 = (tempo * tempo).sum(axis=1)
    zufall = kennungen.astype(np.float32) / 255.0
    # Since all drops are born with the same lifetime, this is exactly the
    # used-up share of their lifetime — 0 at birth.
    verbraucht = np.clip(1.0 - leben / max(mittel_leben, 1e-6), 0.0, 1.0)
    spray = arten == 2

    # 1. Impact: already fast at birth (otherwise mist in mid-fall,
    #    measured 93 particles at z=+0.44), a fixed fine share of it
    aufschlag = (v2 > aufprall2) & (verbraucht < 0.25) & (zufall < fein)
    # 2. Long fall, staggered by ID — no sudden cloud
    langer_fall = ((verbraucht >= ab_alter)
                   & ((verbraucht - ab_alter) / (1.0 - ab_alter) > zufall))
    # 3. Slowed down, but only after a while in the air. With a spread birth
    #    lifetime, newborns already counted as half used up
    #    (measured: 71 of 107 mist particles at frame 45 came from that).
    abgebremst = (v2 < langsam2) & (verbraucht > 0.35)
    # If the engine has already atomised, mist is a type of its own in the
    # data and flies physically right. Then recolour nothing.
    if not nebel_engine:
        arten[spray & (aufschlag | langer_fall | abgebremst)] = 3

    tabellen = _groessen_tabellen(getattr(dp, "spray_groesse", 3.0) if dp else 3.0)
    duenn = _ausduennen_kennung(kennungen)
    for a, (artname, objname, mengenname) in ARTEN.items():
        maske = arten == a
        anteil = getattr(dp, mengenname, 1.0) if dp else 1.0
        if anteil < 1.0:
            maske &= duenn < anteil * 255.0
        if maske.any() or bpy.data.objects.get(objname) is not None:
            o = _objekt_holen(objname, scene)
            gefunden[objname] = _punkte_setzen(o, positionen[maske], zufall[maske],
                                               tempo[maske], tabellen[a][kennungen[maske]])
    return gefunden


def frame_laden(scene, frame=None):
    """Fetch surface and whitewater of the frame from the cache."""
    geometrie.hilfsobjekte_unsichtbar(scene)
    domain = geometrie.finde_domain(scene)
    if domain is None:
        return None
    pfad = cache.pfad_aufloesen(domain.hydrokit.cache_pfad)
    info = cache.info_lesen(pfad)
    if info is None:
        return None
    versatz = info.get("versatz", [0.0, 0.0, 0.0])
    if frame is None:
        frame = scene.frame_current

    ergebnis = {}
    obj = _objekt_holen(OBERFLAECHE, scene)
    # The coarse preview mesh in the viewport, the full one when rendering.
    daten = None
    if not _rendert and getattr(domain.hydrokit, "vorschau_im_viewport", False):
        daten = cache.mesh_lesen_np(pfad, frame, "vorschau")
    if daten is None:
        daten = cache.mesh_lesen_np(pfad, frame)
    ergebnis[OBERFLAECHE] = _mesh_setzen(obj, daten, versatz)

    if info.get("gischt"):
        ergebnis.update(gischt_laden(scene, pfad, versatz, frame,
                                     info.get("nebel_engine", False)))
    from . import linse
    ergebnis["HK_Linsentropfen"] = linse.anzeigen(scene, frame)
    return ergebnis


# During a render job the full mesh is loaded, otherwise the preview.
_rendert = False


@bpy.app.handlers.persistent
def _bei_frame(scene, depsgraph=None):
    try:
        frame_laden(scene)
    except Exception:
        pass        # a display error must never lock up Blender


@bpy.app.handlers.persistent
def _render_beginnt(scene, depsgraph=None):
    global _rendert
    _rendert = True


@bpy.app.handlers.persistent
def _render_endet(scene, depsgraph=None):
    global _rendert
    _rendert = False
    try:
        frame_laden(scene)      # back to the light preview
    except Exception:
        pass


_HANDLER = (
    ("render_init",       _render_beginnt),
    ("frame_change_post", _bei_frame),
    ("render_pre",        _bei_frame),
    ("render_complete",   _render_endet),
    ("render_cancel",     _render_endet),
)


def handler_an():
    handler_aus()
    for liste, fn in _HANDLER:
        getattr(bpy.app.handlers, liste).append(fn)


def handler_aus():
    namen = {fn.__name__ for _, fn in _HANDLER}
    for liste, _ in _HANDLER:
        L = getattr(bpy.app.handlers, liste)
        for h in list(L):
            if getattr(h, "__name__", "") in namen:
                L.remove(h)


def handler_laeuft():
    return any(getattr(h, "__name__", "") == "_bei_frame"
               for h in bpy.app.handlers.frame_change_post)
