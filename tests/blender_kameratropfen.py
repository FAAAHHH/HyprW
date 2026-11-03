# HyprW — background-mode test of the lens drops
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Usage (repo path after "--", otherwise the folder above this script):
#   blender -b --factory-startup --python tests/blender_kameratropfen.py -- /path/to/hydrokit
# or with the bpy module from PyPI:
#   python tests/blender_kameratropfen.py /path/to/hydrokit
#
# Checks: installing and enabling the add-on, properties, panel drawing (with
# a mock layout that checks every call against Blender's RNA: property names,
# keywords, icons, operators) and sim/linse.py with an artificial cache in
# which every hit lands at its own spot on the glass. That way it can be
# checked per drop whether it was allowed to stick.
# At the end a small real bake with the engine and the lens on top of it.
#
# The look (Geometry Nodes, material) is only called, not judged.

import json
import math
import os
import shutil
import struct
import sys
import tempfile
import traceback
import zipfile

import bpy
import numpy as np

FEHLER = []
ERGEBNISSE = []


def melden(text):
    ERGEBNISSE.append(text)
    print("  " + text)


def pruefe(bedingung, text):
    if bedingung:
        melden("OK    " + text)
    else:
        FEHLER.append(text)
        print("  FAIL   " + text)


def abschnitt(name):
    def deko(fn):
        def lauf(*a, **k):
            print(f"\n=== {name}")
            try:
                return fn(*a, **k)
            except Exception:
                tb = traceback.format_exc()
                FEHLER.append(f"{name}: Traceback\n{tb}")
                print(tb)
                return None
        return lauf
    return deko


# ------------------------------------------------------------------ Paths
def _repo():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    if argv:
        return os.path.abspath(argv[0])
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


REPO = _repo()
ARBEIT = tempfile.mkdtemp(prefix="hk_test_")


# -------------------------------------------------------- Installation
@abschnitt("Install and enable the add-on")
def installieren():
    if not bpy.app.background:
        raise RuntimeError("meant for background mode only")
    melden(f"Blender {bpy.app.version_string}, Repo {REPO}")
    zip_pfad = os.path.join(ARBEIT, "hydrokit.zip")
    with zipfile.ZipFile(zip_pfad, "w", zipfile.ZIP_DEFLATED) as z:
        for wurzel, ordner, dateien in os.walk(REPO):
            ordner[:] = [o for o in ordner if o not in (".git", "__pycache__", "tests")]
            for d in dateien:
                voll = os.path.join(wurzel, d)
                z.write(voll, os.path.relpath(voll, REPO))
    erg = bpy.ops.extensions.package_install_files(
        filepath=zip_pfad, repo='user_default', enable_on_install=False)
    pruefe(erg == {'FINISHED'}, f"package_install_files -> {erg}")

    import addon_utils
    modul = "bl_ext.user_default.hydrokit"
    fehler = []
    addon_utils.enable(modul, default_set=True,
                       handle_error=lambda ex: fehler.append(traceback.format_exc()))
    for tb in fehler:
        FEHLER.append("Enabling: Traceback\n" + tb)
        print(tb)
    pruefe(not fehler and modul in bpy.context.preferences.addons,
           "Add-on enabled (register() without errors)")
    einst = bpy.context.preferences.addons[modul].preferences if not fehler else None
    pruefe(getattr(einst, "scroll_schritte", None) == 8
           and hasattr(bpy.ops.hydrokit, "seitenleiste_scrollen"),
           "add-on preferences: sidebar scroll speed 8, scroll operator registered")
    return sys.modules.get(modul)


@abschnitt("Properties")
def properties(hk):
    pruefe(hasattr(bpy.types.Scene, "hydrokit"), "Scene.hydrokit registered")
    pruefe(hasattr(bpy.types.Object, "hydrokit"), "Object.hydrokit registered")
    rna = bpy.types.Object.bl_rna.properties["hydrokit"].fixed_type.bl_rna.properties
    erwartet = {"linse_an": ('BOOLEAN', None)}
    for art in ("wasser", "spray", "nebel"):
        erwartet[f"linse_{art}_haftet"] = ('BOOLEAN', None)
        erwartet[f"linse_{art}_haftanteil"] = ('FLOAT', 'PERCENTAGE')
    for name, (typ, sub) in erwartet.items():
        p = rna.get(name)
        if p is None:
            pruefe(False, f"{name} missing")
            continue
        info = f"{name}: {p.type}"
        ok = p.type == typ
        if sub:
            info += f"/{p.subtype} {p.hard_min:g}..{p.hard_max:g}, default {p.default:g}"
            ok &= p.subtype == sub and p.hard_min == 0 and p.hard_max == 100 and p.default == 100
        else:
            info += f", default {p.default}"
        pruefe(ok, info)
    for alt in ("linse_wasser_an", "linse_spray_anteil"):
        pruefe(rna.get(alt) is None, f"old name {alt} removed")
    r, v = rna["oberflaechen_unterteilung"], rna["unterteilung_viewport"]
    pruefe(r.default == 2 and v.default == 1 and r.hard_min == v.hard_min == 1
           and r.hard_max == v.hard_max == 3 and rna["viewport_eigenes_netz"].default is False,
           f"water mesh detail {r.default}, {r.hard_min} to {r.hard_max} (the engine refuses 0); "
           f"lighter viewport mesh off, at detail {v.default} when switched on")
    # Read and write every property once (also triggers update functions)
    obj = bpy.data.objects["Cube"]
    for p in rna:
        if p.identifier == "rna_type" or p.is_readonly:
            continue
        wert = getattr(obj.hydrokit, p.identifier)
        try:
            setattr(obj.hydrokit, p.identifier, wert)
        except Exception:
            pruefe(False, f"{p.identifier} reset:\n{traceback.format_exc()}")
    sc = bpy.context.scene.hydrokit
    for p in sc.bl_rna.properties:
        if p.identifier != "rna_type" and not p.is_readonly:
            getattr(sc, p.identifier)
    melden("all object and scene properties readable and writable")


# ------------------------------------------------------- Mock layout
UI_FN = bpy.types.UILayout.bl_rna.functions
UI_PROPS = {p.identifier for p in bpy.types.UILayout.bl_rna.properties}
ICONS = {i.identifier for i in UI_FN["label"].parameters["icon"].enum_items}
UNTERLAYOUTS = {"row", "column", "box", "split", "grid_flow", "column_flow"}


class OpAttrappe:
    def __init__(self, idname, rna):
        object.__setattr__(self, "_idname", idname)
        object.__setattr__(self, "_rna", rna)

    def __setattr__(self, k, v):
        if k not in self._rna.properties:
            raise AttributeError(f"operator {self._idname} has no property {k}")
        object.__setattr__(self, k, v)


class Layout:
    """Takes calls like UILayout and checks them against the RNA."""

    def __init__(self, protokoll, eltern=None):
        object.__setattr__(self, "_p", protokoll)
        object.__setattr__(self, "_eltern", eltern)
        object.__setattr__(self, "enabled", True)

    def __setattr__(self, k, v):
        if k not in UI_PROPS:
            raise AttributeError(f"UILayout has no attribute {k}")
        object.__setattr__(self, k, v)

    def _aktiv(self):
        l = self
        while l is not None:
            if not l.enabled:
                return False
            l = l._eltern
        return True

    def __getattr__(self, name):
        if name.startswith("_") or name not in UI_FN:
            raise AttributeError(f"UILayout has no function {name}")
        fn = UI_FN[name]
        eingaben = [p for p in fn.parameters if not p.is_output]
        namen = [p.identifier for p in eingaben]

        def aufruf(*args, **kw):
            if len(args) > len(namen):
                raise TypeError(f"{name}: too many arguments")
            gegeben = dict(zip(namen, args))
            for k, v in kw.items():
                if k not in namen:
                    raise TypeError(f"{name}: unknown keyword {k!r}")
                gegeben[k] = v
            for p in eingaben:
                if p.is_required and p.identifier not in gegeben:
                    raise TypeError(f"{name}: {p.identifier} missing")
            if "icon" in gegeben and gegeben["icon"] not in ICONS:
                raise ValueError(f"{name}: icon {gegeben['icon']!r} does not exist")
            aktiv = self._aktiv()
            if name == "prop":
                daten, prop = gegeben["data"], gegeben["property"]
                if prop not in daten.bl_rna.properties:
                    raise AttributeError(f"prop: {type(daten).__name__} has no "
                                         f"property {prop!r}")
                self._p.append(("prop", prop, aktiv))
                return None
            if name == "operator":
                idname = gegeben["operator"]
                modul, op = idname.split(".")
                rna = getattr(getattr(bpy.ops, modul), op).get_rna_type()
                self._p.append(("operator", idname, aktiv))
                return OpAttrappe(idname, rna)
            if name == "panel":
                self._p.append(("panel", gegeben["idname"], aktiv))
                return Layout(self._p, self), Layout(self._p, self)
            if name in UNTERLAYOUTS:
                return Layout(self._p, self)
            if name == "label":
                self._p.append(("label", gegeben.get("text", ""), aktiv))
            return None
        return aufruf


def zeichnen(cls, context):
    """Call draw() of the panel class with the mock."""
    protokoll = []
    methoden = {k: v for k, v in vars(cls).items() if callable(v) and not k.startswith("__")}
    attrappe = type("PanelAttrappe", (), methoden)()
    attrappe.layout = Layout(protokoll)
    cls.draw(attrappe, context)
    return protokoll


def _aktiv(protokoll, art, name):
    treffer = [a for k, n, a in protokoll if k == art and n == name]
    return treffer[0] if treffer else None


