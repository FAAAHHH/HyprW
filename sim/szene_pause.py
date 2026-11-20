# HyprW — Pause Other Animation: heavy animation holds still while playing
# SPDX-License-Identifier: GPL-3.0-or-later
#
# While the timeline plays, the heavy animated objects that are not HyprW's
# keep the pose of the frame where playback started, so a frame change
# mostly has to load the water. Stopped, in every render and in every saved
# file everything moves as before. Off by default
# (scene.hydrokit.abspielen_andere_pausieren).
#
# SPEED, measured 05.11.2026 on a user's scene (Blender 5.2 headless,
# i7-11850H, median frame change over frames 60-65): seven botaniq trees
# with wind, 2.47 M vertices with eleven modifiers each (Simple Deform x5,
# Displace x2, UV Warp x2, two Geometry Nodes masks), driven by twelve
# animated wind helpers. HyprW's display handler off: 556 ms per frame
# change, 1 ms paused. Handler on: 1149 -> 608 ms. In the viewport the
# trees' geometry no longer changes, so it is not uploaded to the GPU again
# on every frame either (not measurable without a display).
# 06.11.2026, the same scene saved again with one tree as a HyprW obstacle
# (it and the twin that shares its wind helpers keep moving, 15 of the 19
# animated data-blocks paused), handler on with a Resolution 300 cache
# (-t 4, while a bake ran): frames 61-66 1081 -> 776 ms, frames 96-101
# 1394 -> 1056 ms; the animation step alone 550 -> 245 ms.
#
# Half of the trees' time is botaniq's two masks (Geometry Nodes, first in
# the stack): the wind modifiers after them change every frame, so the
# whole stack runs again. Masks off in the viewport: 562 -> 272 ms.
#
# HOW: every paused data-block keeps its action; only the action slot is
# unassigned (AnimData.action_slot = None) and set back later by its
# handle. Measured alternatives on the same scene:
#   action = None      also 1 ms, but the action falls to 0 users, and an
#                      action without a fake user is lost when saved so
#   F-Curves muted     555 ms: Blender re-evaluates the trees anyway
#   modifiers off      1 ms, but shows the undeformed tree
#   trees hidden       no help while their wind helpers show (below), and
#                      the trees vanish
#
# WHY HIDING THE TREES IS NOT ENOUGH: botaniq parents its wind helpers
# (bq_Circle_*, bq_Empty-Sphere_*) to the tree. A visible child pulls its
# parent into the depsgraph as visible, so a hidden tree still evaluates
# its whole modifier stack: the 18 helpers alone, every tree hidden, cost
# 451 ms per frame; with their parent cleared 1 ms.
#
# SAFETY: before a slot is touched, what is paused is written into the
# scene (STAND), so an undo step, an autosave or a crash file carries the
# record to set it back from. Set back on stopping, before saving (paused
# again after it), around loading a file, after undo and redo, before a
# render (paused again after it), on quit and on unregister, and by a watchdog should Blender ever
# end playback without its stop handler. Never touched: linked data,
# library overrides, cameras and lights, HyprW's objects and the camera and
# whatever their transforms and modifiers follow.

import json
import statistics
import threading
import time

import bpy

# scene.hydrokit property holding what is paused right now (JSON)
STAND = "abspielen_pause_stand"

# Below this an animated mesh re-evaluates in a few ms (one tree alone:
# 0.37 ms per 1000 vertices)
SCHWER_PUNKTE = 20_000
# Estimate until a frame change was measured: 556 ms for the 2.47 M
# vertices above, all trees evaluating in parallel
MS_PRO_MIO_PUNKTE = 225.0
# Worth a hint: more than one frame at 24 fps
SPUERBAR_MS = 40.0

# These change with the frame by themselves; pausing animation stops nothing
ZEIT_MODIFIER = {'WAVE', 'BUILD', 'MESH_CACHE', 'MESH_SEQUENCE_CACHE', 'EXPLODE', 'CLOTH',
                 'SOFT_BODY', 'DYNAMIC_PAINT', 'FLUID', 'PARTICLE_SYSTEM'}
ZEIT_KNOTEN = {'GeometryNodeInputSceneTime', 'GeometryNodeSimulationInput',
               'GeometryNodeSimulationOutput'}
