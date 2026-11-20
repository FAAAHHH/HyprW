# HyprW — the light mist while the timeline plays
# SPDX-License-Identifier: GPL-3.0-or-later
#
# A baked waterfall played back at 0.7 frames per second while the paused
# viewport was smooth. The full mist volume alone took 173 to 556 ms of every
# frame; while the timeline plays, the viewport draws a light mist instead
# (sim/aussehen.py, NEBEL_LEICHT_PUNKTE), 12 to 19 ms. Light Playback
# (scene.hydrokit.abspielen_leicht) switches that off.
#
# The full look comes back when playback stops, before every save, after
# opening a file, after an undo or redo while not playing, and when the
# add-on is switched off. Renders never see the light mist: its modifier is
# off for rendering. Everything here runs in handlers on the main thread; no
# timers, no threads.

import bpy

from . import aussehen


def _erlaubt(scene):
    """Light Playback on (the default, also before the setting exists)."""
    p = getattr(scene, "hydrokit", None) if scene is not None else None
    return p is not None and bool(getattr(p, "abspielen_leicht", True))


def spielt():
    """True while the timeline plays in any window."""
    wm = getattr(bpy.context, "window_manager", None)
    if wm is None:
        return False
    return any(w.screen is not None and w.screen.is_animation_playing for w in wm.windows)


# Handler names are unique to this module: the add-on's modules remove their
# own handlers by name (anzeige._entfernen).
@bpy.app.handlers.persistent
def _aussehen_spiel_an(scene, depsgraph=None):
    try:
        if _erlaubt(scene):
            aussehen.abspielen_leicht(scene, True)
    except Exception as e:
        print(f"HyprW: light mist for playback ({e})")


@bpy.app.handlers.persistent
def _aussehen_spiel_aus(scene, depsgraph=None):
    # always, also when Light Playback was switched off while playing
    try:
        aussehen.alles_voll()
    except Exception as e:
        print(f"HyprW: full mist after playback ({e})")


@bpy.app.handlers.persistent
def _aussehen_bei_frame(scene, depsgraph=None):
    """While playing, the mist follows Light Playback at the next frame: when
    it is switched during playback, or the playback started before the
    add-on was loaded. Otherwise it only reads a few flags; scrubbing and
    renders change nothing here."""
    try:
        if bpy.app.is_job_running('RENDER') or not spielt():
            return
        soll = _erlaubt(scene)
        if soll != aussehen.ist_leicht(scene):
            aussehen.abspielen_leicht(scene, soll)
    except Exception as e:
        print(f"HyprW: mist while playing ({e})")


@bpy.app.handlers.persistent
def _aussehen_vor_speichern(*args):
    """A saved file never keeps the light mist."""
    try:
        aussehen.alles_voll()
    except Exception as e:
        print(f"HyprW: full mist before saving ({e})")


@bpy.app.handlers.persistent
def _aussehen_nach_speichern(*args):
    """Saved while playing: light again for the rest of the playback."""
    try:
        scene = bpy.context.scene
        if spielt() and _erlaubt(scene):
            aussehen.abspielen_leicht(scene, True)
    except Exception as e:
        print(f"HyprW: light mist after saving ({e})")


@bpy.app.handlers.persistent
def _aussehen_nach_laden(*args):
    """A file written while light (an autosave, a crash) opens with the full
    look."""
    try:
        aussehen.alles_voll(geladen=True)
    except Exception as e:
        print(f"HyprW: full mist after loading ({e})")


@bpy.app.handlers.persistent
def _aussehen_nach_undo(*args):
    """An undo step brings back the look it was stored with: light only
    while playing."""
    try:
        scene = bpy.context.scene
        if spielt() and _erlaubt(scene):
            aussehen.abspielen_leicht(scene, True)
        else:
            aussehen.alles_voll()
    except Exception as e:
        print(f"HyprW: mist after undo ({e})")


_HANDLER = (
    ("animation_playback_pre",  _aussehen_spiel_an),
    ("animation_playback_post", _aussehen_spiel_aus),
    ("frame_change_pre",        _aussehen_bei_frame),
    ("save_pre",                _aussehen_vor_speichern),
    ("save_post",               _aussehen_nach_speichern),
    ("load_post",               _aussehen_nach_laden),
    ("undo_post",               _aussehen_nach_undo),
    ("redo_post",               _aussehen_nach_undo),
)


def lade_handler_an():
    """Installed with the add-on. Touches no data: register() runs while
    bpy.data is not accessible yet."""
    lade_handler_aus()
    for liste, fn in _HANDLER:
        getattr(bpy.app.handlers, liste).append(fn)


def lade_handler_aus():
    for liste, fn in _HANDLER:
        L = getattr(bpy.app.handlers, liste)
        for h in list(L):
            if getattr(h, "__name__", "") == fn.__name__:
                L.remove(h)


def abschalten():
    """Add-on switched off: no handlers, and the full look back. Switched
    off while playing, the light mist would otherwise stay for good."""
    lade_handler_aus()
    try:
        aussehen.alles_voll()
    except Exception as e:
        print(f"HyprW: full mist when switching off ({e})")