def _stelle(protokoll, art, name):
    """Position of the first entry in the protocol, or None."""
    return next((i for i, (k, n, _) in enumerate(protokoll) if k == art and n == name), None)


def _texte(protokoll):
    return [n for k, n, _ in protokoll if k == "label"]


@abschnitt("Self-test of the mock layout")
def attrappe_selbsttest():
    d = bpy.data.objects["Cube"].hydrokit
    for beschreibung, aufruf in (
            ("wrong property name", lambda l: l.prop(d, "gibt_es_nicht")),
            ("wrong icon", lambda l: l.label(text="x", icon='GIBT_ES_NICHT')),
            ("wrong keyword", lambda l: l.prop(d, "linse_an", gibts=True)),
            ("wrong operator", lambda l: l.operator("hydrokit.gibt_es_nicht")),
            ("wrong attribute", lambda l: setattr(l, "gibts", 1)),
            ("wrong operator property",
             lambda l: setattr(l.operator("hydrokit.set_type"), "gibts", 1))):
        try:
            aufruf(Layout([]))
            pruefe(False, f"mock does NOT detect {beschreibung}")
        except Exception as ex:
            pruefe(True, f"mock detects {beschreibung} ({type(ex).__name__})")


@abschnitt("Draw panels")
def panel(hk):
    from importlib import import_module
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")
    ctx = bpy.context
    cube = bpy.data.objects["Cube"]
    d = cube.hydrokit

    cube.hydrokit.typ = 'NONE'
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(any(k == "label" and "No domain yet" in n for k, n, _ in p),
           "sim panel without domain: hint drawn")

    cube.hydrokit.typ = 'DOMAIN'
    d.linse_an = False
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    melden(f"sim panel with domain: {len(p)} entries, all checked against the RNA")
    pruefe(_aktiv(p, "panel", "hk_sim_linse") is not None
           and _stelle(p, "panel", "hk_sim_linse_wasser") is not None
           and _stelle(p, "panel", "hk_sim_linse_spray") is not None,
           "own section hk_sim_linse, water and spray on the lens with the switch in the header")
    pruefe(_aktiv(p, "prop", "linse_an") is True, "main switch usable")
    unter = [f"linse_{a}_{t}" for a in ("wasser", "spray", "nebel")
             for t in ("haftet", "haftanteil")] + ["linse_glas"]
    pruefe(all(_aktiv(p, "prop", n) is False for n in unter),
           "main switch off: all sub-switches greyed out")
    pruefe(_aktiv(p, "operator", "hydrokit.linse_berechnen") is False,
           "main switch off: button greyed out")

    d.linse_an = True
    d.linse_spritz_rest = 100.0   # splashing changes no volume here
    d.linse_spray_haftet = False
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(all(_aktiv(p, "prop", n) is True for n in unter if n != "linse_spray_haftanteil"),
           "main switch on: sub-switches usable")
    pruefe(_aktiv(p, "prop", "linse_spray_haftanteil") is False,
           "spray sticks off: only its slider greyed out")
    d.linse_spray_haftet = True

    sh = bpy.context.scene.hydrokit
    szene = bpy.context.scene
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    sim_ops = import_module(hk.__name__ + ".operators.sim_ops")
    cache_m = import_module(hk.__name__ + ".sim.cache")
    # A // cache path in an unsaved file: no folder (it was Blender's working folder)
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(not bpy.data.filepath and d.cache_pfad.startswith("//")
           and cache_m.pfad_aufloesen(d.cache_pfad) == ""
           and not bpy.ops.hydrokit.sim_bake.poll()
           and any("Save the file first" in t for t in _texte(p)),
           "unsaved file: no cache in Blender's working folder, Bake asks to save first")
    pruefe(sim_ops.anzahl(1, "frame") == "1 frame" and sim_ops.anzahl(3, "frame") == "3 frames",
           "texts: 1 frame, 3 frames")

    # The running bake is module state: an undo step brought back older scene values
    ende = szene.frame_end
    sim_ops.BAKE.update(laeuft=True, szene=szene.name, frame=37, start=szene.frame_start,
                        ende=ende, sekunden=0.8)
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    texte = _texte(p)
    pruefe(_aktiv(p, "operator", "hydrokit.sim_stop") is True
           and f"Baking frame 37 of {ende}" in texte
           and any("0.8 s per frame" in t and "left" in t for t in texte)
           and not hasattr(sh, "backt") and not bpy.ops.hydrokit.sim_bake.poll(),
           f"sim panel while baking: frame number, time left, a bar instead of a slider, "
           f"no second bake ({texte[:2]})")
    sim_ops.BAKE.update(frame=87, start=50, ende=160)
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe("Baking frame 87 (38 of 111)" in _texte(p),
           "range from frame 50: the frame and its place in the range")
    bpy.ops.hydrokit.sim_stop()
    stopp = sim_ops.BAKE["abbrechen"]
    # Opening a file: nothing is baking any more
    anzeige._nach_laden(None)
    pruefe(stopp and sim_ops.BAKE["laeuft"] is False and sim_ops.BAKE["abbrechen"] is False,
           "Cancel asks the bake to stop; opening a file: no baking box left over")

    # Water mesh detail: one field; the viewport detail only with Lighter
    # Viewport switched on; Coarse Preview replaces both
    d.vorschau_im_viewport = d.viewport_eigenes_netz = False
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    gut = (_aktiv(p, "prop", "oberflaechen_unterteilung") is True
           and _aktiv(p, "prop", "viewport_eigenes_netz") is True
           and _aktiv(p, "prop", "unterteilung_viewport") is False
           and _aktiv(p, "prop", "vorschau_aufloesung") is False)
    d.viewport_eigenes_netz = True
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    gut = gut and _aktiv(p, "prop", "unterteilung_viewport") is True
    d.vorschau_im_viewport = True
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    gut = gut and (_aktiv(p, "prop", "viewport_eigenes_netz") is False
                   and _aktiv(p, "prop", "unterteilung_viewport") is False
                   and _aktiv(p, "prop", "vorschau_aufloesung") is True)
    d.vorschau_im_viewport = d.viewport_eigenes_netz = False
    pruefe(gut, "mesh detail: viewport detail only with Lighter Viewport on; "
                "Coarse Preview greys both out")

    # Water: a section of its own, also without whitewater
    d.gischt = False
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(_aktiv(p, "panel", "hk_sim_wasser") is not None
           and all(_aktiv(p, "prop", n) is True for n in (
               "wasser_variante", "wasser_sichtweite", "wasser_kaustik", "wasser_dispersion"))
           and _aktiv(p, "operator", "hydrokit.sim_water") is True,
           "water section without whitewater: water type, sliders, Apply Water")
    if bpy.data.objects.get(anzeige.OBERFLAECHE) is None:
        cube.select_set(True)
        ctx.view_layer.objects.active = cube
        pruefe(any(k == "label" and "bake first" in n for k, n, _ in p)
               and not bpy.ops.hydrokit.sim_water.poll(),
               "before a bake: Apply Water greyed out, hint to bake first")
    else:
        melden("before-a-bake check skipped: a water object exists already")

    # Order: Bake, then Look (acts at once), then Domain (next bake). The
    # whitewater look greyed out without whitewater, the shutter not.
    reihe = [_stelle(p, "panel", n) for n in ("hk_sim_backen", "hk_sim_aussehen", "hk_sim_domain")]
    pruefe(None not in reihe and reihe == sorted(reihe)
           and _aktiv(p, "operator", "hydrokit.sim_look") is not None
           and _aktiv(p, "prop", "whitewater_scale_multiplier") is False
           and _aktiv(p, "prop", "menge_foam") is False and _aktiv(p, "prop", "spray_farbe") is False
           and _aktiv(p, "prop", "use_motion_blur") is True,
           "Look section between Bake and Domain; whitewater look greyed out without whitewater")
    # The bake section: cache folder and frame range above the button, and
    # a warning when nothing would bring water
    a, b = reihe[0], reihe[1]
    pruefe(all(a < (_stelle(p, "prop", n) or -1) < b for n in ("cache_pfad", "frame_start", "frame_end"))
           and any("stays empty" in t for t in _texte(p)),
           "bake section: cache folder and frame range, warning without water or inflow")
    # Domain: switches in the section headers, their contents greyed out while off
    unter = ("schaum_mitfuehrung", "nebel_an", "nebel_staerke", "gischt_tempo_min", "spray_groesse")
    d.adaptiv_an = False
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    gut = (_aktiv(p, "prop", "gischt") is True and all(_aktiv(p, "prop", n) is False for n in unter)
           and _aktiv(p, "prop", "adaptiv_an") is True and _aktiv(p, "prop", "adaptiv_max_stufe") is False)
    d.gischt = True
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    gut = gut and all(_aktiv(p, "prop", n) is True for n in unter) and _aktiv(p, "prop", "menge_foam") is True
    pruefe(gut, "domain: whitewater, mist and adaptive particles greyed out while their switch is off")
    pruefe(all(_aktiv(p, "prop", n) is not None
               for n in ("offene_raender", "wind_faktor", "wasser_wind", "wasser_wind_tiefe")),
           "domain: open borders, wind strength and wind on the water reachable")
    # Blender prints an update callback's error to stderr instead of raising it
    import contextlib, io
    ausgabe = io.StringIO()
    with contextlib.redirect_stderr(ausgabe):
        sh.gitter_anzeigen = True
        sh.gitter_seiten = 'ALLE'
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    with contextlib.redirect_stderr(ausgabe):
        sh.gitter_anzeigen = False
        sh.gitter_seiten = 'VORNE'
    mit = _aktiv(p, "prop", "gitter_seiten")
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(mit is True and _aktiv(p, "prop", "gitter_seiten") is None,
           "Grid on only with Show Grid on")
    pruefe("Error" not in ausgabe.getvalue(),
           "Show Grid and Grid on switch without an error: " + (ausgabe.getvalue().strip()[:120] or "none"))
    d.gischt = False

    # Role settings: every role draws its own box, all fields checked against the RNA
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0.5, 0.5, 0.5))
    probe = bpy.context.active_object
    for rolle, felder in (('FLUID', ["start_geschwindigkeit"]),
                          ('INFLOW', ["zufluss_geschwindigkeit", "zufluss_substeps"]),
                          ('OBSTACLE', ["hindernis_umkehren", "hindernis_reibung",
                                        "hindernis_gischt", "hindernis_nebel",
                                        "hindernis_sheeting", "hindernis_ausdehnung"]),
                          ('OUTFLOW', ["abfluss_wasser", "abfluss_gischt", "abfluss_umkehren",
                                       "abfluss_allmaehlich", "abfluss_rate"]),
                          ('MIST_DOMAIN', []), ('SPRAY_DOMAIN', [])):
        probe.hydrokit.typ = rolle
        p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
        titel = any(k == "label" and n.endswith(" Settings") for k, n, _ in p)
        da = all(_aktiv(p, "prop", f) is not None for f in felder + (
            ["rolle_aktiv"] if felder else []))
        pruefe(titel and da, f"role settings for {rolle}: {len(felder)} fields")
        if rolle == 'OBSTACLE':
            pruefe(any(probe.name in t for t in _texte(p))
                   and _stelle(p, "panel", "hk_sim_rolle_wechseln") is not None
                   and (_stelle(p, "prop", "hindernis_reibung") or 10**9)
                   < _stelle(p, "panel", "hk_sim_rolle_wechseln"),
                   "role section: object name in the header, settings first, the role "
                   "buttons under Change Role")
    from types import SimpleNamespace
    beschreibung = sim_ops.HYDROKIT_OT_set_type.description
    pruefe(beschreibung(ctx, SimpleNamespace(typ='OBSTACLE')) == "Water flows around it"
           and "already the domain" in beschreibung(ctx, SimpleNamespace(typ='DOMAIN')),
           "role buttons explain their role; a taken Domain says which object has it")
    probe.hydrokit.typ = 'OUTFLOW'
    probe.hydrokit.abfluss_allmaehlich = False
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(_aktiv(p, "prop", "abfluss_rate") is False,
           "outflow: rate greyed out without gradual outflow")
    # Helper roles invisible to Cycles rays (as in FLIP Fluids) — otherwise the
    # Rendered viewport draws the closed domain box and is black inside it
    probe.hydrokit.typ = 'NONE'
    bpy.context.view_layer.objects.active = probe
    bpy.ops.hydrokit.set_type(typ='MIST_DOMAIN')     # the cube is already the domain
    strahlen = ("visible_camera", "visible_diffuse", "visible_glossy",
                "visible_transmission", "visible_volume_scatter", "visible_shadow")
    pruefe(not any(getattr(probe, k) for k in strahlen) and probe.hide_render,
           "helper role: all Cycles rays off, hidden in renders")
    bpy.ops.hydrokit.set_type(typ='NONE')
    pruefe(all(getattr(probe, k) for k in strahlen) and not probe.hide_render
           and probe.display_type == 'TEXTURED', "role cleared: visible again")
    bpy.data.objects.remove(probe)
    bpy.context.view_layer.objects.active = cube

    # Compute device: Both lists every step, only those with a GPU version selectable
    d.rechengeraet = 'BEIDES'
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    stufen = ["geraet_mesh", "geraet_gischt", "geraet_partikel", "geraet_advektion", "geraet_druck"]
    pruefe(all(_aktiv(p, "prop", n) is True for n in stufen)
           and not any(k == "label" and "no GPU version yet" in n for k, n, _ in p),
           "compute device Both: all five steps selectable on their own")
    pruefe(_aktiv(p, "operator", "hydrokit.rechengeraet_messen") is not None
           and _stelle(p, "panel", "hk_sim_geraet") is not None,
           "compute device Both: 'Pick the Fastest per Step' button in its Per Step section")
    d.rechengeraet = 'CPU'
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(_aktiv(p, "prop", "geraet_mesh") is None and _stelle(p, "panel", "hk_sim_geraet") is None,
           "compute device CPU: no per-step list")
    d.rechengeraet = 'GPU'

    sim_ops.LINSE_LAEUFT = True
    bpy.context.scene.hydrokit.linse_fortschritt = 0.4
    try:
        p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
        pruefe(any(k == "label" and "40" in n for k, n, _ in p)
               and "40 %" in _texte(p)
               and _aktiv(p, "operator", "hydrokit.linse_berechnen") is None
               and _aktiv(p, "prop", "linse_fortschritt") is None,
               "sim panel while lens drops compute: progress in the header and instead of "
               "the button")
    finally:
        sim_ops.LINSE_LAEUFT = False

    # The FLIP Fluids tools are gone: the tab holds HyprW's own panel only
    im_tab = [c.__name__ for c in bpy.types.Panel.__subclasses__()
              if getattr(c, "bl_category", "") == "HyprW" and c.is_registered]
    alt = [n for n in ("HYDROKIT_PT_main", "HYDROKIT_OT_run_doctor", "HYDROKIT_OT_apply_preset",
                       "HYDROKIT_OT_apply_materials", "HYDROKIT_OT_create_mist")
           if hasattr(bpy.types, n)]
    pruefe(im_tab == ["HYDROKIT_PT_sim"] and not alt,
           f"HyprW tab holds only HyprW's own panel: {im_tab}, FLIP Fluids tools left: {alt}")