# bpy.data collections whose animation can move a mesh
SAMMLUNGEN = ("objects", "meshes", "shape_keys", "textures")

_ZUSTAND = {"spielt": False, "nach_speichern": [], "nach_render": []}
# Paused per scene (session_uid): {"szene", "frame", "eintraege", "plan"}
_PAUSE = {}
# Animation step of a frame change (frame_change_pre -> _post) in ms: the
# time other animation costs, without HyprW's own handler after it
_MESSUNG = {"t0": None, "frei": [], "pausiert": [], "verwerfen": 0}
# Panel numbers per scene, planned at most every PLAN_ALTER seconds: the
# sidebar redraws on every frame while playing (the plan of the user's
# scene, 53 objects: 7 ms)
_ANZEIGE = {}
PLAN_ALTER = 2.0
# Modifier type -> names of its object pointers
_ZEIGER = {}


def _sicher(fn, *args):
    """A pause or a restore must never stop a save, a load or playback."""
    try:
        return fn(*args)
    except Exception as e:
        print(f"HyprW: Pause Other Animation: {fn.__name__} failed ({e})")
        return None


def eingeschaltet(scene):
    return bool(getattr(getattr(scene, "hydrokit", None), "abspielen_andere_pausieren", False))


def spielt():
    """True while the timeline plays. The playback handlers set the flag;
    with a window open the screen has to confirm it."""
    if not _ZUSTAND["spielt"]:
        return False
    if bpy.app.background:
        return True
    try:
        return any(w.screen is not None and w.screen.is_animation_playing
                   for w in bpy.context.window_manager.windows)
    except Exception:
        return True


def ist_pausiert(scene=None):
    if scene is None:
        return bool(_PAUSE)
    return scene.session_uid in _PAUSE


# ------------------------------------------------------------ What to pause
def _ist_hyprw(obj):
    from . import anzeige
    if obj.name in anzeige.ERGEBNISSE or obj.name.startswith("HK_"):
        return True
    return getattr(getattr(obj, "hydrokit", None), "typ", 'NONE') != 'NONE'


def _kette(obj, sammel):
    """obj and everything its transform follows: parents and constraint
    targets. sammel: session_uid -> object."""
    stapel = [obj]
    while stapel:
        o = stapel.pop()
        if o is None or o.session_uid in sammel:
            continue
        sammel[o.session_uid] = o
        stapel.append(o.parent)
        for c in o.constraints:
            if c.enabled:
                stapel.append(getattr(c, "target", None))
    return sammel


def _modifier_objekte(obj):
    """Objects the viewport modifiers follow (botaniq: the origin of Simple
    Deform and the texture space of Displace are the wind helpers)."""
    for m in obj.modifiers:
        if not m.show_viewport:
            continue
        namen = _ZEIGER.get(m.bl_rna.identifier)
        if namen is None:
            namen = _ZEIGER[m.bl_rna.identifier] = [
                p.identifier for p in m.bl_rna.properties
                if p.type == 'POINTER' and p.fixed_type.identifier == 'Object']
        for name in namen:
            o = getattr(m, name, None)
            if o is not None:
                yield o


def _zeitknoten(gruppe, gesehen):
    """A Geometry Nodes group that reads the scene time or simulates."""
    if gruppe is None or gruppe.session_uid in gesehen:
        return False
    gesehen.add(gruppe.session_uid)
    for knoten in gruppe.nodes:
        if knoten.bl_idname in ZEIT_KNOTEN:
            return True
        if _zeitknoten(getattr(knoten, "node_tree", None), gesehen):
            return True
    return False


def _ausgewertet(scene, depsgraph=None):
    """session_uids of the objects Blender evaluates for the viewport,
    hidden parents of visible children included; None if unknown."""
    try:
        if depsgraph is None:
            vl = bpy.context.view_layer
            if vl is None or bpy.context.scene != scene:
                vl = scene.view_layers[0]
            depsgraph = vl.depsgraph
        return {i.original.session_uid for i in depsgraph.ids
                if isinstance(i, bpy.types.Object)}
    except Exception:
        return None


