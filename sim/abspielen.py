# HyprW — Light Playback: a lighter display while the timeline plays
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Playing a big bake in the viewport ran at 0.7 frames per second while a
# paused frame was smooth. At frame 100 of the user's waterfall (Resolution
# 300, Mesh Detail 3) every frame change read 82 MB and wrote 2 million
# whitewater points and a water mesh of 1.27 million triangles into Blender,
# which then evaluated and drew all of it again. While the timeline plays,
# Light Playback (scene setting abspielen_leicht) shows lighter versions of
# HyprW's own results:
#
#   WHITEWATER  foam, bubbles and spray together about LEICHT_PUNKTE points,
#               thinned by the ID like the amount sliders, with one share
#               per bake (anteile), so the same particles stay from frame
#               to frame and nothing flickers. The mist points stay complete:
#               the mist's light look thins them itself and draws each drop
#               the larger for it (sim/aussehen.py), which needs all of them.
#               No velocity attribute: only Cycles motion blur reads it.
#   WATER       the cache's own lighter mesh if the bake wrote one (Lighter
#               Viewport, Coarse Preview), else the full one merged on a
#               grid of LEICHT_ZELLEN simulation cells (vereinfachen).
#
# Paused frames and every render show the full result, exactly as before.
# The full frame comes back when playback stops, before saving, after
# opening a file that was saved while light, after an undo or redo to a step
# taken while playing, and when the add-on goes. The scene on display is
# remembered by its session_uid, so renaming it while playing changes nothing.
# Everything runs on the main thread (bpy is not thread-safe).

import os
from collections import OrderedDict

import bpy

from . import cache, geometrie

# Foam, bubbles and spray shown while playing, together, in the largest frame
LEICHT_PUNKTE = 150_000
# Grid of the light water mesh, in simulation cells
LEICHT_ZELLEN = 1.5
# Below this many triangles the full mesh is shown: merging costs more than
# it saves
LEICHT_AB_DREIECKE = 50_000
# ID property on the water's mesh while it shows a light frame: a file
# saved like that (an autosave while playing) loads the full frame on opening
MARKE = "hk_leicht"

# True between animation_playback_pre and animation_playback_post
SPIELT = False
# This playback: the domain, looked up once
_SITZUNG = {}
# What the viewport shows: scene (name and session_uid), light or full
GEZEIGT = {}
# Shares per bake (anteile)
_ANTEILE = {}


def spielt():
    """True while the timeline plays. Blender is asked as well: a stop that
    never reached animation_playback_post counts as stopped, so a paused
    frame is never the light one."""
    if not SPIELT:
        return False
    if bpy.app.background:
        return True         # tests and measurements call the hooks directly
    try:
        return any(w.screen is not None and w.screen.is_animation_playing
                   for w in bpy.context.window_manager.windows)
    except (AttributeError, ReferenceError, RuntimeError):
        return True


def leicht(scene):
    """Show this frame light: playing, Light Playback on, not rendering."""
    from . import anzeige
    if not SPIELT or anzeige._rendert:
        return False
    if not getattr(getattr(scene, "hydrokit", None), "abspielen_leicht", True):
        return False
    return spielt()


def domain(scene):
    """The domain, looked up once per playback: finde_domain walks every
    object of the scene on every frame."""
    name = _SITZUNG.get("domain")
    if name is not None and _SITZUNG.get("szene") == scene.name:
        obj = scene.objects.get(name)
        if obj is not None and getattr(getattr(obj, "hydrokit", None), "typ", 'NONE') == 'DOMAIN':
            return obj
    obj = geometrie.finde_domain(scene)
    if obj is not None:
        _SITZUNG.update(szene=scene.name, domain=obj.name)
    return obj


def gezeigt(scene, ist_leicht):
    """Note what the viewport shows now, and mark the water's mesh while it
    is a light frame. scene None: nothing shown any more (Clear Cache)."""
    if scene is None:
        GEZEIGT.clear()
    else:
        GEZEIGT.update(szene=scene.name, uid=scene.session_uid, leicht=bool(ist_leicht))
    from . import anzeige
    obj = bpy.data.objects.get(anzeige.OBERFLAECHE)
    me = obj.data if obj is not None and obj.type == 'MESH' else None
    if me is None:
        return
    if ist_leicht and scene is not None:
        if not me.get(MARKE):
            me[MARKE] = True
    elif MARKE in me:
        del me[MARKE]