# ---------------------------------------------------- artificial cache
def _bobj(ecken):
    ecken = np.asarray(ecken, np.float32).reshape(-1, 3)
    return (struct.pack("<i", len(ecken)) + ecken.tobytes() + struct.pack("<i", 0))


def _cache_bauen(hk, linse, pfad, versatz, plan, frames):
    """plan: list of (art, x_glas, y_glas, kennung). Hits only in frame 1."""
    from importlib import import_module
    cache = import_module(hk.__name__ + ".sim.cache")
    scene = bpy.context.scene
    cam = scene.camera
    M, t_w, t_h, seiten = linse._kamera_daten(scene, cam)
    D = bpy.data.objects["Cube"].hydrokit.linse_durchmesser / 1000.0
    Mw = np.array(cam.matrix_world, np.float64)
    R = Mw[:3, :3]
    # Distance in front of the camera, within capture range; 3 m/s, so a hit does
    # not spread on impact (Laan 2014) as far as its neighbour in the 8 mm grid
    z = 0.45

    def welt(pc):
        return pc @ R.T + Mw[:3, 3] - versatz

    if os.path.isdir(pfad):
        shutil.rmtree(pfad)
    cache.info_schreiben(pfad, {"versatz": list(versatz), "gischt": True,
                                "frames": [frames[0], frames[-1]]})
    for f in frames:
        pos_w, vel_w, ken, art = [], [], [], []
        wasser = []
        for typ, x, y, k in plan:
            nx = x / (D / 2.0)
            ny = y * seiten / (D / 2.0)
            tiefe = z if f == frames[0] else 5.0        # later far away
            pc = np.array([nx * t_w * tiefe, ny * t_h * tiefe, -tiefe])
            if typ == "wasser":
                wasser.append(welt(pc[None])[0])
            else:
                pos_w.append(welt(pc[None])[0])
                vel_w.append(R @ np.array([0.0, 0.0, 3.0]))    # towards the camera
                ken.append(k)
                art.append(linse.SPRAY if typ == "spray" else linse.NEBEL)
        cache.mesh_schreiben(pfad, f, _bobj(wasser))
        n = len(ken)
        cache.partikel_schreiben(
            pfad, f,
            np.asarray(pos_w, np.float32).tobytes(),
            np.asarray(vel_w, np.float32).tobytes(),
            np.full(n, 1.0, np.float32).tobytes(),
            np.asarray(ken, np.uint8).tobytes(),
            np.asarray(art, np.uint8).tobytes())


def _erwartet(linse, d, plan, f):
    """Which hits must stick given the switches: set of (x, y) in mm
    and total volume."""
    behalten, vol = [], 0.0
    nr = -1                               # particle number in the frame (without water)
    th_a, th_r = (math.radians(w) for w in linse.GLAESER[d.linse_glas])
    v_krit = linse.kritisches_volumen(th_a, th_r, 1.0)
    for typ, x, y, k in plan:
        if typ != "wasser":
            nr += 1
        if typ == "nebel" or not getattr(d, f"linse_{typ}_haftet"):
            continue        # mist is a cloud: fine droplets, file of its own
        prozent = getattr(d, f"linse_{typ}_haftanteil")
        if typ == "wasser":
            cx, cy = round(x / 0.002), round(y / 0.002)
            ok = linse._haftet(prozent, linse.WASSER, np.array([cx]), np.array([cy]), f)[0]
            v = math.pi / 6.0 * 0.002 ** 3
        else:
            code = linse.SPRAY if typ == "spray" else linse.NEBEL
            ok = linse._haftet(prozent, code, np.array([nr]), f)[0]
            if typ == "spray":
                dm = linse._gamma_durchmesser(d.spray_groesse / 1000.0, 0.125e-3, 8.5e-3,
                                              np.array([k]))[0]
            else:
                dm = linse._gamma_durchmesser(d.nebel_groesse / 1000.0, 40e-6, 400e-6,
                                              np.array([k]))[0]
            v = math.pi / 6.0 * dm ** 3
        if ok:
            behalten.append((round(x * 1000), round(y * 1000)))
            vol += v
    return sorted(behalten), vol