def plan_erstellen(scene, depsgraph=None):
    """What Pause Other Animation would hold still in this scene.

    Heavy: a mesh with SCHWER_PUNKTE vertices or more that Blender evaluates
    and whose geometry changes per frame through animation, its own or that
    of the objects its modifiers follow. Its sources are paused only if all
    of them can be: local, no library override, no drivers, no NLA, not a
    camera or light, nothing HyprW's objects or the camera follow. Returns
    {"quellen": {(collection, uid): data-block}, "schwer": [(name,
    vertices)], "punkte", "nicht": [(name, reason)]}."""
    ausgewertet = _ausgewertet(scene, depsgraph)
    # What HyprW's objects (an animated obstacle, the lens drops on the
    # camera) and the camera follow keeps moving
    geschuetzt = {}
    for o in scene.objects:
        if _ist_hyprw(o):
            _kette(o, geschuetzt)
            for ziel in _modifier_objekte(o):
                _kette(ziel, geschuetzt)
    if scene.camera is not None:
        _kette(scene.camera, geschuetzt)
    plan = {"quellen": {}, "schwer": [], "punkte": 0, "nicht": []}
    for o in scene.objects:
        if (o.type != 'MESH' or _ist_hyprw(o)
                or (ausgewertet is not None and o.session_uid not in ausgewertet)):
            continue
        punkte = len(o.data.vertices)
        if punkte < SCHWER_PUNKTE:
            continue
        kette = _kette(o, {})
        for ziel in _modifier_objekte(o):
            _kette(ziel, kette)
        kandidaten = [("objects", k) for k in kette.values()]
        kandidaten.append(("meshes", o.data))
        if o.data.shape_keys is not None:
            kandidaten.append(("shape_keys", o.data.shape_keys))
        gesehen = set()
        grund = None
        for m in o.modifiers:
            if not m.show_viewport:
                continue
            if getattr(m, "texture", None) is not None:
                kandidaten.append(("textures", m.texture))
            if m.type in ZEIT_MODIFIER or (m.type == 'NODES' and _zeitknoten(m.node_group, gesehen)):
                grund = grund or "simulation or scene time"
        zeit = grund is not None
        quellen = {}
        for sammlung, idb in kandidaten:
            ad = getattr(idb, "animation_data", None)
            if ad is None:
                continue
            if len(ad.drivers):
                zeit, grund = True, grund or "drivers"
            if len(ad.nla_tracks):
                zeit, grund = True, grund or "NLA"
            if ad.action is None or ad.action_slot is None:
                continue
            zeit = True
            if idb.library is not None or idb.override_library is not None:
                grund = grund or "linked"
            elif sammlung == "objects" and (idb.session_uid in geschuetzt
                                            or idb.type in ('CAMERA', 'LIGHT')):
                grund = grund or "follows the camera or HyprW"
            else:
                quellen[(sammlung, idb.session_uid)] = idb
        if not zeit:
            continue        # does not change per frame
        if grund:
            plan["nicht"].append((o.name, grund))
            continue
        plan["schwer"].append((o.name, punkte))
        plan["punkte"] += punkte
        plan["quellen"].update(quellen)
    return plan


def _zusammenfassen(plan):
    return {"objekte": len(plan["quellen"]), "schwer": len(plan["schwer"]),
            "punkte": plan["punkte"], "nicht": list(plan["nicht"])}


# ------------------------------------------------------------ The record
def _stand_lesen(sc):
    p = getattr(sc, "hydrokit", None)
    if sc.library is not None or p is None or not p.is_property_set(STAND):
        return None
    try:
        return json.loads(getattr(p, STAND)).get("eintraege", [])
    except (ValueError, AttributeError):
        return []


def _stand_loeschen(sc):
    """Unset, not emptied: the file is left exactly as it was."""
    p = getattr(sc, "hydrokit", None)
    if sc.library is None and p is not None and p.is_property_set(STAND):
        p.property_unset(STAND)


def _szene_finden(uid, name):
    for sc in bpy.data.scenes:
        if sc.session_uid == uid:
            return sc
    sc = bpy.data.scenes.get((name, None))
    return sc