def szene_gezeigt():
    """The scene whose frame is on display. By its session_uid: found by
    name, a scene renamed while playing kept its light frame after stopping
    and saving."""
    uid, name = GEZEIGT.get("uid"), GEZEIGT.get("szene")
    if uid is not None:
        for sc in bpy.data.scenes:
            if sc.session_uid == uid:
                return sc
    return bpy.data.scenes.get(name) if name else None


def voll_zeigen(scene=None):
    """The full frame again if a light one is on display. True if loaded."""
    from . import anzeige
    if scene is None:
        scene = szene_gezeigt()
    if scene is None or not GEZEIGT.get("leicht"):
        return False
    anzeige.frame_laden(scene, leicht=False)
    return True


# ------------------------------------------------------------ whitewater
def anteile(pfad, dp):
    """Share per whitewater type (bubble, foam, spray, mist) on top of the
    amount sliders while playing. One set per bake, so the thinning does not
    change from frame to frame: from the largest particle file, by size.
    LEICHT_PUNKTE is shared out over foam, bubbles and spray, the smallest
    first and kept whole if it fits: a few thousand spray drops stay
    complete, foam and bubbles are thinned. Mist 1 (see the top)."""
    import numpy as np
    from .anzeige import ARTEN
    mengen = tuple(float(getattr(dp, ARTEN[a][2], 1.0)) if dp else 1.0 for a in range(4))
    ordner = os.path.join(pfad, cache.FRAMES)
    try:
        marke = os.stat(ordner).st_mtime_ns     # changes when frames are added or removed
    except OSError:
        marke = None
    schluessel = (pfad, marke, mengen)
    if _ANTEILE.get("schluessel") == schluessel:
        return _ANTEILE["anteile"]
    groesste, datei = 0, None
    try:
        with os.scandir(ordner) as eintraege:
            for e in eintraege:
                if e.name.endswith(cache.PARTIKEL_ENDUNG):
                    g = e.stat().st_size
                    if g > groesste:
                        groesste, datei = g, e.path
    except OSError:
        pass
    zahl = [0.0] * 4
    if datei is not None:
        try:
            with open(datei, "rb") as f:
                kopf = f.read(8)
                n = int(np.frombuffer(kopf, np.int32, 1, 4)[0]) if len(kopf) == 8 else 0
                if n > 0 and groesste >= 8 + 30 * n:
                    f.seek(8 + 29 * n)
                    arten = np.frombuffer(f.read(n), np.uint8)
                    zahl = [float(z) for z in np.bincount(arten, minlength=4)[:4]]
        except (OSError, ValueError):
            pass
    erg = [1.0, 1.0, 1.0, 1.0]
    rest = float(LEICHT_PUNKTE)
    typen = sorted((0, 1, 2), key=lambda a: zahl[a] * mengen[a])
    for i, a in enumerate(typen):
        gezeigt_a = zahl[a] * mengen[a]
        teil = min(gezeigt_a, rest / (len(typen) - i))
        if gezeigt_a > 0.0:
            erg[a] = teil / gezeigt_a
        rest -= teil
    erg = tuple(erg)
    _ANTEILE.clear()
    _ANTEILE.update(schluessel=schluessel, anteile=erg)
    return erg


# ------------------------------------------------------------------ water
# Light water meshes already merged, for the next loop of the playback:
# (path, frame, file stamp, cell) -> arrays, least recently used first
_FLAECHEN = OrderedDict()
_FLAECHEN_BYTES = [0]
FLAECHEN_HOECHST = 256 << 20


def flaeche_leicht(pfad, frame, info):
    """(vertices, triangles) of the light water, or None if the frame has
    no mesh: the cache's lighter mesh if the bake wrote one, else the full
    one, merged when it is large. Merged meshes are kept (up to
    FLAECHEN_HOECHST bytes): playback loops, and the second time round the
    frame costs only Blender's part."""
    netz = info.get("netz")
    # caches from before 02.11.2026 only ever held the coarse preview
    if netz is None or netz.get("vorschau"):
        daten = cache.mesh_lesen_np(pfad, frame, "vorschau")
        if daten is not None:
            return daten
    dx = info.get("dx")
    if not dx:
        gitter, groesse = info.get("gitter"), info.get("groesse")
        dx = max(groesse) / max(gitter) if gitter and groesse else 0.0
    zelle = LEICHT_ZELLEN * float(dx)
    datei = cache._datei(pfad, frame)
    try:
        st = os.stat(datei)
    except OSError:
        return None
    schluessel = (datei, st.st_mtime_ns, st.st_size, zelle, LEICHT_AB_DREIECKE)
    daten = _FLAECHEN.get(schluessel)
    if daten is not None:
        _FLAECHEN.move_to_end(schluessel)
        return daten
    daten = cache.mesh_lesen_np(pfad, frame)
    if daten is None or len(daten[1]) // 3 <= LEICHT_AB_DREIECKE:
        return daten
    daten = vereinfachen(daten[0], daten[1], zelle)
    _FLAECHEN[schluessel] = daten
    _FLAECHEN_BYTES[0] += daten[0].nbytes + daten[1].nbytes
    while _FLAECHEN_BYTES[0] > FLAECHEN_HOECHST and _FLAECHEN:
        _, weg = _FLAECHEN.popitem(last=False)
        _FLAECHEN_BYTES[0] -= weg[0].nbytes + weg[1].nbytes
    return daten