def _nebel_zahl(pfad, f):
    datei = os.path.join(pfad, "linse", f"{f:06d}_nebel.npy")
    return len(np.load(datei)) if os.path.exists(datei) else 0


def _gelesen(pfad, f):
    daten = np.load(os.path.join(pfad, "linse", f"{f:06d}.npy"))
    x, y, r, h = (daten[:, i].astype(np.float64) for i in range(4))
    vol = float(np.sum(math.pi * h * (3 * r * r + h * h) / 6.0))
    return sorted(zip(np.round(x * 1000).astype(int).tolist(),
                      np.round(y * 1000).astype(int).tolist())), vol, daten


@abschnitt("Lens simulation with an artificial cache")
def linse_test(hk):
    from importlib import import_module
    linse = import_module(hk.__name__ + ".sim.linse")
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    scene = bpy.context.scene
    cam = scene.camera
    cam.location = (0.0, 0.0, 0.0)
    cam.rotation_euler = (math.radians(90.0), 0.0, 0.0)   # horizontal view
    bpy.context.view_layer.update()
    cube = bpy.data.objects["Cube"]
    d = cube.hydrokit
    d.typ = 'DOMAIN'
    pfad = os.path.join(ARBEIT, "cache_kuenstlich")
    d.cache_pfad = pfad
    frames = [1, 2, 3, 4]
    scene.frame_start, scene.frame_end = frames[0], frames[-1]
    versatz = np.array([1.0, 2.0, 3.0])

    # 8 mm grid: no two drops touch, each one stays on its own.
    # Spray only with IDs whose drops stay below the sliding limit.
    th_a, th_r = (math.radians(w) for w in linse.GLAESER[d.linse_glas])
    v_krit = linse.kritisches_volumen(th_a, th_r, 1.0)
    dm = linse._gamma_durchmesser(d.spray_groesse / 1000.0, 0.125e-3, 8.5e-3, np.arange(256))
    klein = [int(k) for k in np.flatnonzero(math.pi / 6.0 * dm ** 3 < 0.5 * v_krit)]
    plaetze = [(x / 1000.0, y / 1000.0) for y in range(-16, 17, 8) for x in range(-32, 33, 8)]
    plan = []
    for i, (x, y) in enumerate(plaetze):
        typ = ("wasser", "spray", "nebel")[i % 3]
        k = klein[(i * 7) % len(klein)] if typ == "spray" else (i * 37) % 256
        plan.append((typ, x, y, k))
    melden(f"{len(plan)} hits in frame 1 (15 each water/spray/mist), "
           f"critical volume vertical {v_krit*1e9:.2f} µl")
    _cache_bauen(hk, linse, pfad, versatz, plan, frames)

    # --- main switch off
    d.linse_an = False
    erg = linse.berechnen(scene)
    pruefe(erg == (0, 0) and not os.path.isdir(os.path.join(pfad, "linse")),
           f"main switch off: berechnen -> {erg}, no linse/ folder")
    pruefe(not bpy.ops.hydrokit.linse_berechnen.poll(),
           "main switch off: operator locked")

    # --- all combinations per type
    d.linse_an = True
    d.linse_spritz_rest = 100.0   # splashing changes no volume here
    pruefe(bpy.ops.hydrokit.linse_berechnen.poll(), "main switch on: operator available")

    def lauf(einst, beschreibung):
        for art in ("wasser", "spray", "nebel"):
            an, prozent = einst[art]
            setattr(d, f"linse_{art}_haftet", an)
            setattr(d, f"linse_{art}_haftanteil", prozent)
        n_frames, n_tropfen = linse.berechnen(scene)
        soll, soll_vol = _erwartet(linse, d, plan, 1)
        ist, ist_vol, _ = _gelesen(pfad, 1)
        gleich = ist == soll and math.isclose(ist_vol, soll_vol, rel_tol=1e-4, abs_tol=1e-15)
        pruefe(n_frames == 4 and gleich,
               f"{beschreibung}: {len(ist)} drops (expected {len(soll)}), "
               f"volume {ist_vol*1e9:.3f} µl (expected {soll_vol*1e9:.3f})")
        return len(ist)

    aus = (False, 100.0)
    for art in ("wasser", "spray", "nebel"):
        for prozent in (0.0, 50.0, 100.0):
            einst = {a: aus for a in ("wasser", "spray", "nebel")}
            einst[art] = (True, prozent)
            n = lauf(einst, f"only {art:6s} sticks, {prozent:5.1f} %")
            nebel = _nebel_zahl(pfad, 1)
            if art == "nebel":
                pruefe(n == 0, f"only mist, {prozent:.0f} %: no single drops "
                               f"({nebel} fine droplets)")
                if prozent == 0.0:
                    pruefe(nebel == 0, "only mist, 0 %: no fine droplets")
            elif prozent == 100.0:
                pruefe(n == 15, f"only {art}, 100 %: all 15 hits stick")
            if art != "nebel" and prozent == 0.0:
                pruefe(n == 0, f"only {art}, 0 %: no hit sticks")
        einst = {a: aus for a in ("wasser", "spray", "nebel")}
        einst[art] = (False, 100.0)
        lauf(einst, f"{art:6s} sticks OFF at 100 %")
    n = lauf({a: (True, 100.0) for a in ("wasser", "spray", "nebel")}, "all on, 100 %")
    pruefe(n == 30, "all on, 100 %: 30 drops (water + spray)")
    lauf({a: (True, 50.0) for a in ("wasser", "spray", "nebel")}, "all on, 50 %")

    # --- repeatability: computed twice the same -> byte for byte identical
    roh1 = [open(os.path.join(pfad, "linse", f"{f:06d}.npy"), "rb").read() for f in frames]
    linse.berechnen(scene)
    roh2 = [open(os.path.join(pfad, "linse", f"{f:06d}.npy"), "rb").read() for f in frames]
    pruefe(roh1 == roh2, "second run with the same settings byte for byte identical")

    # --- a camera that cannot move skips the frame changes (FLIP loads its
    # mesh on every frame_set); a keyed one must give the same drops
    pruefe(not linse._kamera_bewegt(cam), "static camera: no frame changes")
    cam.keyframe_insert("location", frame=scene.frame_start)
    try:
        pruefe(linse._kamera_bewegt(cam), "keyed camera: frame changes")
        linse.berechnen(scene)
        roh3 = [open(os.path.join(pfad, "linse", f"{f:06d}.npy"), "rb").read() for f in frames]
        pruefe(roh3 == roh1, "keyed (unmoved) camera: same drops as the static one")
    finally:
        cam.animation_data_clear()

    # --- display: object on the camera object, a mesh of 2 + rings*segments
    # points per drop (detail by size in the image, linse.stufe)
    px = linse.pixel_je_meter(scene, d)

    def punkte(r):
        S, K = linse.stufe(r * px)
        return 2 + S * K

    for f in frames:
        scene.frame_set(f)
        erg = anzeige.frame_laden(scene, f)
        _, _, daten = _gelesen(pfad, f)
        datei = os.path.join(pfad, "linse", f"{f:06d}_nebel.npy")
        nebel = np.load(datei) if os.path.exists(datei) else np.zeros((0, 4))
        soll = sum(punkte(r) for r in daten[:, 2]) + sum(punkte(r) for r in nebel[:, 2])
        obj = bpy.data.objects.get(linse.OBJEKT)
        pruefe(obj is not None and erg.get(linse.OBJEKT) == len(daten)
               and len(obj.data.vertices) == soll
               and obj.parent is cam,
               f"display frame {f}: {len(daten)} drops on the camera object")
    dg = bpy.context.evaluated_depsgraph_get()
    ausgewertet = obj.evaluated_get(dg)
    melden(f"mesh evaluated: {len(ausgewertet.data.vertices)} vertices "
           f"(look not judged)")
    d.linse_an = False
    anzeige.frame_laden(scene, 1)
    pruefe(len(obj.data.vertices) == 0, "main switch off: display emptied")

    # --- Operator
    d.linse_an = True
    d.linse_spritz_rest = 100.0   # splashing changes no volume here
    erg = bpy.ops.hydrokit.linse_berechnen()
    pruefe(erg == {'FINISHED'}, f"Operator hydrokit.linse_berechnen -> {erg}")