def _zuruecksetzen(eintraege, uids):
    """Give each entry its slot back. Only where the data-block still holds
    the same action without a slot: whatever the user changed meanwhile
    stays as it is."""
    karten = {}
    zurueck = 0
    for e in eintraege:
        sammlung = e.get("sammlung")
        if sammlung not in SAMMLUNGEN:
            continue
        daten = getattr(bpy.data, sammlung)
        idb = None
        if uids and e.get("uid") is not None:
            karte = karten.get(sammlung)
            if karte is None:
                karte = karten[sammlung] = {i.session_uid: i for i in daten if i.library is None}
            idb = karte.get(e["uid"])
        if idb is None:
            idb = daten.get((e.get("name"), None))
        if idb is None or idb.library is not None:
            continue
        ad = idb.animation_data
        if (ad is None or ad.action is None or ad.action_slot is not None
                or ad.action.name != e.get("aktion")):
            continue
        slots = list(ad.action.slots)
        slot = (next((s for s in slots if s.handle == e.get("griff")), None)
                or next((s for s in slots if s.identifier == e.get("kennung")), None))
        if slot is None:
            continue
        ad.action_slot = slot
        zurueck += 1
    return zurueck


def fortsetzen():
    """Everything moves again: what this session paused and every record
    found in the data (undo steps, autosaves, crash files). Returns how many
    slots were set back."""
    zurueck = 0
    for uid in list(_PAUSE):
        pause = _PAUSE.pop(uid)
        zurueck += _zuruecksetzen(pause["eintraege"], True)
        sc = _szene_finden(uid, pause["szene"])
        if sc is not None:
            _stand_loeschen(sc)
    for sc in bpy.data.scenes:
        eintraege = _stand_lesen(sc)
        if eintraege is None:
            continue
        zurueck += _zuruecksetzen(eintraege, False)
        _stand_loeschen(sc)
    _MESSUNG["verwerfen"] = 1
    return zurueck


def pausieren(scene, depsgraph=None):
    """Hold the heavy animation of this scene still. Returns how many
    data-blocks were paused."""
    uid = scene.session_uid
    if uid in _PAUSE:
        return len(_PAUSE[uid]["eintraege"])
    if scene.library is not None or getattr(scene, "hydrokit", None) is None:
        return 0
    plan = plan_erstellen(scene, depsgraph)
    eintraege = []
    for (sammlung, _), idb in plan["quellen"].items():
        ad = idb.animation_data
        eintraege.append({"sammlung": sammlung, "name": idb.name, "uid": idb.session_uid,
                          "griff": ad.action_slot_handle, "kennung": ad.action_slot.identifier,
                          "aktion": ad.action.name})
    if not eintraege:
        return 0
    # The record first: an undo step, autosave or crash file taken from now
    # on knows what to set back
    setattr(scene.hydrokit, STAND, json.dumps({
        "frame": scene.frame_current,
        "eintraege": [{k: v for k, v in e.items() if k != "uid"} for e in eintraege]}))
    _PAUSE[uid] = {"szene": scene.name, "frame": scene.frame_current, "eintraege": eintraege,
                   "plan": _zusammenfassen(plan)}
    for idb in plan["quellen"].values():
        try:
            idb.animation_data.action_slot = None
        except Exception as e:
            print(f"HyprW: could not pause {idb.name} ({e})")
    _MESSUNG["verwerfen"] = 1
    _waechter_starten()
    return len(eintraege)


def einstellung_geaendert(scene):
    """Switched while playing: acts at once."""
    _ANZEIGE.clear()
    if not spielt():
        return
    if eingeschaltet(scene):
        pausieren(scene)
    else:
        fortsetzen()


# ------------------------------------------------------------ Panel numbers
def _median(werte):
    return statistics.median(werte) if len(werte) >= 3 else None