def vereinfachen(ecken, dreiecke, zelle):
    """A lighter copy of the water mesh (flat arrays as in the cache) by
    vertex clustering (Rossignac & Borrel 1993): the vertices in one cell of
    a grid fixed to the cache's origin merge into their mean; triangles whose
    corners land in fewer than three cells vanish, and so do doubles. The
    grid does not move between frames, so calm water does not shimmer.

    Measured in Blender on the user's waterfall (Resolution 300, Mesh Detail
    3) at 1.5 cells: frame 60, 418,000 -> 18,200 triangles, 16 ms; frame 100,
    1,273,000 -> 53,500 triangles, 43 ms. Blender then takes the light mesh
    in 3 to 6 ms instead of 55 to 169 ms."""
    import numpy as np
    nv, nt = len(ecken) // 3, len(dreiecke) // 3
    if not nv or not nt or not zelle > 0.0:
        return ecken, dreiecke
    inv = np.float32(1.0 / zelle)
    # The cell per axis from the strided columns: reducing an n x 3 array
    # along axis 0 took 22 ms here
    achsen = []
    for k in range(3):
        q = np.floor(ecken[k::3] * inv).astype(np.int32)
        q -= q.min()
        achsen.append(q)
    w1, w2 = int(achsen[1].max()) + 1, int(achsen[2].max()) + 1
    zellen = (int(achsen[0].max()) + 1) * w1 * w2
    typ = np.int32 if zellen < 2**31 else np.int64
    nummer_zelle = achsen[0].astype(typ) * (w1 * w2) + achsen[1].astype(typ) * w2 + achsen[2]
    if zellen <= 16_000_000:
        belegt = np.zeros(zellen, np.int32)
        belegt[nummer_zelle] = 1
        nummer = np.cumsum(belegt, dtype=np.int32)
        m = int(nummer[-1])
        nummer -= 1
        gruppe = nummer[nummer_zelle]
    else:
        _, gruppe = np.unique(nummer_zelle, return_inverse=True)
        gruppe = gruppe.astype(np.int32).ravel()
        m = int(gruppe.max()) + 1
    zahl = np.bincount(gruppe, minlength=m)
    mitte = np.empty((m, 3), np.float32)
    for k in range(3):
        mitte[:, k] = np.bincount(gruppe, weights=ecken[k::3], minlength=m) / zahl
    a, b, c = gruppe[dreiecke[0::3]], gruppe[dreiecke[1::3]], gruppe[dreiecke[2::3]]
    ok = (a != b) & (b != c) & (c != a)
    a, b, c = a[ok], b[ok], c[ok]
    if len(a) and m < 1_500_000:
        # Several fine triangles between the same three cells once. The key:
        # the sorted corners and whether the original order is a rotation of
        # them (the winding); of two opposite ones the first stays
        lo = np.minimum(np.minimum(a, b), c).astype(np.int64)
        hi = np.maximum(np.maximum(a, b), c).astype(np.int64)
        mi = a.astype(np.int64) + b + c - lo - hi
        gerade = ((a < b) & (b < c)) | ((b < c) & (c < a)) | ((c < a) & (a < b))
        schluessel = np.unique(((lo * m + mi) * m + hi) * 2 + gerade)
        g = (schluessel & 1).astype(bool)
        schluessel >>= 1
        einmal = np.ones(len(schluessel), bool)
        einmal[1:] = schluessel[1:] != schluessel[:-1]
        schluessel, g = schluessel[einmal], g[einmal]
        hi = (schluessel % m).astype(np.int32)
        schluessel //= m
        mi = (schluessel % m).astype(np.int32)
        a = (schluessel // m).astype(np.int32)
        b, c = np.where(g, mi, hi), np.where(g, hi, mi)
    tt = np.empty((len(a), 3), np.int32)
    tt[:, 0], tt[:, 1], tt[:, 2] = a, b, c
    benutzt = np.zeros(m, np.bool_)
    benutzt[tt.ravel()] = True
    nummer = np.cumsum(benutzt, dtype=np.int32) - 1
    return np.ascontiguousarray(mitte[benutzt]).ravel(), nummer[tt].ravel()


# ------------------------------------------------------------------ hooks
@bpy.app.handlers.persistent
def _abspielen_start(scene, depsgraph=None):
    """Playback starts: light frames from the next one on. Rays of helper
    objects once here instead of on every frame."""
    global SPIELT
    SPIELT = True
    _SITZUNG.clear()
    try:
        geometrie.hilfsobjekte_unsichtbar(scene)
    except Exception as e:
        print(f"HyprW: Light Playback start ({e})")


@bpy.app.handlers.persistent
def _abspielen_ende(scene, depsgraph=None):
    """Playback stopped: the frame on display in full quality at once."""
    global SPIELT
    SPIELT = False
    _SITZUNG.clear()
    try:
        voll_zeigen()       # the scene on display, which need not be the one that played
    except Exception as e:
        print(f"HyprW: full frame after playback ({e})")


@bpy.app.handlers.persistent
def _abspielen_vor_speichern(*args):
    """A saved file never holds a light frame, also when saved while
    playing: the next frame played is light again."""
    try:
        voll_zeigen()
    except Exception as e:
        print(f"HyprW: full frame before saving ({e})")


@bpy.app.handlers.persistent
def _abspielen_vor_laden(*args):
    """Another file: nothing of this playback stays."""
    global SPIELT
    SPIELT = False
    _SITZUNG.clear()
    GEZEIGT.clear()
    _FLAECHEN.clear()
    _FLAECHEN_BYTES[0] = 0


@bpy.app.handlers.persistent
def _abspielen_nach_laden(*args):
    """A file written while a light frame was on display (an autosave while
    playing) opens with the full frame."""
    try:
        from . import anzeige
        obj = bpy.data.objects.get(anzeige.OBERFLAECHE)
        if obj is not None and obj.type == 'MESH' and obj.data.get(MARKE):
            del obj.data[MARKE]
            anzeige.frame_laden(bpy.context.scene, leicht=False)
    except Exception as e:
        print(f"HyprW: full frame after loading ({e})")


@bpy.app.handlers.persistent
def _abspielen_nach_undo(*args):
    """An undo or redo brings the meshes back as they were when its step
    was taken; a step taken while playing (a setting clicked) holds the
    light water and the thinned whitewater. GEZEIGT is not undone: after
    playback it said full, so the light frame stayed on display while paused
    and a save wrote it. The marker comes back with the water's mesh and
    says what is shown."""
    try:
        from . import anzeige
        scene = bpy.context.scene
        if scene is None:
            return
        obj = bpy.data.objects.get(anzeige.OBERFLAECHE)
        markiert = bool(obj is not None and obj.type == 'MESH' and obj.data.get(MARKE))
        GEZEIGT.update(szene=scene.name, uid=scene.session_uid, leicht=markiert)
        if markiert and not spielt():
            anzeige.frame_laden(scene, leicht=False)
    except Exception as e:
        print(f"HyprW: full frame after undo ({e})")


_HANDLER = (
    ("animation_playback_pre",  _abspielen_start),
    ("animation_playback_post", _abspielen_ende),
    ("save_pre",                _abspielen_vor_speichern),
    ("load_pre",                _abspielen_vor_laden),
    ("load_post",               _abspielen_nach_laden),
    ("undo_post",               _abspielen_nach_undo),
    ("redo_post",               _abspielen_nach_undo),
)


def an():
    """Hooks on, with the add-on (sim/anzeige.py, lade_handler_an). Touches
    no data: register() runs before bpy.data can be read."""
    aus_ohne_zeigen()
    for liste, fn in _HANDLER:
        getattr(bpy.app.handlers, liste).append(fn)


def aus_ohne_zeigen():
    for liste, fn in _HANDLER:
        L = getattr(bpy.app.handlers, liste)
        for h in list(L):
            if getattr(h, "__name__", "") == fn.__name__:
                L.remove(h)


def aus():
    """Hooks off and the full frame back: switched off while playing, the
    light frame would stay until the next frame change."""
    global SPIELT
    aus_ohne_zeigen()
    SPIELT = False
    _SITZUNG.clear()
    try:
        voll_zeigen()
    except Exception as e:
        print(f"HyprW: full frame when switching off ({e})")
    GEZEIGT.clear()
    _FLAECHEN.clear()
    _FLAECHEN_BYTES[0] = 0