# ----------------------------------------------------------- real bake
@abschnitt("Load the engine and a small real bake")
def bake_test(hk):
    from importlib import import_module
    backen = import_module(hk.__name__ + ".sim.backen")
    linse = import_module(hk.__name__ + ".sim.linse")
    ffe = import_module(hk.__name__ + ".ffengine.ffengine")
    try:
        ffe.ffengine.CBindings_get_error_message
        melden(f"libffengine.so loaded: {ffe.ffengine._lib._name}")
    except Exception:
        tb = traceback.format_exc()
        FEHLER.append("engine does not load:\n" + tb)
        print(tb)
        return
    fehlt = backen.fehlende_erweiterungen()
    pruefe(not fehlt, f"engine extensions complete (missing: {fehlt or 'nothing'})")

    scene = bpy.context.scene
    cube = bpy.data.objects["Cube"]
    d = cube.hydrokit
    d.typ = 'DOMAIN'
    d.aufloesung = 24
    d.gischt = True
    d.cache_pfad = os.path.join(ARBEIT, "cache_bake")
    cube.location = (0.0, 0.0, 0.0)
    cube.scale = (1.0, 1.0, 1.0)
    # water sphere flying towards the camera; camera inside the domain
    bpy.ops.mesh.primitive_uv_sphere_add(radius=0.3, location=(0.0, -0.4, 0.0))
    kugel = bpy.context.active_object
    kugel.hydrokit.typ = 'FLUID'
    kugel.hydrokit.start_geschwindigkeit = (0.0, 8.0, 0.0)
    cam = scene.camera
    cam.location = (0.0, 0.7, 0.0)
    cam.rotation_euler = (math.radians(90.0), 0.0, math.radians(180.0))  # looking along -Y
    bpy.context.view_layer.update()
    scene.frame_start, scene.frame_end = 1, 12
    n, dauer, fehler = backen.backen(scene)
    pruefe(fehler is None and n == 12, f"bake: {n} frames in {dauer:.1f} s, error: {fehler}")
    d.linse_an = True
    d.linse_spritz_rest = 100.0   # splashing changes no volume here
    for a in ("wasser", "spray", "nebel"):
        setattr(d, f"linse_{a}_haftet", True)
        setattr(d, f"linse_{a}_haftanteil", 100.0)
    n_frames, n_tropfen = linse.berechnen(scene)
    pfad = os.path.join(d.cache_pfad, "linse")
    je_frame = [len(np.load(os.path.join(pfad, f"{f:06d}.npy"))) for f in range(1, 13)]
    melden(f"lens on real cache: {n_frames} frames, drops per frame {je_frame}")
    pruefe(n_frames == 12, "lens runs through on the real cache")
    d.linse_wasser_haftanteil = 0.0
    linse.berechnen(scene)
    je_frame0 = [len(np.load(os.path.join(pfad, f"{f:06d}.npy"))) for f in range(1, 13)]
    melden(f"the same with water 0 %: drops per frame {je_frame0}")
    marke = os.path.join(pfad, linse.FERTIG)
    aktuell = linse.ist_aktuell(scene)
    os.remove(marke)                         # as after a run cancelled half way
    pruefe(aktuell and not linse.ist_aktuell(scene),
           "lens drops count as current only after a complete run")
    open(marke, "w").close()
    aussehen = import_module(hk.__name__ + ".sim.aussehen")
    anzeige_m = import_module(hk.__name__ + ".sim.anzeige")
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")
    sim_ops = import_module(hk.__name__ + ".operators.sim_ops")
    scene.frame_set(6)
    anzeige_m.frame_laden(scene)

    # --- after a bake the whitewater gets its look by itself (the bake
    # operator calls nach_bake; backen.backen() here does not)
    aussehen.nach_bake(bpy.context)
    gischt_obj = [o for o in map(bpy.data.objects.get, aussehen.GISCHT_OBJEKTE) if o is not None]
    flaeche = bpy.data.objects[anzeige_m.OBERFLAECHE]
    pruefe(gischt_obj and all(any(m.type == 'NODES' for m in o.modifiers) for o in gischt_obj)
           and len(flaeche.data.materials) and flaeche.data.materials[0] is not None
           and aussehen.look_stand(bpy.context)[0] == 'ok',
           f"after the bake: look on {len(gischt_obj)} whitewater objects and the water")
    for m in [m for m in gischt_obj[0].modifiers if m.type == 'NODES']:
        gischt_obj[0].modifiers.remove(m)
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    pruefe(aussehen.look_stand(bpy.context)[0] == 'fehlt'
           and any("no look yet" in t for t in _texte(p)),
           "whitewater without its look: the panel says it renders black")
    flaeche.data.materials[0].node_tree.nodes.new("NodeFrame").name = "hk_test_marke"
    aussehen.nach_bake(bpy.context)
    pruefe(any(m.type == 'NODES' for m in gischt_obj[0].modifiers)
           and "hk_test_marke" in flaeche.data.materials[0].node_tree.nodes,
           "the next bake gives it back; the water chosen before stays as it is")
    dx = cube[aussehen.LOOK_DX]
    cube[aussehen.LOOK_DX] = dx * 1.5
    stand, text = aussehen.look_stand(bpy.context)
    cube[aussehen.LOOK_DX] = dx
    pruefe(stand == 'veraltet' and aussehen.look_stand(bpy.context)[0] == 'ok',
           f"whitewater sized for other cells is noticed: '{text}'")
    # A whitewater type not on the loaded frame gets its look right away too
    spray = bpy.data.objects.get("HK_Spray")
    if spray is not None:
        bpy.data.objects.remove(spray)
    aussehen.nach_bake(bpy.context)
    spray = bpy.data.objects.get("HK_Spray")
    pruefe(spray is not None and any(m.type == 'NODES' for m in spray.modifiers),
           "a whitewater type missing on the loaded frame gets its look after the bake")
    anzeige_m.frame_laden(scene)

    # The update function of the look sliders swallows errors; so call it directly
    erledigt = aussehen.anwenden(bpy.context)
    melden(f"aussehen.anwenden on real cache: {erledigt}")

    # --- amount sliders act while dragging, without a frame change
    anzeige_m.frame_laden(scene)
    mengen = {"HK_Schaum": "menge_foam", "HK_Spray": "menge_spray", "HK_Blasen": "menge_bubble"}
    zahlen = {n: len(bpy.data.objects[n].data.vertices) for n in mengen if n in bpy.data.objects}
    name = max(zahlen, key=zahlen.get) if zahlen else None
    if name is None or zahlen[name] < 20:
        pruefe(False, f"whitewater to try the amount sliders on: {zahlen}")
    else:
        setattr(d, mengen[name], 0.5)
        halb = len(bpy.data.objects[name].data.vertices)
        setattr(d, mengen[name], 1.0)
        zurueck = len(bpy.data.objects[name].data.vertices)
        pruefe(0 < halb < zahlen[name] and zurueck == zahlen[name],
               f"amount slider acts at once: {name} {zahlen[name]} -> {halb} at 50 % "
               f"-> {zurueck} at 100 %")

    # --- drawn size spread and velocity for motion blur
    tab = anzeige_m._groessen_tabellen(3.0)
    pruefe(abs(tab[2].mean() - 1.0) < 0.05 and tab[2].min() < 0.2 and tab[2].max() > 2.0,
           f"spray size factor = engine diameter / d50: mean {tab[2].mean():.2f}, "
           f"{tab[2].min():.2f} .. {tab[2].max():.2f}")
    pruefe(abs(tab[0].mean() - 1.0) < 1e-6 and np.median(tab[0]) < 1.0,
           "bubble sizes: mean 1, mostly small (Deane & Stokes spectrum)")
    halb = anzeige_m._ausduennen_kennung(np.arange(256)) < 0.5 * 255.0
    pruefe(abs(tab[2][halb].mean() - tab[2].mean()) < 0.1,
           f"amount 50 %: spray sizes not biased (kept mean {tab[2][halb].mean():.2f})")
    sp_obj = bpy.data.objects.get("HK_Spray") or bpy.data.objects.get("HK_Schaum")
    namen = {a.name for a in sp_obj.data.attributes} if sp_obj else set()
    pruefe({"velocity", "hk_groesse"} <= namen,
           f"whitewater points carry velocity and size ({sp_obj.name if sp_obj else None})")

    # --- foam bubble texture and spray tint
    def knoten(name, typ):
        mat = bpy.data.materials.get(name)
        return [n for n in mat.node_tree.nodes if n.type == typ] if mat else None
    pruefe(len(knoten("HK Foam", 'TEX_VORONOI') or []) == 3,
           "foam with bubble texture (3 Voronoi networks)")
    # foam as aerated water: 20 % air, 1 mm bubbles. Through the centre of a
    # particle 13 mm across: 15 % straight, 30 % reflected, 55 % diffusely
    # through, a little red absorbed, nothing created
    zurueck, diffus, gerade = aussehen.schaum_schicht(aussehen.schaum_optik(0.2), 0.0128)
    summe = [r + t + g for r, t, g in zip(zurueck, diffus, gerade)]
    pruefe(abs(gerade[1] - 0.147) < 0.002 and abs(zurueck[1] - 0.301) < 0.002
           and abs(diffus[1] - 0.551) < 0.002 and all(0.99 < x <= 1.0 for x in summe)
           and summe[0] < summe[2],
           f"foam optics: straight {gerade[1]:.3f}, reflected {zurueck[1]:.3f}, "
           f"diffuse {diffus[1]:.3f}, sums {tuple(round(x, 4) for x in summe)}")
    # No subsurface: Cycles renders it black on points on the GPU. The
    # scattering is evaluated in the shader from each point's radius.
    typen = {n.type for n in bpy.data.materials["HK Foam"].node_tree.nodes}
    pruefe({'POINT_INFO', 'BSDF_DIFFUSE', 'BSDF_TRANSLUCENT', 'BSDF_TRANSPARENT'} <= typen
           and 'BSDF_PRINCIPLED' not in typen,
           f"foam material scatters in the shader, no subsurface ({len(typen)} node types)")
    # water from measured optics: visibility 3 m -> attenuation 4.8 / 3 = 1.6 /m,
    # of which pure water plus dissolved matter absorb 0.08 at 550 nm
    mo = import_module(hk.__name__ + ".operators.materials_ops")
    anzeige_mod = import_module(hk.__name__ + ".sim.anzeige")
    absorption, streuung = mo.wasser_optik(3.0, 0.15)
    pruefe(abs(absorption[1] + streuung - 1.6) < 1e-9 and absorption[0] > absorption[2] > absorption[1],
           f"water optics: absorption {tuple(round(x, 4) for x in absorption)}, scattering {streuung:.3f}")
    sh = bpy.context.scene.hydrokit
    bpy.context.scene.cycles.volume_bounces = 0
    wasser_obj = bpy.data.objects[anzeige_mod.OBERFLAECHE]
    vorher = wasser_obj.data.materials[0]
    sh.wasser_variante = 'KLAR'              # the variant fills the measured sliders
    pruefe(abs(sh.wasser_sichtweite - 10.75) < 1e-6 and wasser_obj.data.materials[0] == vorher,
           f"choosing a water only fills the sliders ({sh.wasser_sichtweite} m); "
           f"the water stays {vorher.name} until Apply Water")

    # Apply Water: only with the water selected, and only the water body.
    # Marker nodes in the whitewater materials and node groups would vanish
    # with a rebuild.
    def gischt_baeume():
        for name in ("HK Foam", "HK Bubbles", "HK Spray", "HK Mist", "HK Nebel"):
            mat, gruppe = bpy.data.materials.get(name), bpy.data.node_groups.get(name)
            if mat is not None:
                yield mat.node_tree
            if gruppe is not None:
                yield gruppe
    for baum in gischt_baeume():
        baum.nodes.new("NodeFrame").name = "hk_test_marke"
    for o in bpy.context.view_layer.objects:
        o.select_set(False)
    bpy.context.view_layer.objects.active = kugel
    ohne = bpy.ops.hydrokit.sim_water.poll()
    hinweis = any("Click the water" in t for t in _texte(zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)))
    cube.select_set(True)                    # the domain does not count any more
    bpy.context.view_layer.objects.active = cube
    pruefe(not ohne and hinweis and not bpy.ops.hydrokit.sim_water.poll(),
           "Apply Water greyed out without the water selected, also with the domain; "
           "the panel says to click the water")
    cube.select_set(False)
    wasser_obj.select_set(True)
    bpy.context.view_layer.objects.active = wasser_obj
    erg = bpy.ops.hydrokit.sim_water()
    wasser = wasser_obj.data.materials[0]
    streu = [n for n in wasser.node_tree.nodes if n.type == 'VOLUME_SCATTER']
    pruefe(erg == {'FINISHED'} and wasser.name == "HK Wasser Klar"
           and streu and abs(streu[0].inputs["Anisotropy"].default_value - 0.924) < 1e-6
           and bpy.context.scene.cycles.volume_bounces >= 16,
           f"Apply Water with the water selected: {wasser.name}, "
           f"{bpy.context.scene.cycles.volume_bounces} volume bounces")
    marken = ["hk_test_marke" in baum.nodes for baum in gischt_baeume()]
    pruefe(len(marken) >= 4 and all(marken),
           f"Apply Water leaves foam, bubbles and spray as they are ({len(marken)} node trees)")
    # The panel says which water is applied, and when a slider has moved since
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    gut = "Applied: Clear" in _texte(p) and any("deep water" in t for t in _texte(p))
    sh.wasser_sichtweite += 1.0
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    sh.wasser_sichtweite -= 1.0
    pruefe(gut and any("Not applied yet" in t and "Visibility" in t for t in _texte(p)),
           "water section: 'Applied: Clear', 'Not applied yet: Visibility' after a slider moved")

    sh.wasser_variante = 'GEBIRGSBACH'
    wasser.node_tree.nodes.new("NodeFrame").name = "hk_test_marke"
    d.spray_farbe = (0.5, 0.7, 1.0)          # update re-applies the whitewater look
    sp = knoten("HK Spray", 'BSDF_PRINCIPLED')
    pruefe(sp and tuple(round(x, 3) for x in sp[0].inputs["Base Color"].default_value[:3])
           == (0.5, 0.7, 1.0), "spray colour reaches the material")
    pruefe(wasser_obj.data.materials[0] == wasser and "hk_test_marke" in wasser.node_tree.nodes,
           "whitewater sliders leave the water body as it is")
    d.schaum_blasen = 0
    pruefe(knoten("HK Foam", 'TEX_VORONOI') == [], "foam bubbles 0: plain foam")
    d.schaum_blasen = 4
    schaum_ng = bpy.data.node_groups.get("HK Foam")
    pruefe(schaum_ng is not None and any(n.bl_idname == "GeometryNodeStoreNamedAttribute"
                                         for n in schaum_ng.nodes),
           "points store their centre (texture travels with the particle)")

    # --- foam, spray, mist and lens drops are click-through, so clicks reach the
    # objects inside the domain; the water stays clickable for Apply Water
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    da = [bpy.data.objects[n] for n in anzeige.ERGEBNISSE
          if n in bpy.data.objects and n != anzeige.OBERFLAECHE]
    pruefe(len(da) >= 2 and all(o.hide_select for o in da) and not wasser_obj.hide_select,
           f"results click-through by default ({len(da)} objects), the water clickable")
    scene.hydrokit.ergebnisse_waehlbar = True
    pruefe(not any(o.hide_select for o in da), "Selectable Results on: all selectable")
    scene.hydrokit.ergebnisse_waehlbar = False
    pruefe(all(o.hide_select for o in da) and not wasser_obj.hide_select,
           "Selectable Results off again: click-through, the water still clickable")
    # A file saved with a click-through water gets a clickable one when opened
    wasser_obj.hide_select = True
    anzeige._nach_laden(None)
    pruefe(not wasser_obj.hide_select, "opening a file: the water clickable again")

    # A frame without whitewater (past the end of a cancelled bake): no frozen
    # foam from the frame before
    gischt_da = [o for o in map(bpy.data.objects.get, aussehen.GISCHT_OBJEKTE) if o is not None]
    vorher = sum(len(o.data.vertices) for o in gischt_da)
    anzeige.frame_laden(scene, frame=40)
    nachher = sum(len(o.data.vertices) for o in gischt_da)
    anzeige.frame_laden(scene)
    pruefe(vorher > 0 and nachher == 0,
           f"a frame without whitewater shows none ({vorher} -> {nachher} points)")

    # A material of the user's own on the water survives opening the file
    schaum = bpy.data.materials.get("HK Foam")
    eigenes = bpy.data.materials.new("Mein Wasser")
    alt_mat = wasser_obj.data.materials[0]
    wasser_obj.data.materials[0] = eigenes
    if schaum is not None:
        schaum[aussehen.STAND_KEY] = 0          # an older whitewater look
    anzeige._nach_laden(None)
    schaum = bpy.data.materials.get("HK Foam")
    pruefe(wasser_obj.data.materials[0] == eigenes and schaum is not None
           and schaum.get(aussehen.STAND_KEY) == aussehen.MATERIAL_STAND,
           "opening a file renews an older whitewater look, the user's own water stays")
    wasser_obj.data.materials[0] = alt_mat

    # Clear Cache: the deleted bake leaves the viewport and the renders
    cache = import_module(hk.__name__ + ".sim.cache")
    bpy.ops.hydrokit.sim_clear()
    reste = sum(len(bpy.data.objects[n].data.vertices) for n in anzeige.ERGEBNISSE
                if n in bpy.data.objects)
    pruefe(reste == 0 and aussehen.look_stand(bpy.context)[0] is None
           and not cache.gebackene_frames(cache.pfad_aufloesen(d.cache_pfad)),
           f"Clear Cache empties water, whitewater and lens drops ({reste} vertices left); "
           f"the look says nothing is baked")

    # --- role settings reach the engine: every setter once, non-default values
    d.linse_an = False
    d.nebel_an = True
    d.sheeting = True
    d.cache_pfad = os.path.join(ARBEIT, "cache_rollen")
    bpy.ops.mesh.primitive_plane_add(size=1.6, location=(0.0, 0.0, -0.6))
    boden = bpy.context.active_object
    h = boden.hydrokit
    h.typ = 'OBSTACLE'
    h.hindernis_reibung, h.hindernis_gischt, h.hindernis_nebel = 0.5, 2.0, 0.5
    h.hindernis_sheeting, h.hindernis_ausdehnung = 1.5, 0.001
    bpy.ops.mesh.primitive_cube_add(size=1.9, location=(0.0, 0.0, 0.0))
    huelle = bpy.context.active_object
    huelle.hydrokit.typ = 'OBSTACLE'
    huelle.hydrokit.hindernis_umkehren = True
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0.0, 0.3, 0.6))
    zu = bpy.context.active_object
    zu.hydrokit.typ = 'INFLOW'
    zu.hydrokit.zufluss_geschwindigkeit = (0.0, 0.0, -2.0)
    zu.hydrokit.zufluss_substeps = 2
    bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0.0, -0.6, -0.5))
    ab = bpy.context.active_object
    ab.hydrokit.typ = 'OUTFLOW'
    ab.hydrokit.abfluss_gischt = False
    ab.hydrokit.abfluss_umkehren = False
    ab.hydrokit.abfluss_allmaehlich = True
    ab.hydrokit.abfluss_rate = 4.0
    kugel.hydrokit.rolle_aktiv = False
    bpy.context.view_layer.update()
    scene.frame_start, scene.frame_end = 1, 4
    n, dauer, fehler = backen.backen(scene)
    pruefe(fehler is None and n == 4,
           f"bake with all role settings: {n} frames in {dauer:.1f} s, error: {fehler}")
    import json
    with open(os.path.join(d.cache_pfad, "info.json")) as datei:
        info = json.load(datei)
    pruefe(kugel.name not in info.get("wasser", []) and boden.name in info.get("hindernisse", []),
           "switched-off water left out of the bake, obstacles in it")

    # --- GPU surface mesh: every field also on the CPU, compared bit for bit
    gpu = import_module(hk.__name__ + ".ffengine.gpu")
    if not gpu.is_supported():
        pruefe(False, "engine without GPU support (HKGPU_is_available missing)")
    elif not gpu.is_available():
        melden(f"no GPU here ({gpu.status_message()}): GPU mesh check skipped")
        d.rechengeraet = 'GPU'
        n, dauer, fehler = backen.backen(scene)
        pruefe(fehler is None and n == 4, "GPU asked for without a GPU: bakes on the CPU")
    else:
        d.rechengeraet = 'GPU'
        gpu.set_verify(True)
        try:
            n, dauer, fehler = backen.backen(scene)
        finally:
            gpu.set_verify(False)
        schritte = (("mesh", "surface mesh"), ("curvature", "whitewater curvature"),
                    ("water", "water level set"), ("advection", "advection"),
                    ("pressure", "pressure solve"))
        for schritt, name in schritte:
            auf_gpu, auf_cpu = gpu.counts(schritt)
            falsch, verglichen = gpu.verify_counts(schritt)
            # the mesh runs every frame; the others only once there is water
            mindestens = 4 if schritt == "mesh" else 1
            pruefe(fehler is None and n == 4 and auf_gpu >= mindestens and auf_cpu == 0,
                   f"{name} on the GPU ({gpu.device_name()}): {auf_gpu} calls, {auf_cpu} on the CPU")
            if schritt == "advection":
                abweichung = gpu.verify_max_diff(schritt)
                pruefe(verglichen > 0 and falsch == 0 and abweichung < 1e-4,
                       f"{name} equal to the CPU up to rounding: largest difference {abweichung:.2g} m/s")
            elif schritt == "pressure":
                pruefe(verglichen > 0 and falsch == 0,
                       f"{name} equal to the CPU: {falsch} of {verglichen} values beyond 1e-6, "
                       f"relative difference {gpu.verify_max_diff(schritt):.2g}")
            else:
                pruefe(verglichen > 0 and falsch == 0,
                       f"{name} bit-identical to the CPU: {falsch} of {verglichen} values differ")
        d.rechengeraet = 'BEIDES'
        d.geraet_mesh, d.geraet_gischt, d.geraet_druck = 'GPU', 'CPU', 'CPU'
        backen.backen(scene)
        pruefe(gpu.counts("mesh")[0] >= 4 and gpu.counts("curvature") == (0, 0)
               and gpu.counts("pressure") == (0, 0) and gpu.counts("advection")[0] >= 1,
               "Both: mesh and advection on the GPU, whitewater and pressure on the CPU as chosen")
        d.geraet_gischt, d.geraet_druck = 'GPU', 'GPU'
        d.rechengeraet = 'CPU'
        backen.backen(scene)
        pruefe(all(gpu.counts(k) == (0, 0) for k, _ in schritte),
               "compute device CPU: nothing on the GPU")

        # Pick the fastest per step: probe bake of a copy in another Blender
        import json
        import time
        start = time.time()
        erg = bpy.ops.hydrokit.rechengeraet_messen()
        try:
            gemessen = json.loads(d.rechengeraet_messung).get("steps", {})
        except ValueError:
            gemessen = {}
        pruefe(erg == {'FINISHED'} and d.rechengeraet == 'BEIDES' and len(gemessen) == 5
               and all(getattr(d, "geraet_" + k) in ('CPU', 'GPU') for k in gemessen),
               f"fastest per step measured in {time.time() - start:.0f} s: "
               + ", ".join(f"{k} {getattr(d, 'geraet_' + k)}" for k in gemessen))
        pruefe(all(w["gpu_calls"] > 0 and w["cpu_fallbacks"] == 0 and w["differing"] == 0
                   for k, w in gemessen.items() if k not in ("advektion", "druck")),
               "probe: bit-identical steps agreed in the probe too")
        d.rechengeraet = 'GPU'