def anzeige_daten(scene):
    """What the panel shows: how many objects the pause holds still (or
    would), and what a frame of them costs: measured once frames changed,
    estimated from the vertices before."""
    uid = scene.session_uid
    pause = _PAUSE.get(uid)
    if pause is not None:
        daten = dict(pause["plan"])
    else:
        jetzt = time.monotonic()
        alt = _ANZEIGE.get(uid)
        if alt is None or jetzt - alt[0] > PLAN_ALTER:
            try:
                alt = (jetzt, _zusammenfassen(plan_erstellen(scene)))
            except Exception:
                alt = (jetzt, {"objekte": 0, "schwer": 0, "punkte": 0, "nicht": []})
            _ANZEIGE[uid] = alt
        daten = dict(alt[1])
    frei, pausiert = _median(_MESSUNG["frei"]), _median(_MESSUNG["pausiert"])
    geschaetzt = daten["punkte"] * MS_PRO_MIO_PUNKTE / 1e6
    daten["gemessen"] = frei is not None
    daten["ms"] = frei if frei is not None else geschaetzt
    if frei is not None and pausiert is not None:
        daten["ersparnis"] = max(0.0, frei - pausiert)
    else:
        daten["ersparnis"] = daten["ms"] if daten["objekte"] else 0.0
    daten["pausiert"] = len(pause["eintraege"]) if pause is not None else 0
    daten["frame"] = pause["frame"] if pause is not None else None
    return daten


# ------------------------------------------------------------ Watchdog
def _waechter():
    """Blender can end playback without the stop handler (a closed window,
    for example): nothing may stay paused then."""
    try:
        if not _PAUSE:
            return None
        if not spielt():
            _ZUSTAND["spielt"] = False
            fortsetzen()
            return None
    except Exception:
        return None
    return 1.0


def _waechter_starten():
    if not bpy.app.background and not bpy.app.timers.is_registered(_waechter):
        bpy.app.timers.register(_waechter, first_interval=1.0, persistent=True)


# ------------------------------------------------------------ Handlers
@bpy.app.handlers.persistent
def _pause_beim_abspielen(scene, depsgraph=None):
    _ZUSTAND["spielt"] = True
    if eingeschaltet(scene):
        _sicher(pausieren, scene, depsgraph)


@bpy.app.handlers.persistent
def _pause_beim_anhalten(scene=None, depsgraph=None):
    _ZUSTAND["spielt"] = False
    _sicher(fortsetzen)


@bpy.app.handlers.persistent
def _pause_vor_speichern(*args):
    """The file gets the animation as it is: unpaused."""
    _ZUSTAND["nach_speichern"] = [(uid, p["szene"]) for uid, p in _PAUSE.items()]
    _sicher(fortsetzen)


@bpy.app.handlers.persistent
def _pause_nach_speichern(*args):
    """Saved (or failed to): pause again if still playing."""
    szenen, _ZUSTAND["nach_speichern"] = _ZUSTAND["nach_speichern"], []
    if spielt():
        _wieder_pausieren(szenen)


@bpy.app.handlers.persistent
def _pause_vor_laden(*args):
    """Loading ends playback without its stop handler."""
    _ZUSTAND["spielt"] = False
    _ZUSTAND["nach_render"] = []
    _sicher(fortsetzen)
    _PAUSE.clear()
    _ANZEIGE.clear()
    _MESSUNG.update(t0=None, frei=[], pausiert=[], verwerfen=0)


@bpy.app.handlers.persistent
def _pause_nach_laden(*args):
    """A file saved while paused (an autosave, a crash file): set back."""
    _ZUSTAND["spielt"] = False
    _sicher(fortsetzen)


@bpy.app.handlers.persistent
def _pause_nach_undo(scene=None, *args):
    """An undo step brings the data back as it was when the step was taken,
    paused or not: set back what is recorded, then pause again if still
    playing. The frame of the pause is now the current one."""
    szenen = [(uid, p["szene"]) for uid, p in _PAUSE.items()]
    _sicher(fortsetzen)
    if not spielt():
        return
    if not szenen and scene is not None:
        szenen = [(scene.session_uid, scene.name)]
    _wieder_pausieren(szenen)


@bpy.app.handlers.persistent
def _pause_vor_render(*args):
    """Every render shows the animation. F12 ends playback before it renders;
    this covers a render started from a script while playing. Only from the
    main thread: bpy is not thread-safe."""
    if threading.current_thread() is threading.main_thread() and _PAUSE:
        _ZUSTAND["nach_render"] = [(uid, p["szene"]) for uid, p in _PAUSE.items()]
        _sicher(fortsetzen)


def _wieder_pausieren(szenen):
    for uid, name in szenen:
        sc = _szene_finden(uid, name)
        if sc is not None and eingeschaltet(sc):
            _sicher(pausieren, sc)