# ------------------------------------------------------ water mesh detail
@abschnitt("Water mesh detail: viewport and render")
def netz_detail_test(hk):
    """Lighter Viewport on, detail 2, viewport 1: the bake also writes a
    lighter mesh; the viewport shows it, renders the full one. Switched off,
    one mesh for both."""
    from importlib import import_module
    backen = import_module(hk.__name__ + ".sim.backen")
    cache = import_module(hk.__name__ + ".sim.cache")
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    scene = bpy.context.scene
    d = bpy.data.objects["Cube"].hydrokit
    d.oberflaechen_unterteilung = 0
    pruefe(d.oberflaechen_unterteilung == 1, "mesh detail 0 is not possible (the engine refuses it)")
    d.vorschau_im_viewport = False
    d.oberflaechen_unterteilung, d.unterteilung_viewport, d.viewport_eigenes_netz = 2, 1, True
    d.cache_pfad = os.path.join(ARBEIT, "cache_netz")
    pfad = cache.pfad_aufloesen(d.cache_pfad)
    scene.frame_start, scene.frame_end = 1, 4
    n, dauer, fehler = backen.backen(scene)
    voll = cache.mesh_lesen_np(pfad, 4)
    leicht = cache.mesh_lesen_np(pfad, 4, "vorschau")
    n_voll = len(voll[1]) // 3 if voll else 0
    n_leicht = len(leicht[1]) // 3 if leicht else 0
    pruefe(fehler is None and 0 < n_leicht < n_voll,
           f"Lighter Viewport 1 at detail 2: lighter viewport mesh written "
           f"({n_leicht} instead of {n_voll} triangles)")

    info = cache.info_lesen(pfad) or {}
    pruefe(info.get("netz") == {"detail": 2, "vorschau": "leicht", "viewport": 1}
           and info.get("dauer", 0) > 0,
           f"the cache records which second mesh it holds: {info.get('netz')}, "
           f"and how long the bake took: {info.get('dauer')} s")

    # The bake section lists what the next bake would change
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    gut = not any(t.startswith("Changed since") for t in _texte(p))
    d.oberflaechen_unterteilung = 3
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    texte = _texte(p)
    gut = (gut and any("Mesh Detail 2 → 3" in t for t in texte)
           and "Baked with Mesh Detail 2" in texte
           and _aktiv(p, "operator", "hydrokit.sim_bake") is True)
    d.oberflaechen_unterteilung = 2
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    gut = (gut and not any("→" in t for t in _texte(p))
           and "Baked with Mesh Detail 2" not in _texte(p))
    pruefe(gut, "bake status: Mesh Detail 2 → 3 listed as changed since the bake, "
                "gone once it matches again")

    wasser = bpy.data.objects[anzeige.OBERFLAECHE]

    def dreiecke(rendern=False):
        anzeige._rendert = rendern
        try:
            anzeige.frame_laden(scene, 4)
        finally:
            anzeige._rendert = False
        return len(wasser.data.polygons)
    im_viewport, beim_rendern = dreiecke(), dreiecke(rendern=True)
    d.unterteilung_viewport = 2          # not below the mesh detail: the full one
    gleich = dreiecke()
    d.unterteilung_viewport, d.vorschau_im_viewport = 1, True
    grob = dreiecke()                    # Coarse Preview wanted, the cache holds the lighter one
    d.vorschau_im_viewport = False
    pruefe(n_leicht and im_viewport == n_leicht and beim_rendern == gleich == grob == n_voll,
           f"viewport shows the lighter mesh ({im_viewport}); renders, viewport 2 and a Coarse "
           f"Preview the cache does not hold the full one ({beim_rendern}, {gleich}, {grob})")

    # A real render without the display (reopened file, command line, lens
    # drops computing): the render handlers come with the add-on
    gezaehlt = []

    def zaehlen(sc, depsgraph=None):
        gezaehlt.append(len(wasser.data.polygons))
    anzeige.handler_aus()
    scene.frame_set(4)
    anzeige.frame_laden(scene, 4)
    r = scene.render
    r.engine, r.resolution_x, r.resolution_y, r.resolution_percentage = 'CYCLES', 16, 16, 100
    scene.cycles.samples, scene.cycles.device = 1, 'CPU'
    bpy.app.handlers.render_post.append(zaehlen)
    try:
        bpy.ops.render.render()
    finally:
        bpy.app.handlers.render_post.remove(zaehlen)
    pruefe(gezaehlt == [n_voll] and len(wasser.data.polygons) == n_leicht
           and not anzeige.handler_laeuft(),
           f"render without the display renders the full mesh ({gezaehlt} of {n_voll}), "
           f"the viewport gets the lighter one back ({len(wasser.data.polygons)})")

    # Opening a file with a bake: the viewport follows the timeline again,
    # and the bake section says whether it does
    sim_ops = import_module(hk.__name__ + ".operators.sim_ops")
    pruefe(anzeige.nach_laden_anzeigen(scene) and anzeige.handler_laeuft(),
           "after opening a file with a bake the viewport follows the timeline")
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    gut = "Viewport follows the timeline" in _texte(p)
    anzeige.handler_aus()
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    gut = gut and any("does not follow" in t for t in _texte(p))
    sim_ops.LINSE_LAEUFT = True
    try:
        p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
        gut = gut and any("paused" in t for t in _texte(p))
    finally:
        sim_ops.LINSE_LAEUFT = False
    pruefe(gut, "bake section: viewport follows / does not follow / paused for the lens drops")

    # In pieces like FLIP Fluids' Auto mode: the same water, the lighter mesh too
    stueck = backen.NETZ_ZELLEN_PRO_STUECK
    backen.NETZ_ZELLEN_PRO_STUECK = 2000
    try:
        n, dauer, fehler = backen.backen(scene)
    finally:
        backen.NETZ_ZELLEN_PRO_STUECK = stueck
    voll2 = cache.mesh_lesen_np(pfad, 4)
    leicht2 = cache.mesh_lesen_np(pfad, 4, "vorschau")
    n_voll2 = len(voll2[1]) // 3 if voll2 else 0
    n_leicht2 = len(leicht2[1]) // 3 if leicht2 else 0
    pruefe(fehler is None and n_voll2 == n_voll and abs(n_leicht2 - n_leicht) <= 0.02 * n_leicht,
           f"mesh in 8 pieces: {n_voll2} triangles as in one piece, lighter mesh {n_leicht2} "
           f"(one piece {n_leicht})")

    # Coarse Preview with resolution 0 means no second mesh, whatever the
    # greyed-out Lighter Viewport says; and Lighter Viewport off writes none
    for grob_an, aufl, leicht_an, text in ((True, 0, True, "Coarse Preview at 0"),
                                          (False, 60, False, "Lighter Viewport off")):
        d.vorschau_im_viewport, d.vorschau_aufloesung, d.viewport_eigenes_netz = grob_an, aufl, leicht_an
        n, dauer, fehler = backen.backen(scene)
        pruefe(fehler is None and cache.mesh_lesen_np(pfad, 4) is not None
               and cache.mesh_lesen_np(pfad, 4, "vorschau") is None
               and (cache.info_lesen(pfad) or {}).get("netz", {}).get("vorschau", "?") is None,
               f"{text}: no second mesh written")

    # The GPU computes the same: field, viewport mesh field, marching cubes
    # and smoothing of both meshes, compared bit for bit in verify mode
    gpu = import_module(hk.__name__ + ".ffengine.gpu")
    if gpu.is_supported() and gpu.is_available():
        d.vorschau_im_viewport, d.viewport_eigenes_netz, d.unterteilung_viewport = False, True, 1
        d.rechengeraet = 'GPU'
        gpu.set_verify(True)
        try:
            n, dauer, fehler = backen.backen(scene)
        finally:
            gpu.set_verify(False)
        falsch, verglichen = gpu.verify_counts("mesh")
        auf_gpu, auf_cpu = gpu.counts("mesh")
        pruefe(fehler is None and verglichen > 0 and falsch == 0 and auf_cpu == 0 and auf_gpu >= 3 * n,
               f"mesh with the viewport mesh on the GPU: {auf_gpu} calls, {auf_cpu} on the CPU, "
               f"{falsch} of {verglichen} values differ")
    else:
        melden("no GPU here: the GPU check of the viewport mesh is skipped")
    d.vorschau_im_viewport, d.vorschau_aufloesung, d.viewport_eigenes_netz = False, 60, False


# ------------------------------------------------- outdated materials
def material_stand_test(hk):
    """Materials carry the version of the look; older ones are rebuilt when
    a file is opened (once: foam with subsurface, black on the GPU)."""
    from importlib import import_module
    aussehen = import_module(hk.__name__ + ".sim.aussehen")
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    schaum = bpy.data.materials.get(aussehen.PREFIX + "Foam")
    if schaum is None:
        pruefe(False, "foam material exists for the version test")
        return
    pruefe(schaum.get(aussehen.STAND_KEY, 0) == aussehen.MATERIAL_STAND
           and not aussehen.materialien_veraltet(),
           f"generated materials carry the look version {aussehen.MATERIAL_STAND}")
    schaum[aussehen.STAND_KEY] = 1
    # A file from an older HyprW: its water is older too (a current water or
    # one of the user's own stays as it is, see bake_test)
    flaeche = bpy.data.objects.get(anzeige.OBERFLAECHE)
    wasser = next((m for m in flaeche.data.materials if m is not None), None) if flaeche else None
    if wasser is not None:
        wasser[aussehen.STAND_KEY] = 1
    bpy.context.scene.cycles.volume_bounces = 0
    anzeige._nach_laden(None)
    typen = {n.type for n in schaum.node_tree.nodes}
    pruefe(schaum.get(aussehen.STAND_KEY, 0) == aussehen.MATERIAL_STAND
           and 'BSDF_PRINCIPLED' not in typen
           and (wasser is None or bpy.context.scene.cycles.volume_bounces >= 16),
           "an outdated look is rebuilt when a file is opened")