def _nach_render_pausieren():
    szenen, _ZUSTAND["nach_render"] = _ZUSTAND["nach_render"], []
    if spielt():
        _wieder_pausieren(szenen)
    return None


@bpy.app.handlers.persistent
def _pause_nach_render(*args):
    """The render is done or was cancelled: pause again if still playing.
    Before, a render from a script left everything moving until playback
    stopped. Blender may call this from the render's thread: then on the
    main thread by a timer."""
    if not _ZUSTAND["nach_render"]:
        return
    if threading.current_thread() is threading.main_thread():
        _sicher(_nach_render_pausieren)
    elif not bpy.app.timers.is_registered(_nach_render_pausieren):
        bpy.app.timers.register(_nach_render_pausieren, first_interval=0.0)


@bpy.app.handlers.persistent
def _pause_beim_beenden(*args):
    _sicher(fortsetzen)


@bpy.app.handlers.persistent
def _pause_frame_vor(*args):
    if threading.current_thread() is threading.main_thread():
        _MESSUNG["t0"] = time.perf_counter()


@bpy.app.handlers.persistent
def _pause_frame_nach(*args):
    t0 = _MESSUNG["t0"]
    if t0 is None or threading.current_thread() is not threading.main_thread():
        return
    _MESSUNG["t0"] = None
    if _MESSUNG["verwerfen"] > 0:
        _MESSUNG["verwerfen"] -= 1     # the one frame that re-evaluates after a switch
        return
    werte = _MESSUNG["pausiert" if _PAUSE else "frei"]
    werte.append((time.perf_counter() - t0) * 1000.0)
    del werte[:-9]


_HANDLER = (
    ("animation_playback_pre",  _pause_beim_abspielen),
    ("animation_playback_post", _pause_beim_anhalten),
    ("save_pre",                _pause_vor_speichern),
    ("save_post",               _pause_nach_speichern),
    ("save_post_fail",          _pause_nach_speichern),
    ("load_pre",                _pause_vor_laden),
    ("load_post",               _pause_nach_laden),
    ("undo_post",               _pause_nach_undo),
    ("redo_post",               _pause_nach_undo),
    ("render_init",             _pause_vor_render),
    ("render_complete",         _pause_nach_render),
    ("render_cancel",           _pause_nach_render),
    ("exit_pre",                _pause_beim_beenden),
    ("frame_change_pre",        _pause_frame_vor),
    # First in the list: the measurement ends before HyprW's own handler
    ("frame_change_post",       _pause_frame_nach),
)


def _entfernen():
    """By name and module: after a reload the old module's functions are
    other objects, and anzeige.py has handlers of its own."""
    for liste, fn in _HANDLER:
        L = getattr(bpy.app.handlers, liste, None)
        if L is None:
            continue
        for h in list(L):
            if (getattr(h, "__name__", "") == fn.__name__
                    and getattr(h, "__module__", "").endswith("szene_pause")):
                L.remove(h)


def _nach_aktivieren():
    """After enabling or a reload: records an earlier session left in the
    data are set back."""
    try:
        if "hydrokit" in bpy.types.Scene.bl_rna.properties and not _PAUSE:
            fortsetzen()
    except Exception:
        pass
    return None


def handler_an():
    _entfernen()
    for liste, fn in _HANDLER:
        L = getattr(bpy.app.handlers, liste, None)
        if L is None:
            continue        # a Blender without this list
        if liste == "frame_change_post":
            L.insert(0, fn)
        else:
            L.append(fn)
    if not bpy.app.background and not bpy.app.timers.is_registered(_nach_aktivieren):
        bpy.app.timers.register(_nach_aktivieren, first_interval=0.2)


def handler_aus():
    """Unregister: everything moves again before the handlers go."""
    _sicher(fortsetzen)
    _entfernen()
    for zeitgeber in (_waechter, _nach_aktivieren, _nach_render_pausieren):
        if bpy.app.timers.is_registered(zeitgeber):
            bpy.app.timers.unregister(zeitgeber)
    _ZUSTAND["spielt"] = False
    _ZUSTAND["nach_render"] = []
    _ANZEIGE.clear()