# ------------------------------------------------------- studio water
def studio_wasser_test(hk):
    """Light through the surface, caustics, ripples and dispersion."""
    from importlib import import_module
    mo = import_module(hk.__name__ + ".operators.materials_ops")
    mat = mo.baue_wasser('KLAR', 0.035)
    typen = [n.type for n in mat.node_tree.nodes]
    pruefe('LIGHT_PATH' in typen and 'BSDF_TRANSPARENT' in typen and typen.count('TEX_NOISE') == 2
           and len(mat.node_tree.animation_data.drivers) == 2,
           "studio water: light through the surface, caustics and ripples moving with time")
    mat = mo.baue_wasser('KLAR', 0.035, schatten=0.0, dispersion=True)
    glas = sorted(round(n.inputs["IOR"].default_value, 5)
                  for n in mat.node_tree.nodes if n.type == 'BSDF_GLASS')
    pruefe(glas == [1.33192, 1.33468, 1.33867]
           and 'LIGHT_PATH' not in {n.type for n in mat.node_tree.nodes},
           f"dispersion: one glass per colour with water's measured index {glas}")


# ------------------------------------------------- adaptive particle sizes
def adaptiv_test(hk):
    """Pool baked with and without adaptive particle sizes: particles merge,
    the volume in standard particles and the mesh volume stay the same."""
    from importlib import import_module
    backen = import_module(hk.__name__ + ".sim.backen")
    if not backen.engine_erweiterungen().get("adaptiv"):
        pruefe(False, "engine without adaptive particle sizes")
        return

    bpy.ops.scene.new(type='EMPTY')
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 30

    def kiste(name, loc, scale, typ):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        o = bpy.context.active_object
        o.name, o.scale = name, scale
        o.hydrokit.typ = typ
        return o

    d = kiste("Pool Domain", (0.0, 0.0, 1.0), (1.0, 0.5, 1.0), 'DOMAIN').hydrokit
    d.aufloesung = 40
    kiste("Pool Water", (0.0, 0.0, 0.6), (1.0, 0.5, 0.6), 'FLUID')
    bpy.context.view_layer.update()

    def volumen(daten):
        nv = int(np.frombuffer(daten, np.int32, 1, 0)[0])
        v = np.frombuffer(daten, np.float32, nv * 3, 4).reshape(-1, 3).astype(np.float64)
        nt = int(np.frombuffer(daten, np.int32, 1, 4 + nv * 12)[0])
        t = np.frombuffer(daten, np.int32, nt * 3, 8 + nv * 12).reshape(-1, 3)
        return float(np.einsum("ij,ij->i", v[t[:, 0]], np.cross(v[t[:, 1]], v[t[:, 2]])).sum() / 6.0)

    ergebnis = {}
    for an in (False, True):
        d.adaptiv_an = an
        d.cache_pfad = os.path.join(ARBEIT, f"cache_adaptiv_{int(an)}")
        b = backen.Backer(scene)
        if not b.vorbereiten():
            pruefe(False, f"adaptive test bake prepared: {b.fehler}")
            return
        while b.schritt():
            pass
        stufen = b.sim.get_adaptive_particle_level_counts()
        ergebnis[an] = (stufen, sum(n * 2 ** L for L, n in enumerate(stufen)),
                        volumen(b.sim.get_surface_data()))
        b.aufraeumen()

    (_, einheiten0, vol0), (stufen1, einheiten1, vol1) = ergebnis[False], ergebnis[True]
    melden(f"adaptive pool: levels {stufen1}, {sum(stufen1)} instead of {einheiten0} particles")
    pruefe(sum(stufen1[1:]) > 0 and sum(stufen1) < einheiten0,
           f"deep water merged: {sum(stufen1[1:])} merged particles, {sum(stufen1)} in total")
    # Merging and splitting keep the volume exactly; the few particles the
    # engine removes at the walls differ between the two runs
    pruefe(abs(einheiten1 - einheiten0) <= 0.001 * einheiten0,
           f"volume kept: {einheiten1} standard particles with, {einheiten0} without")
    pruefe(abs(vol1 - vol0) < 0.01 * vol0,
           f"same water body: mesh volume {vol1:.4f} m3 with, {vol0:.4f} m3 without")


# ---------------------------------------------------------------- Run
def main():
    bpy.ops.wm.read_factory_settings(use_empty=False)
    hk = installieren()
    if hk is None or not hasattr(bpy.types.Scene, "hydrokit"):
        print("\nadd-on not enabled — aborting")
    else:
        properties(hk)
        attrappe_selbsttest()
        panel(hk)
        linse_test(hk)
        bake_test(hk)
        netz_detail_test(hk)
        material_stand_test(hk)
        studio_wasser_test(hk)
        adaptiv_test(hk)
        try:
            import addon_utils
            addon_utils.disable(hk.__name__, default_set=True,
                                handle_error=lambda ex: FEHLER.append(
                                    "Disabling: Traceback\n" + traceback.format_exc()))
            pruefe(not hasattr(bpy.types.Scene, "hydrokit"),
                   "add-on disabled (unregister() without errors)")
        except Exception:
            FEHLER.append("Disabling: Traceback\n" + traceback.format_exc())
    shutil.rmtree(ARBEIT, ignore_errors=True)
    print("\n=== Result")
    print(f"{len(ERGEBNISSE)} checks, {len(FEHLER)} failures")
    for f in FEHLER:
        print("FEHLER:", f)
    return 1 if FEHLER else 0


if __name__ == "__main__":
    sys.exit(main())
