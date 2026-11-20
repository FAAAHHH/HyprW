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
    # Every setting in the panel has its (i) with an explanation
    hilfe = import_module(hk.__name__ + ".ui.hilfe")
    import ast
    baum = ast.parse(open(sim_panel.__file__, encoding="utf-8").read())
    namen, themen = set(), set()
    for k in ast.walk(baum):
        if isinstance(k, ast.Call):
            wer = k.func.attr if isinstance(k.func, ast.Attribute) else getattr(k.func, "id", "")
            texte = [a.value for a in k.args
                     if isinstance(a, ast.Constant) and isinstance(a.value, str)]
            if wer in ("prop", "_regler") and texte:
                namen.add(texte[0])
            elif wer == "_hilfe" and texte:
                themen.add(texte[0])
    # Names the panel builds at run time
    gebaut = {"geraet_" + key for key, *_ in sim_panel.backen.STUFEN}
    gebaut |= {f"menge_{a}" for a in ("foam", "bubble", "spray", "nebel")}
    gebaut |= {f"linse_{a}_{t}" for a in ("wasser", "spray") for t in ("haftet", "haftanteil")}
    gebaut |= {"gischt_tempo_min", "gischt_tempo_max", "gischt_kruemmung_min", "gischt_kruemmung_max",
               "gischt_turbulenz_min", "gischt_turbulenz_max", "rolle", "scroll_schritte"}
    fehlt = {n for n in namen | gebaut | themen if n not in hilfe.HILFE and n not in hilfe.GLEICH}
    ungenutzt = set(hilfe.HILFE) - namen - themen - gebaut
    leer = [k for k, e in hilfe.HILFE.items() if len(e) != 5 or not all(e)]
    pruefe(not fehlt and not ungenutzt and not leer,
           f"every setting has an explanation ({len(hilfe.HILFE)} topics; missing {sorted(fehlt)}, "
           f"unused {sorted(ungenutzt)}, incomplete {leer})")
    # The popup draws every topic; the panel really shows the buttons
    fehler = []
    for thema in hilfe.HILFE:
        p_hilfe = []
        attrappe = type("Hilfe", (), {})()
        attrappe.layout, attrappe.thema = Layout(p_hilfe), thema
        hilfe.HYDROKIT_OT_hilfe.draw(attrappe, ctx)
        if len([t for k, t, _ in p_hilfe if k == "label"]) < 8:
            fehler.append(thema)
    knoepfe = sum(1 for k, n, _ in p if k == "operator" and n == "hydrokit.hilfe")
    pruefe(not fehler and knoepfe >= 40 and hilfe.HYDROKIT_OT_hilfe.bl_idname == "hydrokit.hilfe",
           f"explanations draw, {knoepfe} info buttons in the panel ({fehler})")
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


# ------------------------------------------------------ anisotropic surface
@abschnitt("Anisotropic surface")
def aniso_netz_test(hk):
    """The water surface from anisotropic kernels (engine_patch/README.md
    section 12): it holds the particles' volume where the spheres swell, and
    the bake status notices when it is switched."""
    from importlib import import_module
    import json
    import numpy as np
    backen = import_module(hk.__name__ + ".sim.backen")
    cache = import_module(hk.__name__ + ".sim.cache")
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")
    pruefe(backen.engine_erweiterungen().get("aniso") is True,
           "the engine has the anisotropic surface")
    scene = bpy.context.scene
    d = bpy.data.objects["Cube"].hydrokit
    d.viewport_eigenes_netz = d.vorschau_im_viewport = False
    d.oberflaechen_unterteilung = 2
    scene.frame_start, scene.frame_end = 1, 4

    ergebnis = {}
    for an in (False, True):
        d.netz_anisotrop = an
        d.cache_pfad = os.path.join(ARBEIT, "cache_aniso_" + ("an" if an else "aus"))
        pfad = cache.pfad_aufloesen(d.cache_pfad)
        n, dauer, fehler = backen.backen(scene)
        v, t = cache.mesh_lesen_np(pfad, 4)
        v = v.reshape(-1, 3).astype(np.float64)
        t = t.reshape(-1, 3)
        a, b, c = v[t[:, 0]], v[t[:, 1]], v[t[:, 2]]
        volumen = float(np.einsum("ij,ij->i", a, np.cross(b, c)).sum() / 6.0)
        with open(os.path.join(pfad, "zwischenstand", "info.json")) as f:
            stand = json.load(f)
        # every marker particle stands for a cell's eighth
        soll = stand["wasser"] * stand["dx"] ** 3 / 8.0
        ergebnis[an] = (fehler, volumen, soll, cache.info_lesen(pfad) or {})
    (f_aus, v_aus, s_aus, _), (f_an, v_an, s_an, info) = ergebnis[False], ergebnis[True]
    pruefe(f_aus is None and f_an is None and abs(v_an / s_an - 1.0) < 0.05
           and v_aus / s_aus > v_an / s_an,
           f"anisotropic mesh holds the particles' volume ({v_an:.4f} of {s_an:.4f}), "
           f"the spheres swell ({v_aus:.4f} of {s_aus:.4f})")
    pruefe((info.get("netz") or {}).get("aniso") == 0.5,
           f"the cache records the anisotropic surface: {info.get('netz')}")

    d.netz_glaettung = 1.0
    glatt = sim_panel._aenderungen(scene, d, info)
    d.netz_anisotrop = False
    aus = sim_panel._aenderungen(scene, d, info)
    d.netz_glaettung = 0.5
    pruefe("Smoothness 0.5 → 1" in glatt and "Anisotropic: on → off" in aus,
           f"bake status lists the switch and the smoothness: {glatt}, {aus}")


# ------------------------------------------------------ inflow turbulence
@abschnitt("Inflow turbulence")
def turbulenz_test(hk):
    """An inflow with turbulence (engine_patch/README.md section 13): the water
    leaving it swirls sideways, and the inflow delivers about the same amount
    of water. (Bakes with an inflow differ by the engine's random jitter, so
    the swirl is measured in the particle velocities, not by comparing
    meshes.)"""
    from importlib import import_module
    import json
    import numpy as np
    backen = import_module(hk.__name__ + ".sim.backen")
    cache = import_module(hk.__name__ + ".sim.cache")
    pruefe(backen.engine_erweiterungen().get("turbulenz") is True,
           "the engine has inflow turbulence")
    scene = bpy.context.scene
    d = bpy.data.objects["Cube"].hydrokit
    d.netz_anisotrop = False
    bpy.ops.mesh.primitive_cube_add(size=0.2, location=(0.0, 0.0, 0.5))
    zu = bpy.context.active_object
    zu.hydrokit.typ = 'INFLOW'
    zu.hydrokit.zufluss_geschwindigkeit = (1.5, 0.0, 0.0)
    scene.frame_start, scene.frame_end = 1, 6
    ergebnis = {}
    try:
        for name, prozent in (("aus", 0.0), ("an", 25.0)):
            zu.hydrokit.zufluss_turbulenz = prozent
            d.cache_pfad = os.path.join(ARBEIT, "cache_turbulenz_" + name)
            pfad = cache.pfad_aufloesen(d.cache_pfad)
            n, dauer, fehler = backen.backen(scene)
            stand = os.path.join(pfad, "zwischenstand")
            with open(os.path.join(stand, "info.json")) as f:
                wasser = json.load(f)["wasser"]
            vel = np.fromfile(os.path.join(stand, "marker_velocity.data"), np.float32).reshape(-1, 3)
            # water from the inflow: fast along +x; its sideways spread (y,
            # untouched by gravity) is the turbulence
            schnell = vel[vel[:, 0] > 0.75]
            quer = float(np.sqrt(np.mean(schnell[:, 1] ** 2))) if len(schnell) else 0.0
            ergebnis[name] = (fehler, quer, wasser, len(schnell))
    finally:
        bpy.data.objects.remove(zu)
    aus, an = ergebnis["aus"], ergebnis["an"]
    pruefe(aus[0] is None and an[0] is None and an[3] > 0 and an[1] > 3.0 * aus[1] + 0.05,
           f"25 % turbulence: sideways RMS of the inflow water {aus[1]:.3f} -> {an[1]:.3f} m/s "
           f"({aus[3]} / {an[3]} particles)")
    pruefe(abs(an[2] / aus[2] - 1.0) < 0.1,
           f"the inflow delivers about the same water: {aus[2]} -> {an[2]} particles")


# ------------------------------------------------------- Light Playback
def _anzeige_stand(anzeige):
    """Everything the display objects hold, per object: counts, positions,
    corners, face starts, every attribute, materials, ID properties."""
    import hashlib
    stand = {}
    for name in anzeige.ERGEBNISSE:
        o = bpy.data.objects.get(name)
        if o is None:
            continue
        me = o.data
        e = [len(me.vertices), len(me.edges), len(me.loops), len(me.polygons),
             tuple(m.name if m else None for m in me.materials), tuple(sorted(me.keys()))]
        if len(me.polygons):
            ls = np.empty(len(me.polygons), np.int32)
            me.polygons.foreach_get("loop_start", ls)
            e.append(hashlib.sha1(ls.tobytes()).hexdigest())
        for a in me.attributes:
            feld = {'FLOAT': ("value", np.float32, 1), 'INT': ("value", np.int32, 1),
                    'BOOLEAN': ("value", np.bool_, 1), 'FLOAT_VECTOR': ("vector", np.float32, 3),
                    'INT32_2D': ("value", np.int32, 2)}.get(a.data_type)
            if feld is None:
                e.append((a.name, a.domain, a.data_type))
                continue
            arr = np.empty(len(a.data) * feld[2], feld[1])
            a.data.foreach_get(feld[0], arr)
            e.append((a.name, a.domain, a.data_type, hashlib.sha1(arr.tobytes()).hexdigest()))
        stand[name] = tuple(e)
    return stand


def _attribut(o, name):
    a = o.data.attributes.get(name)
    if a is None:
        return None
    arr = np.empty(len(a.data), np.float32)
    a.data.foreach_get("value", arr)
    return arr


@abschnitt("Light Playback: lighter while playing, full when paused, saved, rendered")
def abspielen_test(hk):
    from importlib import import_module
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    abspielen = import_module(hk.__name__ + ".sim.abspielen")
    cache = import_module(hk.__name__ + ".sim.cache")
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")
    hilfe = import_module(hk.__name__ + ".ui.hilfe")
    backen = import_module(hk.__name__ + ".sim.backen")
    scene = bpy.context.scene
    d = bpy.data.objects["Cube"].hydrokit
    # A bake of its own with whitewater and one mesh (bake_test's is cleared)
    d.cache_pfad = os.path.join(ARBEIT, "cache_abspielen")
    pfad = cache.pfad_aufloesen(d.cache_pfad)
    d.gischt, d.viewport_eigenes_netz, d.vorschau_im_viewport = True, False, False
    scene.frame_start, scene.frame_end = 1, 10
    n, dauer, fehler = backen.backen(scene)
    pruefe(fehler is None and n == 10 and os.path.isfile(cache.partikel_datei(pfad, 8)),
           f"bake for Light Playback: {n} frames with whitewater, error: {fehler}")
    sh = scene.hydrokit
    pruefe(sh.abspielen_leicht is True and "abspielen_leicht" in hilfe.HILFE,
           "Light Playback: on by default, with its explanation")
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    pruefe(_stelle(p, "prop", "abspielen_leicht") is not None,
           "Light Playback is in the Viewport Display section")
    namen = {fn.__name__ for liste in ("animation_playback_pre", "animation_playback_post",
                                       "save_pre", "load_pre", "load_post")
             for fn in getattr(bpy.app.handlers, liste)}
    pruefe({"_abspielen_start", "_abspielen_ende", "_abspielen_vor_speichern",
            "_abspielen_vor_laden", "_abspielen_nach_laden"} <= namen,
           "playback start/stop, save and load hooks installed with the add-on")

    # --- the particle file read straight into arrays: the same as before
    roh = cache.partikel_lesen(pfad, 8)
    neu = cache.partikel_lesen_np(pfad, 8)
    gleich = roh is not None and neu is not None and all(
        bytes(memoryview(np.ascontiguousarray(n))) == bytes(r) for n, r in zip(neu, roh))
    teil = cache.partikel_lesen_np(pfad, 8, tempo=False, leben=False)
    kaputt = os.path.join(ARBEIT, "kaputt")
    os.makedirs(os.path.join(kaputt, cache.FRAMES), exist_ok=True)
    with open(cache.partikel_datei(pfad, 8), "rb") as f:
        bytes_8 = f.read()
    with open(cache.partikel_datei(kaputt, 1), "wb") as f:
        f.write(bytes_8[:len(bytes_8) // 2])
    with open(cache.partikel_datei(kaputt, 2), "wb") as f:
        f.write(b"HKP2" + bytes_8[4:])
    pruefe(gleich and teil[1] is None and teil[2] is None and np.array_equal(teil[3], neu[3])
           and cache.partikel_lesen_np(kaputt, 1) is None
           and cache.partikel_lesen_np(kaputt, 2) is None
           and cache.partikel_lesen_np(kaputt, 3) is None,
           f"particles read straight into arrays: byte for byte as before "
           f"({len(neu[3]) if neu else 0} at frame 8); cut off, older or missing files: None")
    cache.info_schreiben(kaputt, {"a": 1})
    erst = cache.info_gemerkt(kaputt)
    zweit = cache.info_gemerkt(kaputt)
    cache.info_schreiben(kaputt, {"a": 22})
    pruefe(erst == {"a": 1} and zweit is erst and cache.info_gemerkt(kaputt) == {"a": 22},
           "info.json parsed once per version of the file, again after a new bake wrote it")

    # --- the split by type against the old per-type masks, point for point
    anzeige.handler_an()
    scene.frame_set(8)
    anzeige.frame_laden(scene, 8)
    pos = np.frombuffer(roh[0], np.float32).reshape(-1, 3) + np.asarray(
        cache.info_lesen(pfad)["versatz"], np.float32)
    arten = np.frombuffer(roh[4], np.uint8)
    gut = True
    for a, (_, objname, _) in anzeige.ARTEN.items():
        o = bpy.data.objects.get(objname)
        if o is None or not cache.info_lesen(pfad).get("nebel_engine", False) and a >= 2:
            continue        # spray and mist differ by the Python mist classification
        co = np.empty(len(o.data.vertices) * 3, np.float32)
        o.data.attributes["position"].data.foreach_get("vector", co)
        gut = gut and np.array_equal(co.reshape(-1, 3), pos[arten == a])
    pruefe(gut, "whitewater split once by type: every point where the old masks put it")

    # --- full result while paused
    scene.frame_set(6)
    voll = _anzeige_stand(anzeige)
    wasser = bpy.data.objects[anzeige.OBERFLAECHE]
    n_voll = len(wasser.data.polygons)
    gischt_voll = {n: len(bpy.data.objects[n].data.vertices)
                   for n in ("HK_Blasen", "HK_Schaum", "HK_Spray") if n in bpy.data.objects}

    alt = (abspielen.LEICHT_PUNKTE, abspielen.LEICHT_AB_DREIECKE)
    abspielen.LEICHT_PUNKTE = max(1, sum(gischt_voll.values()) // 4)
    abspielen.LEICHT_AB_DREIECKE = 0          # the test bake's mesh is small: merge it anyway
    abspielen._ANTEILE.clear()
    try:
        abspielen._abspielen_start(scene)
        scene.frame_set(5)
        scene.frame_set(6)
        leicht = _anzeige_stand(anzeige)
        gischt_leicht = {n: len(bpy.data.objects[n].data.vertices) for n in gischt_voll}
        ohne_tempo = all(bpy.data.objects[n].data.attributes.get("velocity") is None
                         for n in gischt_leicht if gischt_leicht[n])
        pruefe(abspielen.leicht(scene) and 0 < len(wasser.data.polygons) < n_voll
               and wasser.data.get(abspielen.MARKE) and ohne_tempo
               and sum(gischt_leicht.values()) < sum(gischt_voll.values()),
               f"while playing: water {len(wasser.data.polygons)} instead of {n_voll} "
               f"triangles, whitewater {gischt_leicht} instead of {gischt_voll}, no velocity")
        # the same particles stay from frame to frame: only IDs the shares keep
        anteile = abspielen.anteile(pfad, d)
        gut = True
        for f in (5, 6, 7):
            scene.frame_set(f)
            for a, (_, objname, menge) in anzeige.ARTEN.items():
                o = bpy.data.objects.get(objname)
                z = _attribut(o, "hk_zufall") if o is not None else None
                if z is None or not len(z) or anteile[a] >= 1.0:
                    continue
                kenn = np.rint(z * 255.0).astype(np.int64)
                gut = gut and bool(anzeige._behalten(getattr(d, menge) * anteile[a])[kenn].all())
        pruefe(gut and min(anteile[:3]) < 1.0 and anteile[3] == 1.0,
               f"thinned by ID with one share per bake {tuple(round(x, 3) for x in anteile)}: "
               f"the same particles from frame to frame, mist complete")
        scene.frame_set(6)
        e, t = abspielen.flaeche_leicht(pfad, 6, cache.info_lesen(pfad))
        t3 = t.reshape(-1, 3)
        pruefe(len(e) % 3 == 0 and t.max() < len(e) // 3
               and not ((t3[:, 0] == t3[:, 1]) | (t3[:, 1] == t3[:, 2]) | (t3[:, 0] == t3[:, 2])).any(),
               "light water: valid corners, no collapsed triangles")

        # rendering while playing: the full frame, then light again
        anzeige._render_beginnt(scene)
        anzeige._bei_frame(scene)
        im_render = _anzeige_stand(anzeige)
        anzeige._render_endet(scene)
        pruefe(im_render == voll and _anzeige_stand(anzeige) == leicht,
               "a render while playing gets the full frame, the viewport the light one after")

        # saving while playing: the file gets the full frame
        anzeige.frame_laden(scene)
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ARBEIT, "leicht.blend"), copy=True)
        pruefe(_anzeige_stand(anzeige) == voll, "saved while playing: the full frame is saved")
        scene.frame_set(6)
        abspielen._abspielen_vor_laden()
        abspielen._abspielen_nach_laden()
        pruefe(_anzeige_stand(anzeige) == voll,
               "a file holding a light frame (autosave) opens with the full frame")

        abspielen._abspielen_start(scene)
        sh.abspielen_leicht = False
        scene.frame_set(6)
        pruefe(_anzeige_stand(anzeige) == voll, "Light Playback off: full frames while playing")
        sh.abspielen_leicht = True
        scene.frame_set(6)
        abspielen._abspielen_ende(scene)
        pruefe(not abspielen.SPIELT and _anzeige_stand(anzeige) == voll,
               "playback stopped: the frame on display in full at once, as when paused")
        # renamed while playing: the scene on display is found by its
        # session_uid (by name the light frame stayed until the next frame change)
        abspielen._abspielen_start(scene)
        scene.frame_set(6)
        name_alt = scene.name
        scene.name = "HKT umbenannt"
        abspielen._abspielen_ende(scene)
        umbenannt = _anzeige_stand(anzeige) == voll and not wasser.data.get(abspielen.MARKE)
        scene.name = name_alt
        pruefe(umbenannt, "scene renamed while playing: stopping brings the full frame back")
        abspielen._abspielen_start(scene)
        scene.frame_set(6)
        abspielen.aus()
        pruefe(_anzeige_stand(anzeige) == voll, "add-on switched off while playing: full frame")
    finally:
        abspielen.LEICHT_PUNKTE, abspielen.LEICHT_AB_DREIECKE = alt
        abspielen._ANTEILE.clear()
        abspielen.SPIELT = False
        abspielen.an()
        anzeige.handler_aus()

    # --- vertex clustering on a closed sphere: lighter, valid, the same twice
    bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, segments=64, ring_count=32)
    kugel = bpy.context.active_object
    me = kugel.data
    me.calc_loop_triangles()
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    tri = np.empty(len(me.loop_triangles) * 3, np.int32)
    me.loop_triangles.foreach_get("vertices", tri)
    bpy.data.objects.remove(kugel)
    e1, t1 = abspielen.vereinfachen(co, tri, 0.2)
    e2, t2 = abspielen.vereinfachen(co, tri, 0.2)
    r = np.linalg.norm(e1.reshape(-1, 3), axis=1)
    pruefe(0 < len(t1) < len(tri) and np.array_equal(e1, e2) and np.array_equal(t1, t2)
           and t1.max() < len(e1) // 3 and r.max() <= 1.0 + 1e-5 and r.min() > 0.8,
           f"vertex clustering: sphere {len(tri) // 3} -> {len(t1) // 3} triangles, "
           f"radius {r.min():.2f} .. {r.max():.2f}, the same twice")


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


# ------------------------------------------- light mist while playing
@abschnitt("Light mist while the timeline plays")
def leicht_nebel_test(hk):
    """While the timeline plays the viewport draws the mist from at most
    NEBEL_LEICHT_PUNKTE points (sim/aussehen.py); paused, in every render and
    in every saved file the stack and the mist are exactly the full ones."""
    import hashlib
    from importlib import import_module
    aussehen = import_module(hk.__name__ + ".sim.aussehen")
    aa = import_module(hk.__name__ + ".sim.abspielen_aussehen")
    namen = {liste: [getattr(h, "__name__", "") for h in getattr(bpy.app.handlers, liste)]
             for liste, _ in aa._HANDLER}
    pruefe(all(fn.__name__ in namen[liste] for liste, fn in aa._HANDLER),
           "playback, frame, save, load and undo handlers installed with the add-on")

    # A scene of its own without a domain: the render handlers then load no
    # cache frame over the test points
    bpy.ops.scene.new(type='EMPTY')
    scene = bpy.context.scene
    kamera = bpy.data.objects.new("HK Test Kamera", bpy.data.cameras.new("HK Test Kamera"))
    scene.collection.objects.link(kamera)
    scene.camera = kamera
    kamera.location = (0.5, -3.0, 0.5)
    kamera.rotation_euler = (math.radians(90.0), 0.0, 0.0)
    obj = bpy.data.objects.get("HK_Nebel")
    if obj is None:
        obj = bpy.data.objects.new("HK_Nebel", bpy.data.meshes.new("HK_Nebel"))
    scene.collection.objects.link(obj)
    for m in list(obj.modifiers):
        obj.modifiers.remove(m)
    n = 80_000
    punkte = np.random.default_rng(7).random((n, 3)).astype(np.float32)   # a 1 m cube
    zufall = (np.arange(n) % 256).astype(np.float32) / 255.0             # the engine ID: one byte

    def fuellen(k, mit_zufall=True):
        me = obj.data
        me.clear_geometry()
        me.vertices.add(k)
        me.vertices.foreach_set("co", punkte[:k].ravel())
        attr = me.attributes.get("hk_zufall")
        if attr is not None and not mit_zufall:
            me.attributes.remove(attr)
        elif mit_zufall:
            if attr is None:
                attr = me.attributes.new("hk_zufall", 'FLOAT', 'POINT')
            attr.data.foreach_set("value", zufall[:k])
        me.update()

    voxel = 0.02
    radius = voxel * aussehen.NEBEL_RADIUS_VOXEL
    dichte = aussehen.NEBEL_OPTISCHE_TIEFE / radius
    mat = aussehen.baue_nebel_material(dichte, 0.03, radius)
    voll = aussehen._gruppe_nebel("HK Nebel", radius, dichte, mat, voxel)
    aussehen._modifier_setzen(obj, voll)
    fuellen(n)

    # The density grid as points (Grid to Points), in the viewport and in a render
    probe = bpy.data.objects.new("HK Test Gitter", bpy.data.meshes.new("HK Test Gitter"))
    scene.collection.objects.link(probe)
    g = bpy.data.node_groups.new("HK Test Gitter", "GeometryNodeTree")
    g.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    g.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    info = g.nodes.new("GeometryNodeObjectInfo")
    info.inputs["Object"].default_value = obj
    holen = g.nodes.new("GeometryNodeGetNamedGrid")
    holen.inputs["Name"].default_value = "density"
    zellen = g.nodes.new("GeometryNodeGridToPoints")
    ablegen = g.nodes.new("GeometryNodeStoreNamedAttribute")
    ablegen.data_type = 'FLOAT'
    ablegen.inputs["Name"].default_value = "wert"
    ausgang = g.nodes.new("NodeGroupOutput")
    g.links.new(info.outputs["Geometry"], holen.inputs["Volume"])
    g.links.new(holen.outputs["Grid"], zellen.inputs["Grid"])
    g.links.new(zellen.outputs["Points"], ablegen.inputs["Geometry"])
    g.links.new(zellen.outputs["Value"], ablegen.inputs["Value"])
    g.links.new(ablegen.outputs["Geometry"], ausgang.inputs[0])
    probe.modifiers.new("Gitter", 'NODES').node_group = g

    def erfassen(depsgraph):
        """(active cells, sum, hash of cells and values, cell size)"""
        gs = probe.evaluated_get(depsgraph).evaluated_geometry()   # keeps pc alive
        pc = gs.pointcloud
        k = len(pc.points) if pc is not None else 0
        if not k:
            return (0, 0.0, "", None)
        wert = np.empty(k, np.float32)
        pc.attributes["wert"].data.foreach_get("value", wert)
        pos = np.empty(3 * k, np.float32)
        pc.attributes["position"].data.foreach_get("vector", pos)
        pos = pos.reshape(-1, 3)
        o = np.lexsort(pos.T)
        roh = pos[o].tobytes() + wert[o].tobytes()
        x = np.unique(np.round(pos[:, 0].astype(np.float64), 6))
        zelle = float(np.diff(x).min()) if len(x) > 1 else None
        return (k, round(float(wert.sum(dtype=np.float64)), 6),
                hashlib.sha256(roh).hexdigest()[:16], zelle)

    class Erfassung(bpy.types.RenderEngine):
        bl_idname = "HK_TEST_ERFASSUNG"
        bl_label = "HyprW test capture"
        ergebnis = []

        def render(self, depsgraph):
            Erfassung.ergebnis.append((depsgraph.mode, erfassen(depsgraph)))
            r = depsgraph.scene.render
            self.end_result(self.begin_result(0, 0, r.resolution_x, r.resolution_y))

    def rendern():
        Erfassung.ergebnis.clear()
        bpy.ops.render.render()
        return Erfassung.ergebnis[-1] if Erfassung.ergebnis else (None, None)

    def stapel():
        return [(m.name, m.show_viewport, m.show_render,
                 m.node_group.name if m.node_group else None) for m in obj.modifiers]

    def zeichen(baum):
        """Every node with its inputs, and every link"""
        def wert(s):
            v = getattr(s, "default_value", None)
            if isinstance(v, bpy.types.ID):
                return v.name
            try:
                return repr(v if isinstance(v, str) else tuple(v))
            except TypeError:
                return repr(v)
        return ([(k.bl_idname, k.name, [(s.identifier, wert(s)) for s in k.inputs])
                 for k in baum.nodes]
                + [(l.from_node.name, l.from_socket.identifier, l.to_node.name,
                    l.to_socket.identifier) for l in baum.links])

    bpy.utils.register_class(Erfassung)
    scene.render.engine = Erfassung.bl_idname
    scene.render.resolution_x, scene.render.resolution_y = 16, 9
    scene.render.resolution_percentage = 100
    spielt = aa.spielt
    try:
        dg = bpy.context.evaluated_depsgraph_get()
        dg.update()
        vorher = stapel()
        gruppe = zeichen(voll)
        pausiert = erfassen(dg)
        modus, gerendert = rendern()
        pruefe(vorher == [("HK Aussehen", True, True, "HK Nebel")] and pausiert[0] > 0
               and modus == 'RENDER' and gerendert == pausiert,
               f"paused: the full mist ({pausiert[0]:,} cells of {pausiert[3] * 100:.1f} cm), "
               f"a render evaluates the same voxel for voxel")

        aa._aussehen_spiel_an(scene)
        dg.update()
        spielend = stapel()
        leicht = erfassen(dg)
        anteil = aussehen.NEBEL_LEICHT_PUNKTE / n
        soll = radius * anteil ** (-1.0 / 3.0) / aussehen.NEBEL_LEICHT_RADIUS_VOXEL
        pruefe(spielend == [("HK Aussehen", False, True, "HK Nebel"),
                            (aussehen.LEICHT_MOD, True, False, aussehen.LEICHT_GRUPPE)],
               "playing: the full look off in the viewport only, the light one after it, "
               "never rendered")
        pruefe(0 < leicht[0] < pausiert[0] and leicht[3] is not None
               and abs(leicht[3] - soll) < 1e-3 * soll,
               f"playing: mist from {anteil:.1%} of {n:,} points, cells {leicht[3] * 100:.2f} cm "
               f"= radius x share^(-1/3) / {aussehen.NEBEL_LEICHT_RADIUS_VOXEL} "
               f"({soll * 100:.2f} cm), {leicht[0]:,} instead of {pausiert[0]:,} cells")
        modus, gerendert = rendern()
        # Even switched on for rendering by hand, the light group passes a
        # render through (Is Viewport)
        obj.modifiers[aussehen.LEICHT_MOD].show_render = True
        _, von_hand = rendern()
        obj.modifiers[aussehen.LEICHT_MOD].show_render = False
        pruefe(modus == 'RENDER' and gerendert == pausiert and von_hand == pausiert,
               "a render while playing evaluates the full mist voxel for voxel, also with the "
               "light modifier switched on for rendering")

        aa._aussehen_spiel_aus(scene)
        dg.update()
        pruefe(stapel() == vorher and erfassen(dg) == pausiert and zeichen(voll) == gruppe,
               "playback stopped: exactly the stack and the mist from before, HK Nebel unchanged")

        # While playing: Light Playback switched, or a playback that started
        # before the add-on was loaded
        aa.spielt = lambda: True
        aa._aussehen_bei_frame(scene)
        gut = aussehen.ist_leicht(scene)
        if hasattr(scene.hydrokit, "abspielen_leicht"):
            scene.hydrokit.abspielen_leicht = False
            aa._aussehen_bei_frame(scene)
            gut &= stapel() == vorher
            aa._aussehen_spiel_an(scene)
            gut &= stapel() == vorher
            scene.hydrokit.abspielen_leicht = True
            aa._aussehen_bei_frame(scene)
            gut &= aussehen.ist_leicht(scene)
            pruefe(gut, "Light Playback switched while playing: the mist follows at the next "
                        "frame; switched off, playing changes nothing")
        else:
            pruefe(gut, "playing without the start event: light from the next frame on")

        # Saved while playing: the file has the full look, playback goes on light
        pfad = os.path.join(ARBEIT, "leicht_gespeichert.blend")
        bpy.ops.wm.save_as_mainfile(filepath=pfad, copy=True)
        weiter = aussehen.ist_leicht(scene)
        bekannt = {k: set(getattr(bpy.data, k).keys())
                   for k in ("objects", "meshes", "node_groups", "materials")}
        with bpy.data.libraries.load(pfad) as (quelle, ziel):
            ziel.objects = [obj.name]
            gruppen = list(quelle.node_groups)
        gespeichert = [(m.name, m.show_viewport, m.show_render) for m in ziel.objects[0].modifiers]
        for k in ("objects", "meshes", "node_groups", "materials"):
            sammlung = getattr(bpy.data, k)
            for name in set(sammlung.keys()) - bekannt[k]:
                sammlung.remove(sammlung[name])
        pruefe(weiter and gespeichert == [("HK Aussehen", True, True)]
               and aussehen.LEICHT_GRUPPE not in gruppen,
               f"saved while playing: the file has the full look {gespeichert} and no light "
               f"group; playing goes on light")

        # A file written while light (an autosave) opens with the full look
        aussehen._ABGESCHALTET.clear()
        aa._aussehen_nach_laden(pfad)
        gut = stapel() == vorher
        aa._aussehen_nach_undo(scene)
        gut &= aussehen.ist_leicht(scene)
        aa.spielt = lambda: False
        aa._aussehen_nach_undo(scene)
        pruefe(gut and stapel() == vorher,
               "opening a file written while light: the full look; undo and redo: light only "
               "while playing")

        # The light modifier deleted while playing; a look the user switched off
        aa._aussehen_spiel_an(scene)
        obj.modifiers.remove(obj.modifiers[aussehen.LEICHT_MOD])
        aa._aussehen_spiel_aus(scene)
        gut = stapel() == vorher
        obj.modifiers["HK Aussehen"].show_viewport = False
        aa._aussehen_spiel_an(scene)
        gut &= stapel() == [("HK Aussehen", False, True, "HK Nebel")]
        aa._aussehen_spiel_aus(scene)
        gut &= stapel() == [("HK Aussehen", False, True, "HK Nebel")]
        obj.modifiers["HK Aussehen"].show_viewport = True
        pruefe(gut, "light modifier deleted while playing: the full look comes back; a look "
                    "switched off by the user stays off")

        # An empty frame, points without hk_zufall, a scene without mist
        fuellen(0)
        aa._aussehen_spiel_an(scene)
        dg.update()
        leer = erfassen(dg)
        aa._aussehen_spiel_aus(scene)
        fuellen(n, mit_zufall=False)
        aa._aussehen_spiel_an(scene)
        dg.update()
        ohne = erfassen(dg)
        aa._aussehen_spiel_aus(scene)
        fuellen(n)
        obj.name = "HK_Nebel_weg"
        ohne_nebel = not aussehen.abspielen_leicht(scene, True)
        aa._aussehen_spiel_an(scene)
        aa._aussehen_spiel_aus(scene)
        obj.name = "HK_Nebel"
        pruefe(leer[0] == 0 and 0 < ohne[0] < pausiert[0] and ohne[3] is not None
               and abs(ohne[3] - soll) < 1e-3 * soll and ohne_nebel and stapel() == vorher,
               f"empty frame: no mist; without hk_zufall chosen by index ({ohne[0]:,} cells); "
               f"no HK_Nebel: nothing switched")

        # Switched off while light: no handlers, the full look back
        aa._aussehen_spiel_an(scene)
        aa.abschalten()
        uebrig = [getattr(h, "__name__", "") for liste, _ in aa._HANDLER
                  for h in getattr(bpy.app.handlers, liste)
                  if getattr(h, "__name__", "").startswith("_aussehen_")]
        aa.lade_handler_an()
        pruefe(not uebrig and stapel() == vorher,
               "add-on switched off while light: handlers gone, the full look back")
    finally:
        aa.spielt = spielt
        scene.render.engine = 'BLENDER_WORKBENCH'
        bpy.utils.unregister_class(Erfassung)


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


# ------------------------------------------------ obstacle sheets (terrain)
def _flaeche_objekt(name, ecken, flaechen):
    me = bpy.data.meshes.new(name)
    me.from_pydata(ecken, [], flaechen)
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    return ob


def _kiste_dreiecke(x0, y0, z0, x1, y1, z1):
    """The 12 triangles of a box facing out, as corner positions."""
    p = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    dreiecke = []
    for q in ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)):
        dreiecke += [(p[q[0]], p[q[1]], p[q[2]]), (p[q[0]], p[q[2]], p[q[3]])]
    return dreiecke


def _gelaende_objekt(name, n, x0, y0, x1, y1, hoehe, gedreht=False):
    """Height field of n x n quads facing up; gedreht turns every third one
    round (an import with mixed winding)."""
    ecken = [(x0 + (x1 - x0) * a / n, y0 + (y1 - y0) * b / n, 0.0)
             for a in range(n + 1) for b in range(n + 1)]
    ecken = [(x, y, hoehe(x, y)) for x, y, _ in ecken]
    flaechen = []
    for a in range(n):
        for b in range(n):
            q = [a * (n + 1) + b, (a + 1) * (n + 1) + b, (a + 1) * (n + 1) + b + 1, a * (n + 1) + b + 1]
            flaechen.append(q[::-1] if gedreht and (a + b) % 3 == 0 else q)
    return _flaeche_objekt(name, ecken, flaechen)


def _netz(tm):
    return (np.frombuffer(tm.vertices, dtype=np.float32).reshape(-1, 3),
            np.frombuffer(tm.triangles, dtype=np.int32).reshape(-1, 3))


def _kanten_zahlen(f):
    k = np.sort(np.concatenate((f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]])), axis=1).astype(np.int64)
    return np.unique(k[:, 0] * (int(k.max()) + 1) + k[:, 1], return_counts=True)[1]


def _kreuzungen(v, f, x, y):
    """z of every face the vertical line through (x, y) crosses."""
    p = v.astype(np.float64)
    a, b, c = p[f[:, 0]], p[f[:, 1]], p[f[:, 2]]
    d = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    ok = np.abs(d) > 1e-12                      # walls are seen edge-on
    d = np.where(ok, d, 1.0)
    l1 = ((b[:, 1] - c[:, 1]) * (x - c[:, 0]) + (c[:, 0] - b[:, 0]) * (y - c[:, 1])) / d
    l2 = ((c[:, 1] - a[:, 1]) * (x - c[:, 0]) + (a[:, 0] - c[:, 0]) * (y - c[:, 1])) / d
    l3 = 1.0 - l1 - l2
    drin = ok & (l1 > 0) & (l2 > 0) & (l3 > 0)
    return (l1 * a[:, 2] + l2 * b[:, 2] + l3 * c[:, 2])[drin]


def _falsch_innen(v, f, roh_v, roh_f, punkte, abstand):
    """Points `abstand` under and over the sheet that the engine's inside
    test (ray parity per column) would get wrong."""
    falsch = []
    for x, y in punkte:
        oben = _kreuzungen(roh_v, roh_f, x, y)
        if len(oben) == 0:
            continue
        z = float(oben.max())
        alle = _kreuzungen(v, f, x, y)
        for dz, innen in ((-abstand, True), (abstand, False)):
            if (int((alle > z + dz).sum()) % 2 == 1) != innen:
                falsch.append((round(x, 3), round(y, 3), round(z + dz, 3)))
    return falsch


@abschnitt("Obstacle sheets: closed bodies untouched, terrain closed downwards")
def gelaende_test(hk):
    """An open obstacle mesh (terrain, plane) becomes a closed body as the
    engine needs one. Before, a constant shell behind the faces: at the cliff
    top of the user's terrain its back surface crossed the front and water
    leaked into the rock (Resolution 202, both from the same state at frame
    100: 35 particles inside the rock by frame 180; closed downwards, none)."""
    from importlib import import_module
    from mathutils import Vector
    geometrie = import_module(hk.__name__ + ".sim.geometrie")
    import bmesh
    versatz = Vector((0.0, 0.0, 0.0))
    g, dx = 2.0, 2.0 / 24
    md = 3.0 * dx
    bereich = ((g, g, g), 4.0 * dx + md)
    neu = []

    def umwandeln(ob):
        bpy.context.view_layer.update()
        return geometrie.als_trianglemesh(ob, versatz, mindestdicke=md, bereich=bereich)

    # closed bodies reach the engine exactly as before
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=2, radius=0.4)
    me = bpy.data.meshes.new("HK_Test_Kugel")
    bm.to_mesh(me)
    bm.free()
    kugel = bpy.data.objects.new("HK_Test_Kugel", me)
    bpy.context.scene.collection.objects.link(kugel)
    kugel.location = (1.0, 1.0, 1.0)
    neu.append(kugel)
    roh, dick = geometrie.als_trianglemesh(kugel, versatz), umwandeln(kugel)
    pruefe(dick is not None and not dick.hk_verdickt
           and bytes(dick.vertices) == bytes(roh.vertices)
           and bytes(dick.triangles) == bytes(roh.triangles),
           "closed obstacle: handed to the engine bit for bit as without a thickness")

    # a cube with split edges (flat-shaded import): welded, then closed
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=0.5)
    bmesh.ops.split_edges(bm, edges=bm.edges[:])
    me = bpy.data.meshes.new("HK_Test_Splitwuerfel")
    bm.to_mesh(me)
    bm.free()
    wuerfel = bpy.data.objects.new("HK_Test_Splitwuerfel", me)
    bpy.context.scene.collection.objects.link(wuerfel)
    wuerfel.location = (1.0, 1.0, 0.5)
    neu.append(wuerfel)
    tm = umwandeln(wuerfel)
    v, f = _netz(tm)
    pruefe(len(me.vertices) == 24 and len(v) == 8 and len(f) == 12
           and set(_kanten_zahlen(f)) == {2} and not tm.hk_verdickt,
           f"cube with split edges welded: {len(me.vertices)} -> {len(v)} vertices, closed, not thickened")

    # terrain past the domain with a knife-edge ridge (narrower than twice
    # the shell) and a cliff: closed downwards, the surface stays in place
    def hoehe(x, y):
        z = 0.6 + 0.1 * math.sin(2.0 * x) * math.cos(3.0 * y)
        if abs(x - 1.0) < 1e-6:
            z += 0.5                            # the ridge: one row of vertices
        if x > 1.5:
            z += 0.4                            # the cliff
        return z
    gelaende = _gelaende_objekt("HK_Test_Gelaende", 48, -1.0, -1.0, 3.0, 3.0, hoehe)
    neu.append(gelaende)
    tm = umwandeln(gelaende)
    v, f = _netz(tm)
    roh_v, roh_f = _netz(geometrie.als_trianglemesh(gelaende, versatz))
    drinnen = np.all((roh_v[:, :2] >= 0.0) & (roh_v[:, :2] <= g), axis=1)
    alle = {tuple(e) for e in v.tolist()}
    punkte = [(0.1234 + 0.37 * a, 0.0987 + 0.41 * b) for a in range(5) for b in range(5)]
    punkte += [(0.9 + 0.0137 * a, 0.731) for a in range(15)]           # across the ridge
    punkte += [(1.43 + 0.0119 * a, 1.217) for a in range(12)]          # across the cliff
    falsch = _falsch_innen(v, f, roh_v, roh_f, punkte, 0.02)
    pruefe(tm.hk_verdickt and set(_kanten_zahlen(f) % 2) == {0} and float(v[:, 2].min()) < 0.0
           and all(tuple(e) in alle for e in roh_v[drinnen].tolist()),
           f"terrain past the domain closed downwards: closed, lowest point z {float(v[:, 2].min()):.2f} m "
           f"(grid floor 0), all {int(drinnen.sum())} surface vertices over the grid kept")
    # the bottom only copies the boundary: before, every surface vertex got an
    # unused copy (585k on the user's terrain, removed one by one in the engine)
    pruefe(len(np.unique(f)) == len(v),
           f"closed downwards: all {len(v)} vertices are used by a face")
    pruefe(not falsch,
           f"terrain: inside just under the surface and outside just over it at {2 * len(punkte)} "
           f"points, ridge and cliff included (wrong: {falsch[:3]})")

    # mixed winding: turned to the orientation of most of the area first
    # (with exactly half each way it is a coin toss, as for any rule on
    # winding), then closed downwards
    gemischt = _gelaende_objekt("HK_Test_Gemischt", 24, -1.0, -1.0, 3.0, 3.0,
                                lambda x, y: 0.6 + 0.1 * math.sin(2.0 * x), gedreht=True)
    neu.append(gemischt)
    tm = umwandeln(gemischt)
    v, f = _netz(tm)
    roh_v, roh_f = _netz(geometrie.als_trianglemesh(gemischt, versatz))
    falsch = _falsch_innen(v, f, roh_v, roh_f, punkte[:25], 0.02)
    pruefe(set(_kanten_zahlen(f) % 2) == {0} and float(v[:, 2].min()) < 0.0 and not falsch,
           f"terrain with every third face turned round: closed downwards, inside test right "
           f"(wrong: {falsch[:3]})")

    # a floor duplicated in place (Shift+D cancelled, an import with doubled
    # faces), and one doubled with the copy turned round (double-sided):
    # welded, each was a double sheet with 2 or 4 faces on every edge, closed
    # to the parity test but without volume, and the water fell through. The
    # copy goes; the floor reaches the engine bit for bit as the single one
    boden_ecken = [(-1.0 + 0.5 * a, -1.0 + 0.5 * b, 0.6) for a in range(9) for b in range(9)]
    boden_flaechen = []
    for a in range(8):
        for b in range(8):
            q = (9 * a + b, 9 * a + 9 + b, 9 * a + 10 + b, 9 * a + 1 + b)
            boden_flaechen += [(q[0], q[1], q[2]), (q[0], q[2], q[3])]
    boden = _flaeche_objekt("HK_Test_Boden", boden_ecken, boden_flaechen)
    neu.append(boden)
    tb = umwandeln(boden)
    roh_v, roh_f = _netz(geometrie.als_trianglemesh(boden, versatz))
    for name, wie in (("duplicated in place", 1), ("doubled with the copy turned round", -1)):
        doppelt = _flaeche_objekt("HK_Test_Boden_Doppelt", boden_ecken * 2,
                                  boden_flaechen + [tuple(i + 81 for i in t)[::wie]
                                                    for t in boden_flaechen])
        neu.append(doppelt)
        tm = umwandeln(doppelt)
        v, f = _netz(tm)
        falsch = _falsch_innen(v, f, roh_v, roh_f, punkte[:25], 0.02)
        pruefe(tb.hk_verdickt and tm.hk_verdickt and float(v[:, 2].min()) < 0.0 and not falsch
               and bytes(tm.vertices) == bytes(tb.vertices)
               and bytes(tm.triangles) == bytes(tb.triangles),
               f"floor {name}: the copy dropped, closed downwards bit for bit as the single "
               f"floor, inside test right (wrong: {falsch[:3]})")

    # two boxes stacked, every triangle on corners of its own (an STL import):
    # welded, they touch at a face both ways round. Without a fold that pair
    # is a wall between two bodies and stays; the whole is closed
    stl = (_kiste_dreiecke(0.75, 0.75, 0.25, 1.25, 1.25, 0.75)
           + _kiste_dreiecke(0.75, 0.75, 0.75, 1.25, 1.25, 1.25))
    kisten = _flaeche_objekt("HK_Test_STL_Kisten", [e for t in stl for e in t],
                             [(3 * i, 3 * i + 1, 3 * i + 2) for i in range(len(stl))])
    neu.append(kisten)
    tm = umwandeln(kisten)
    v, f = _netz(tm)
    pruefe(len(v) == 12 and len(f) == 24 and set(_kanten_zahlen(f)) == {2, 4} and not tm.hk_verdickt,
           f"two boxes stacked, every triangle on its own corners: welded to {len(v)} vertices and "
           f"{len(f)} triangles, the face where they touch kept both ways, closed, not thickened")

    # one quad 2 km wide over the 2 m grid: cut, so the engine does not grid
    # its whole box (std::bad_alloc before)
    riese = _flaeche_objekt("HK_Test_Riesenquad",
                            [(-1000.0, -1000.0, 0.6), (1000.0, -1000.0, 0.6),
                             (1000.0, 1000.0, 0.6), (-1000.0, 1000.0, 0.6)], [(0, 1, 2, 3)])
    neu.append(riese)
    tm = umwandeln(riese)
    v, f = _netz(tm)
    roh_v, roh_f = _netz(geometrie.als_trianglemesh(riese, versatz))
    weite = float(np.abs(v[:, :2] - g / 2).max())
    falsch = _falsch_innen(v, f, roh_v, roh_f, punkte[:25], 0.02)
    pruefe(weite < g and set(_kanten_zahlen(f) % 2) == {0} and not falsch,
           f"2 km quad cut to the grid: reaches {weite:.2f} m from its middle instead of 1000 m, "
           f"closed, inside test right")

    # a plate in the middle and a ceiling stay shells: never a column to the floor
    platte = _flaeche_objekt("HK_Test_Platte", [(0.7, 0.7, 1.0), (1.3, 0.7, 1.0), (1.3, 1.3, 1.0),
                                                (0.7, 1.3, 1.0)], [(0, 1, 2, 3)])
    decke = _flaeche_objekt("HK_Test_Decke", [(-2.0, -2.0, 1.5), (-2.0, 4.0, 1.5), (4.0, 4.0, 1.5),
                                              (4.0, -2.0, 1.5)], [(0, 1, 2, 3)])
    neu += [platte, decke]
    tp, td = umwandeln(platte), umwandeln(decke)
    vp, fp = _netz(tp)
    vd, fd = _netz(td)
    pruefe(tp.hk_verdickt and abs(float(vp[:, 2].min()) - (1.0 - md)) < 1e-4
           and set(_kanten_zahlen(fp) % 2) == {0},
           f"plate inside the domain: a {md:.2f} m shell under it, not closed down to the floor")
    pruefe(td.hk_verdickt and float(vd[:, 2].min()) > 1.5 - 1e-4 and set(_kanten_zahlen(fd) % 2) == {0},
           "ceiling facing down past the domain: a shell above it, not closed downwards")

    # an open sheet scaled to nothing: welded to one point, no faces left
    null = _flaeche_objekt("HK_Test_Null", [(0.5, 0.5, 0.5), (1.5, 0.5, 0.5), (1.5, 1.5, 0.5),
                                            (0.5, 1.5, 0.5)], [(0, 1, 2, 3)])
    null.scale = (0.0, 0.0, 0.0)
    neu.append(null)
    pruefe(umwandeln(null) is None, "open sheet scaled to zero: left out, no error")

    for ob in neu:
        me = ob.data
        bpy.data.objects.remove(ob)
        bpy.data.meshes.remove(me)


# ------------------------------------------------- pause other animation
@abschnitt("Pause Other Animation while playing")
def szene_pause_test(hk):
    """A heavy mesh whose Simple Deform follows an animated helper parented
    to it (botaniq's wind), next to things that must keep moving or must
    never be touched. Playback start and stop are simulated by calling the
    handlers; save, undo and redo run for real."""
    import bmesh
    from importlib import import_module
    sp = import_module(hk.__name__ + ".sim.szene_pause")
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")
    rna = bpy.types.Scene.bl_rna.properties["hydrokit"].fixed_type.bl_rna.properties
    p, stand = rna.get("abspielen_andere_pausieren"), rna.get(sp.STAND)
    pruefe(p is not None and p.type == 'BOOLEAN' and p.default is False
           and p.name == "Pause Other Animation" and stand is not None and stand.is_hidden,
           "Pause Other Animation: off by default, its record hidden")
    nach_frame = [getattr(h, "__name__", "") for h in bpy.app.handlers.frame_change_post]
    pruefe(all(any(getattr(h, "__name__", "") == fn.__name__ for h in getattr(bpy.app.handlers, liste))
               for liste, fn in sp._HANDLER)
           and ("_bei_frame" not in nach_frame
                or nach_frame.index("_pause_frame_nach") < nach_frame.index("_bei_frame")),
           "handlers for playback, save, load, undo, render and the frame measurement registered, "
           "the measurement before HyprW's own frame handler")

    bpy.ops.scene.new(type='EMPTY')
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 40

    def netz(name, n):
        me = bpy.data.meshes.new(name)
        bm = bmesh.new()
        bmesh.ops.create_grid(bm, x_segments=n, y_segments=n, size=1.0)
        bm.to_mesh(me)
        bm.free()
        o = bpy.data.objects.new(name, me)
        scene.collection.objects.link(o)
        return o

    baum = netz("HKT Baum", 240)
    wind = bpy.data.objects.new("HKT Wind", None)
    scene.collection.objects.link(wind)
    wind.parent = baum
    biegen = baum.modifiers.new("Biegen", 'SIMPLE_DEFORM')
    biegen.deform_method, biegen.origin = 'BEND', wind
    for f, w in ((1, 0.0), (40, 1.2)):
        biegen.angle = w
        baum.keyframe_insert('modifiers["Biegen"].angle', frame=f)
    for f, w in ((1, 0.0), (40, 1.5)):
        wind.rotation_euler.z = w
        wind.keyframe_insert("rotation_euler", frame=f)
    klein = netz("HKT Klein", 20)                # light: keeps moving
    kamera = bpy.data.objects.new("HKT Kamera", bpy.data.cameras.new("HKT Kamera"))
    scene.collection.objects.link(kamera)
    scene.camera = kamera
    for o, werte in ((klein, ((1, 0.0), (40, 4.0))), (kamera, ((1, -10.0), (40, -5.0)))):
        for f, w in werte:
            o.location.y = w
            o.keyframe_insert("location", frame=f)
    folgt = netz("HKT Folgt Kamera", 240)        # heavy, follows the camera
    verschieben = folgt.modifiers.new("Verschieben", 'DISPLACE')
    verschieben.texture = bpy.data.textures.new("HKT Wolken", 'CLOUDS')
    verschieben.texture_coords, verschieben.texture_coords_object = 'OBJECT', kamera
    treiber = netz("HKT Treiber", 240)           # heavy, driven by the frame
    treiber.modifiers.new("Biegen", 'SIMPLE_DEFORM')
    treiber.driver_add('modifiers["Biegen"].angle').driver.expression = "frame / 40"
    hindernis = netz("HKT Hindernis", 240)       # heavy, but HyprW's
    hindernis.modifiers.new("Biegen", 'SIMPLE_DEFORM')
    for f, w in ((1, 0.0), (40, 0.8)):
        hindernis.modifiers["Biegen"].angle = w
        hindernis.keyframe_insert('modifiers["Biegen"].angle', frame=f)
    hindernis.hydrokit.typ = 'OBSTACLE'
    netz("HKT Domain", 2).hydrokit.typ = 'DOMAIN'
    # Heavy, but what they follow also bends HyprW's obstacle, or is a light
    hwind = bpy.data.objects.new("HKT Hindernis Wind", None)
    scene.collection.objects.link(hwind)
    hindernis.modifiers["Biegen"].origin = hwind
    netz("HKT Teilt Hindernis", 240).modifiers.new("Biegen", 'SIMPLE_DEFORM').origin = hwind
    licht = bpy.data.objects.new("HKT Licht", bpy.data.lights.new("HKT Licht", 'POINT'))
    scene.collection.objects.link(licht)
    netz("HKT Folgt Licht", 240).modifiers.new("Biegen", 'SIMPLE_DEFORM').origin = licht
    for o in (hwind, licht):
        for f, w in ((1, 0.0), (40, 1.0)):
            o.rotation_euler.z = w
            o.keyframe_insert("rotation_euler", frame=f)

    def lokal(name):
        return bpy.data.objects.get((name, None))

    namen = ("HKT Baum", "HKT Wind", "HKT Klein", "HKT Kamera", "HKT Hindernis",
             "HKT Hindernis Wind", "HKT Licht")
    bleiben = namen[2:]         # never paused

    def griff(name):
        ad = lokal(name).animation_data
        return ad.action_slot_handle if ad.action_slot is not None else None

    def nutzer():
        return {n: lokal(n).animation_data.action.users for n in namen}

    def lage(name):
        dg = bpy.context.evaluated_depsgraph_get()
        me = lokal(name).evaluated_get(dg).data
        werte = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", werte)
        return werte

    def y(name):
        dg = bpy.context.evaluated_depsgraph_get()
        return lokal(name).evaluated_get(dg).matrix_world.translation.y

    def sh():
        return bpy.context.scene.hydrokit

    def wie_vorher():
        return (all(griff(n) == griffe[n] for n in namen) and nutzer() == vorher_nutzer
                and not sh().is_property_set(sp.STAND))

    griffe, vorher_nutzer = {n: griff(n) for n in namen}, nutzer()
    scene.frame_set(8)
    erwartet8 = lage("HKT Baum")
    scene.frame_set(5)
    erwartet5 = lage("HKT Baum")
    plan = sp.plan_erstellen(scene)
    nicht = dict(plan["nicht"])
    pruefe(sorted(i.name for i in plan["quellen"].values()) == ["HKT Baum", "HKT Wind"]
           and [n for n, _ in plan["schwer"]] == ["HKT Baum"]
           and all(nicht.get(n) == "follows the camera or HyprW"
                   for n in ("HKT Folgt Kamera", "HKT Teilt Hindernis", "HKT Folgt Licht"))
           and nicht.get("HKT Treiber") == "drivers" and "HKT Hindernis" not in nicht,
           f"plan: the heavy mesh and the wind helper it follows; camera, light cube, HyprW's "
           f"obstacle, what it bends with and a light keep moving ({plan['schwer']}, {nicht})")

    # Off: playing pauses nothing
    sh().abspielen_andere_pausieren = False
    sp._pause_beim_abspielen(scene, None)
    aus = not sp.ist_pausiert() and wie_vorher()
    sp._pause_beim_anhalten(scene, None)
    pruefe(aus, "switched off: playing pauses nothing")

    # On: the tree holds the pose of frame 5, the rest moves on
    sh().abspielen_andere_pausieren = True
    sp._pause_beim_abspielen(scene, None)
    pausiert = (griff("HKT Baum") is None and griff("HKT Wind") is None
                and all(griff(n) == griffe[n] for n in bleiben)
                and nutzer() == vorher_nutzer and sh().is_property_set(sp.STAND)
                and lokal("HKT Baum").animation_data.action is not None)
    kamera5, klein5 = y("HKT Kamera"), y("HKT Klein")
    for f in (6, 7, 8):
        scene.frame_set(f)
    pruefe(pausiert and np.array_equal(lage("HKT Baum"), erwartet5)
           and y("HKT Kamera") != kamera5 and y("HKT Klein") != klein5,
           "playing: tree and wind helper keep the pose of frame 5 (slot unassigned, action and "
           "its users kept, record written first); camera and light cube move on")
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    pruefe(_aktiv(p, "prop", "abspielen_andere_pausieren") is True
           and "Paused: 2 objects at frame 5" in _texte(p),
           "panel while playing: the switch, how many objects are paused, at which frame")
    sp._pause_beim_anhalten(scene, None)
    bpy.context.view_layer.update()
    pruefe(wie_vorher() and not sp.ist_pausiert() and np.array_equal(lage("HKT Baum"), erwartet8),
           "stopped at frame 8: every slot back by its handle, record unset, the tree exactly as "
           "without the pause")

    # Saving while playing: the file gets the animation unpaused
    kopie = os.path.join(ARBEIT, "pause_kopie.blend")
    sp._pause_beim_abspielen(scene, None)
    erg = bpy.ops.wm.save_as_mainfile(filepath=kopie, copy=True)
    wieder = griff("HKT Baum") is None and sp.ist_pausiert(scene)
    with bpy.data.libraries.load(kopie, link=True) as (von, nach):
        nach.objects = ["HKT Baum", "HKT Wind"]
        nach.scenes = [scene.name]
    gespeichert = (all(o.animation_data.action_slot is not None
                       and o.animation_data.action_slot_handle == griffe[o.name] for o in nach.objects)
                   and not nach.scenes[0].hydrokit.is_property_set(sp.STAND))
    sp._pause_beim_anhalten(scene, None)
    pruefe(erg == {'FINISHED'} and wieder and gespeichert and wie_vorher(),
           "saved while playing: the copy holds every slot and no record; paused again after saving")

    # Linked data is never touched: the tree linked back from the copy
    gelinkt = next(o for o in nach.objects if o.name == "HKT Baum")
    scene.collection.objects.link(gelinkt)
    gelinkt_griff = gelinkt.animation_data.action_slot_handle
    bpy.context.view_layer.update()
    plan = sp.plan_erstellen(scene)
    sp._pause_beim_abspielen(scene, None)
    unberuehrt = gelinkt.animation_data.action_slot_handle == gelinkt_griff
    sp._pause_beim_anhalten(scene, None)
    pruefe(unberuehrt and ("HKT Baum", "linked") in plan["nicht"] and wie_vorher(),
           "a linked animated tree is reported and never touched")
    scene.collection.objects.unlink(gelinkt)
    bpy.data.libraries.remove(gelinkt.library)

    # Undo after stopping into a step taken while paused: its record sets it back
    sp._pause_beim_abspielen(scene, None)
    bpy.ops.ed.undo_push(message="HKT paused")
    sp._pause_beim_anhalten(scene, None)
    lokal("HKT Klein").location.z = 1.0
    bpy.ops.ed.undo_push(message="HKT after")
    bpy.ops.ed.undo()
    pruefe(wie_vorher() and not sp.ist_pausiert(),
           "undo into a step taken while paused: everything moves again, record unset")
    # Undo and redo while playing: set back, then paused again
    bpy.ops.ed.undo_push(message="HKT before playing")
    lokal("HKT Klein").location.z = 2.0
    bpy.ops.ed.undo_push(message="HKT change")
    sp._pause_beim_abspielen(bpy.context.scene, None)
    bpy.ops.ed.undo()
    nach_undo = sp.ist_pausiert(bpy.context.scene) and griff("HKT Baum") is None
    bpy.ops.ed.redo()
    nach_redo = sp.ist_pausiert(bpy.context.scene) and griff("HKT Wind") is None
    sp._pause_beim_anhalten(bpy.context.scene, None)
    pruefe(nach_undo and nach_redo and wie_vorher(),
           "undo and redo while playing: paused again; stopping sets everything back")
    scene = bpy.context.scene

    # Every other way out
    wege = {}
    for name, ausloeser in (
            ("render", lambda: sp._pause_vor_render(scene)),
            ("load", lambda: sp._pause_vor_laden()),
            ("quit", lambda: sp._pause_beim_beenden()),
            ("setting off", lambda: setattr(sh(), "abspielen_andere_pausieren", False)),
            ("unregister", lambda: (sp.handler_aus(), sp.handler_an())),
            # Playback ended without its stop handler (a closed window)
            ("watchdog", lambda: (sp._ZUSTAND.update(spielt=False), sp._waechter()))):
        sh().abspielen_andere_pausieren = True
        sp._pause_beim_abspielen(scene, None)
        war = sp.ist_pausiert(scene)
        ausloeser()
        wege[name] = war and wie_vorher() and not sp.ist_pausiert()
        sp._pause_beim_anhalten(scene, None)
    sh().abspielen_andere_pausieren = False
    sp._pause_beim_abspielen(scene, None)
    sh().abspielen_andere_pausieren = True
    wege["setting on"] = sp.ist_pausiert(scene) and griff("HKT Baum") is None
    sp._pause_beim_anhalten(scene, None)
    pruefe(all(wege.values()) and wie_vorher(),
           f"set back before a render, loading, quitting, switching off, unregistering and by the "
           f"watchdog; switched on while playing: paused at once ({wege})")
    # A render from a script while playing: set back for it, paused again
    # after it (before, everything kept moving until playback stopped)
    sp._pause_beim_abspielen(scene, None)
    sp._pause_vor_render(scene)
    im_render = not sp.ist_pausiert() and wie_vorher()
    sp._pause_nach_render(scene)
    danach = sp.ist_pausiert(scene) and griff("HKT Baum") is None
    sp._pause_beim_anhalten(scene, None)
    # playback stopped during the render: nothing is paused after it
    sp._pause_beim_abspielen(scene, None)
    sp._pause_vor_render(scene)
    sp._pause_beim_anhalten(scene, None)
    sp._pause_nach_render(scene)
    pruefe(im_render and danach and wie_vorher() and not sp.ist_pausiert(),
           "a render from a script while playing: everything moves in it, paused again after "
           "it; stopped during the render: nothing stays paused")
    # A file saved paused without the handlers (autosave, crash file): the
    # record in it is enough, in a session that knows nothing
    sp._pause_beim_abspielen(scene, None)
    sp._PAUSE.clear()
    sp._pause_nach_laden()
    pruefe(wie_vorher(), "a file opened with a record (autosave, crash): everything moves again")
    # What the user changed while playing stays
    sp._pause_beim_abspielen(scene, None)
    aktion = lokal("HKT Wind").animation_data.action
    lokal("HKT Wind").animation_data.action = None
    sp._pause_beim_anhalten(scene, None)
    eigen = lokal("HKT Wind").animation_data.action is None and griff("HKT Baum") == griffe["HKT Baum"]
    lokal("HKT Wind").animation_data.action = aktion
    lokal("HKT Wind").animation_data.action_slot = aktion.slots[0]
    pruefe(eigen and wie_vorher(), "an action the user removed while playing is not brought back")

    # Measured per frame change; while off the panel points to the setting
    for f in range(10, 15):
        scene.frame_set(f)
    gemessen = sp.anzeige_daten(scene)
    sh().abspielen_andere_pausieren = False
    alt = list(sp._MESSUNG["frei"])
    sp._MESSUNG["frei"][:] = [120.0] * 5
    sp._ANZEIGE.clear()
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
    sp._MESSUNG["frei"][:] = alt
    pruefe(gemessen["gemessen"] and gemessen["objekte"] == 2 and gemessen["schwer"] == 1
           and "Slow playback" in _texte(p) and "2 objects cost ~120 ms per frame" in _texte(p),
           f"frame changes measured ({gemessen['ms']:.1f} ms); off with slow other animation: "
           f"a hint in the closed header and in the section")


# ------------------------------------ playback: the three hooks together
@abschnitt("Playback: Light Playback, light mist and Pause Other Animation together")
def abspielen_zusammen_test(hk):
    """Light Playback (sim/abspielen.py), the light mist
    (sim/abspielen_aussehen.py) and Pause Other Animation (sim/szene_pause.py)
    hang on the same hooks. Playback start switches all three on; stopping,
    saving and opening a file bring all three back in full. The add-on's hooks
    are fired from bpy.app.handlers in their registered order, as Blender
    does, on abspielen_test's bake with a heavy animated mesh beside it."""
    from importlib import import_module
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    abspielen = import_module(hk.__name__ + ".sim.abspielen")
    aussehen = import_module(hk.__name__ + ".sim.aussehen")
    aa = import_module(hk.__name__ + ".sim.abspielen_aussehen")
    sp = import_module(hk.__name__ + ".sim.szene_pause")
    scene = bpy.context.scene
    sh = scene.hydrokit
    wasser = bpy.data.objects.get(anzeige.OBERFLAECHE)
    if wasser is None:
        pruefe(False, "playback together: abspielen_test's water is missing")
        return
    handler_lief = anzeige.handler_laeuft()
    anzeige.handler_an()
    scene.frame_set(5)

    # The mist with its full look, as Apply Look puts it
    nebel = bpy.data.objects.get("HK_Nebel")
    eigener_nebel = nebel is None
    if eigener_nebel:
        nebel = bpy.data.objects.new("HK_Nebel", bpy.data.meshes.new("HK_Nebel"))
    verlinkt = nebel.name not in scene.collection.all_objects
    if verlinkt:
        scene.collection.objects.link(nebel)
    eigener_look = aussehen._haupt_modifier(nebel) is None
    if eigener_look:
        voxel = 0.02
        radius = voxel * aussehen.NEBEL_RADIUS_VOXEL
        dichte = aussehen.NEBEL_OPTISCHE_TIEFE / radius
        mat = aussehen.baue_nebel_material(dichte, 0.03, radius)
        aussehen._modifier_setzen(nebel, aussehen._gruppe_nebel(
            "HK Test Nebel", radius, dichte, mat, voxel))

    def mist_stapel():
        return [(m.name, m.show_viewport, m.show_render) for m in nebel.modifiers]

    stapel_vorher = mist_stapel()

    # Heavy other animation: a 22,500-vertex grid turning by keyframes
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=150, y_subdivisions=150, size=2.0,
                                    location=(6.0, 6.0, 0.0))
    baum = bpy.context.active_object
    baum.name = "HKT Zusammen Gitter"
    for f, winkel in ((1, 0.0), (10, math.pi)):
        baum.rotation_euler = (0.0, 0.0, winkel)
        baum.keyframe_insert("rotation_euler", frame=f)
    griff = baum.animation_data.action_slot_handle
    aktion = baum.animation_data.action

    def feuern(liste, *args):
        """The add-on's hooks on this list, in their order"""
        for h in list(getattr(bpy.app.handlers, liste)):
            if getattr(h, "__module__", "").startswith(hk.__name__):
                h(*args)

    def stand():
        """(water light, its triangles, mist light, grid paused)"""
        return (bool(wasser.data.get(abspielen.MARKE)), len(wasser.data.polygons),
                aussehen.ist_leicht(scene), baum.animation_data.action_slot is None)

    gesehen = {}

    def beim_speichern(*args):
        gesehen["stand"] = stand()
        gesehen["stapel"] = mist_stapel()

    spielt = aa.spielt
    alt = (abspielen.LEICHT_AB_DREIECKE, sh.abspielen_andere_pausieren, sh.abspielen_leicht)
    abspielen.LEICHT_AB_DREIECKE = 0          # the test bake's mesh is small: merge it anyway
    sh.abspielen_leicht, sh.abspielen_andere_pausieren = True, True
    try:
        scene.frame_set(6)
        n_voll = stand()[1]

        # Playback starts: all three on, the water from the next frame
        scene.frame_set(5)
        z5 = baum.rotation_euler.z
        aa.spielt = lambda: True              # no window in background mode
        feuern("animation_playback_pre", scene, bpy.context.evaluated_depsgraph_get())
        scene.frame_set(6)
        an = stand()
        pruefe(an[0] and 0 < an[1] < n_voll and an[2] and an[3]
               and abs(baum.rotation_euler.z - z5) < 1e-6,
               f"playback start: light water ({an[1]} instead of {n_voll} triangles), light "
               f"mist, the heavy mesh paused in the pose of frame 5")

        # Saved while playing: after the save_pre hooks all three are full;
        # after the save mist and pause are light again, the water at the
        # next frame
        bpy.app.handlers.save_pre.append(beim_speichern)
        try:
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ARBEIT, "zusammen.blend"), copy=True)
        finally:
            bpy.app.handlers.save_pre.remove(beim_speichern)
        nach = stand()
        scene.frame_set(7)
        weiter = stand()
        pruefe(gesehen.get("stand") == (False, n_voll, False, False)
               and gesehen.get("stapel") == stapel_vorher
               and nach[2] and nach[3] and weiter[0] and weiter[2] and weiter[3],
               f"saved while playing: water, mist and animation full when written "
               f"{gesehen.get('stand')}; afterwards light and paused again {weiter}")

        # Opening a file ends playback: load_pre and load_post set all three back
        scene.frame_set(6)
        feuern("load_pre", "")
        aa.spielt = lambda: False
        feuern("load_post", "")
        geladen = stand()
        pruefe(geladen == (False, n_voll, False, False) and not abspielen.SPIELT
               and not sp._ZUSTAND["spielt"] and not sp.ist_pausiert()
               and mist_stapel() == stapel_vorher,
               f"opening a file while playing: all three back in full {geladen}")

        # Played again, then stopped: all three back at once
        scene.frame_set(5)
        aa.spielt = lambda: True
        feuern("animation_playback_pre", scene, bpy.context.evaluated_depsgraph_get())
        scene.frame_set(6)
        wieder = stand()
        aa.spielt = lambda: False
        feuern("animation_playback_post", scene, bpy.context.evaluated_depsgraph_get())
        aus = stand()
        pruefe(wieder[0] and wieder[2] and wieder[3]
               and aus == (False, n_voll, False, False)
               and baum.animation_data.action_slot_handle == griff
               and mist_stapel() == stapel_vorher,
               f"stopped: water, mist and animation full at once {aus}, slot back by its handle")
    finally:
        aa.spielt = spielt
        feuern("animation_playback_post", scene, None)
        abspielen.LEICHT_AB_DREIECKE = alt[0]
        sh.abspielen_andere_pausieren, sh.abspielen_leicht = alt[1], alt[2]
        sp._ANZEIGE.clear()
        bpy.data.objects.remove(baum)
        if aktion is not None and not aktion.users:
            bpy.data.actions.remove(aktion)
        if eigener_look:
            for m in list(nebel.modifiers):
                nebel.modifiers.remove(m)
        if eigener_nebel:
            bpy.data.objects.remove(nebel)
        elif verlinkt:
            scene.collection.objects.unlink(nebel)
        if not handler_lief:
            anzeige.handler_aus()


@abschnitt("Light Playback: undo and redo to a step taken while playing")
def abspielen_undo_test(hk):
    """An undo step taken while playing (a setting clicked) holds the light
    water and the thinned whitewater. Undone to after playback had stopped,
    they stayed on display while paused and a save wrote them (r202 cache:
    7,859 instead of 178,220 triangles). The undo hook loads the full frame
    again. A real undo: ed.undo_push and ed.undo work in background mode."""
    from importlib import import_module
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    abspielen = import_module(hk.__name__ + ".sim.abspielen")
    aa = import_module(hk.__name__ + ".sim.abspielen_aussehen")
    namen = [{fn.__name__ for fn in getattr(bpy.app.handlers, liste)}
             for liste in ("undo_post", "redo_post")]
    pruefe(all("_abspielen_nach_undo" in n for n in namen) and bpy.ops.ed.undo_push.poll(),
           "Light Playback's undo and redo hooks installed with the add-on; undo works here")
    if bpy.data.objects.get(anzeige.OBERFLAECHE) is None:
        pruefe(False, "undo while playing: abspielen_test's water is missing")
        return
    scene = bpy.context.scene
    sh = scene.hydrokit
    handler_lief = anzeige.handler_laeuft()
    anzeige.handler_an()
    alt = (abspielen.LEICHT_AB_DREIECKE, sh.abspielen_leicht, sh.abspielen_andere_pausieren,
           scene.render.fps_base)
    spielt = aa.spielt

    def feuern(liste, *args):
        """The add-on's hooks on this list, in their order"""
        for h in list(getattr(bpy.app.handlers, liste)):
            if getattr(h, "__module__", "").startswith(hk.__name__):
                h(*args)

    def wasser():
        w = bpy.data.objects[anzeige.OBERFLAECHE]
        return bool(w.data.get(abspielen.MARKE)), len(w.data.polygons)

    abspielen.LEICHT_AB_DREIECKE = 0          # the test bake's mesh is small: merge it anyway
    sh.abspielen_andere_pausieren = False
    try:
        scene.frame_set(6)
        voll = _anzeige_stand(anzeige)
        n_voll = voll[anzeige.OBERFLAECHE][3]
        scene.frame_set(5)
        bpy.ops.ed.undo_push(message="HK test: paused")
        aa.spielt = lambda: True              # no window in background mode
        feuern("animation_playback_pre", scene, bpy.context.evaluated_depsgraph_get())
        scene.frame_set(6)
        gespielt = wasser()
        sh.abspielen_leicht = True            # a setting clicked while playing
        bpy.ops.ed.undo_push(message="HK test: clicked while playing")
        aa.spielt = lambda: False
        feuern("animation_playback_post", scene, bpy.context.evaluated_depsgraph_get())
        gestoppt = _anzeige_stand(anzeige) == voll
        scene.render.fps_base = 1.0001        # any change after playback
        bpy.ops.ed.undo_push(message="HK test: after playback")
        erg = bpy.ops.ed.undo()
        scene = bpy.context.scene
        pruefe(gespielt[0] and gespielt[1] < n_voll and gestoppt and erg == {'FINISHED'}
               and scene.frame_current == 6 and _anzeige_stand(anzeige) == voll
               and not abspielen.GEZEIGT.get("leicht"),
               f"undo to the step clicked while playing ({gespielt[1]} instead of {n_voll} "
               f"triangles then): paused, frame 6 in full, every attribute as before")
        gesehen = {}

        def beim_speichern(*args):
            gesehen["wasser"] = wasser()

        bpy.app.handlers.save_pre.append(beim_speichern)
        try:
            bpy.ops.wm.save_as_mainfile(filepath=os.path.join(ARBEIT, "undo.blend"), copy=True)
        finally:
            bpy.app.handlers.save_pre.remove(beim_speichern)
        erg = bpy.ops.ed.redo()
        pruefe(gesehen.get("wasser") == (False, n_voll) and erg == {'FINISHED'}
               and _anzeige_stand(anzeige) == voll,
               f"saved after that undo: the full water written {gesehen.get('wasser')}; "
               f"after redo full as well")
    finally:
        aa.spielt = spielt
        feuern("animation_playback_post", bpy.context.scene, None)
        abspielen.LEICHT_AB_DREIECKE = alt[0]
        sh = bpy.context.scene.hydrokit
        sh.abspielen_leicht, sh.abspielen_andere_pausieren = alt[1], alt[2]
        bpy.context.scene.render.fps_base = alt[3]
        if not handler_lief:
            anzeige.handler_aus()


# ----------------------------------------------------- Hybrid Water lever
HYBRID_TECHNIK = ("adaptiv_max_stufe", "adaptiv_teil_faktor", "adaptiv_verschmelz_faktor",
                  "adaptiv_hindernis_abstand")


@abschnitt("Hybrid Water lever")
def hebel_test(hk):
    """One lever for the engine values: position 3 is the old defaults, a
    move sets the coupled values, writing back the same position keeps
    values tuned by hand, and the panel says in metres what it means."""
    from importlib import import_module
    op = import_module(hk.__name__ + ".properties.object_properties")
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")
    ctx = bpy.context
    cube = bpy.data.objects["Cube"]
    d = cube.hydrokit
    rna = d.bl_rna.properties
    alle = ("adaptiv_an", "adaptiv_teilungsdauer") + HYBRID_TECHNIK
    vorher = {n: getattr(d, n) for n in alle}
    for n in alle[1:]:
        setattr(d, n, rna[n].default)

    h = rna["adaptiv_hebel"]
    alt = tuple(rna[n].default for n in HYBRID_TECHNIK)
    pruefe(h.type == 'INT' and (h.hard_min, h.hard_max, h.default) == (1, 6, 3)
           and not h.is_animatable and alt == (3, *op.HYBRID_HEBEL[3])
           and d.adaptiv_hebel == 3 and not op.hybrid_eigen(d),
           f"lever 1..6, not animatable, default 3 = the defaults before the lever {alt}")

    # Every position sets level, depths and obstacle distance; Split Time stays
    d.adaptiv_teilungsdauer = 0.3
    gut, reihe = True, []
    for stufe in (1, 2, 3, 4, 5, 6):
        d.adaptiv_hebel = stufe
        werte = tuple(getattr(d, n) for n in HYBRID_TECHNIK)
        gut &= (d.adaptiv_hebel == stufe and werte[0] == stufe and werte[3] == op.HYBRID_HEBEL[stufe][2]
                and all(abs(a - b) < 1e-6 for a, b in zip(werte[1:3], op.HYBRID_HEBEL[stufe][:2]))
                and abs(d.adaptiv_teilungsdauer - 0.3) < 1e-6 and not op.hybrid_eigen(d))
        reihe.append(op.hybrid_kennzahlen(d, 0.05))
    pruefe(gut, "every lever position sets level, Split/Merge Depth and Obstacle Distance; "
                "Split Time stays")
    # From barely to strongly hybrid: coarser deep water, never a thicker fine
    # layer, a stronger lever never keeps more distance to obstacles
    aufl = [k["aufloesung"] for k in reihe]
    fein = [k["fein_tiefe"] for k in reihe]
    abstand = [k["fein_abstand"] for k in reihe]
    grob = [k["grob_tiefe"] for k in reihe]
    pruefe(all(abs(a - 100 * 2 ** (-(n + 1) / 3)) < 1e-9 for n, a in enumerate(aufl))
           and all(a > b for a, b in zip(aufl, aufl[1:]))
           and all(a >= b for a, b in zip(fein, fein[1:]))
           and all(a >= b for a, b in zip(abstand, abstand[1:]))
           and all(a < b for a, b in zip(grob, grob[1:])),
           "lever monotonic: deep water " + "/".join(f"{a:.0f}" for a in aufl) + " %, fine layer "
           + "/".join(f"{a:.1f}" for a in fein) + " cells, obstacles " + "/".join(map(str, abstand)))
    # The engine's numbers at the old defaults (fluidsimulation.cpp):
    # R1 = 0.866 * 2^(1/3) = 1.091 cells, merge depth 3 R1 + 1 = 4.27,
    # level 3: 3 * 1.732 + 1 = 6.20; obstacles 3 + ceil(R1) + 1 = 6 cells
    d.adaptiv_hebel = 3
    k = op.hybrid_kennzahlen(d, 1.0)
    pruefe(abs(k["fein_tiefe"] - 4.2734) < 1e-3 and abs(k["grob_tiefe"] - 6.1962) < 1e-3
           and k["fein_abstand"] == 6 and k["faktor"] == 8 and abs(k["volumen"] - 1.0) < 1e-9
           and abs(k["aufloesung"] - 50.0) < 1e-9,
           f"position 3 at dx 1 m: fine {k['fein_tiefe']:.2f} m deep and {k['fein_abstand']} m "
           f"from obstacles, largest from {k['grob_tiefe']:.2f} m, 8 x 1/8 m3")

    # A file from before the lever (values tuned by hand): the lever shows the
    # level, writing back the same position keeps them, a move sets its own
    d.adaptiv_max_stufe, d.adaptiv_teil_faktor = 4, 2.5
    d.adaptiv_verschmelz_faktor, d.adaptiv_hindernis_abstand = 3.5, 4
    gleich_eigen = d.adaptiv_hebel == 4 and op.hybrid_eigen(d)
    d.adaptiv_hebel = 4
    gleich_eigen &= (abs(d.adaptiv_teil_faktor - 2.5) < 1e-6 and d.adaptiv_hindernis_abstand == 4
                     and abs(d.adaptiv_verschmelz_faktor - 3.5) < 1e-6)
    d.adaptiv_hebel = 5
    bewegt = (d.adaptiv_max_stufe == 5 and abs(d.adaptiv_teil_faktor - 2.0) < 1e-6
              and d.adaptiv_hindernis_abstand == 3 and not op.hybrid_eigen(d))
    d.adaptiv_hebel = 9
    pruefe(gleich_eigen and bewegt and d.adaptiv_max_stufe == 6,
           "hand-tuned values: lever shows their level and keeps them until it moves; "
           "clamped to 6")

    # Panel: the lever with its meaning in metres; the five values under a
    # closed Advanced, marked custom when they are not the lever's
    d.adaptiv_hebel = 3
    d.adaptiv_an = True
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    texte = _texte(p)
    oben = _stelle(p, "panel", "hk_sim_adaptiv_technik")
    pruefe(_aktiv(p, "prop", "adaptiv_hebel") is True and oben is not None
           and all((_stelle(p, "prop", n) or -1) > oben > _stelle(p, "prop", "adaptiv_hebel")
                   for n in HYBRID_TECHNIK + ("adaptiv_teilungsdauer",))
           and 'panel("hk_sim_adaptiv_technik", default_closed=True)'
           in open(sim_panel.__file__, encoding="utf-8").read(),
           "panel: lever on top, the five values under Advanced (closed)")
    stellen = [t for t in texte if t.startswith(("Deep water", "Largest", "Stays fine", "   and"))]
    pruefe("Deep water: 50 % of the resolution" in texte and "Advanced · custom" not in texte
           and any(t.startswith("Largest particle: 8x volume, ") for t in texte)
           and any(t.startswith("Stays fine ") and t.endswith(" below the surface")
                   and (" cm " in t or " m " in t) for t in texte)
           and any(t.endswith(" from obstacles") for t in texte),
           "panel in plain units: " + " | ".join(stellen))
    d.adaptiv_teil_faktor = 2.4
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    eigen = "Advanced · custom" in _texte(p)
    d.adaptiv_an = False
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(eigen and _aktiv(p, "prop", "adaptiv_hebel") is False
           and _aktiv(p, "prop", "adaptiv_teil_faktor") is False,
           "panel: 'custom' after a change by hand; lever greyed out while Hybrid Water is off")
    for n, w in vorher.items():
        setattr(d, n, w)


@abschnitt("Hybrid Water lever: the bake reads the Advanced values")
def hebel_backen_test(hk):
    """Files from before the lever bake as before: the bake passes the
    five stored values to the engine, whatever the lever shows."""
    from importlib import import_module
    backen = import_module(hk.__name__ + ".sim.backen")
    if not backen.engine_erweiterungen().get("adaptiv"):
        pruefe(False, "engine without adaptive particle sizes")
        return
    bpy.ops.scene.new(type='EMPTY')
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 2
    bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 1.0))
    dom = bpy.context.active_object
    dom.hydrokit.typ = 'DOMAIN'
    bpy.ops.mesh.primitive_cube_add(location=(0.0, 0.0, 0.5), scale=(1.0, 1.0, 0.5))
    bpy.context.active_object.hydrokit.typ = 'FLUID'
    d = dom.hydrokit
    d.aufloesung, d.rechengeraet, d.adaptiv_an = 16, 'CPU', True
    d.cache_pfad = os.path.join(ARBEIT, "cache_hebel")
    d.adaptiv_max_stufe, d.adaptiv_teilungsdauer, d.adaptiv_teil_faktor = 4, 0.3, 2.5
    d.adaptiv_verschmelz_faktor, d.adaptiv_hindernis_abstand = 3.5, 4
    bpy.context.view_layer.update()
    b = backen.Backer(scene)
    if not b.vorbereiten():
        pruefe(False, f"lever bake prepared: {b.fehler}")
        return
    s = b.sim
    werte = (s.enable_adaptive_particles, s.adaptive_particle_max_level,
             round(s.adaptive_particle_split_time, 4), round(s.adaptive_particle_split_factor, 4),
             round(s.adaptive_particle_merge_factor, 4), s.adaptive_particle_solid_distance)
    b.aufraeumen()
    pruefe(werte == (True, 4, 0.3, 2.5, 3.5, 4),
           f"hand-tuned values reach the engine unchanged: {werte}")


# ------------------------------------------------------------ saved states
class _BakeOpAttrappe:
    """Stands in for the modal bake operator in HYDROKIT_OT_sim_bake._beenden,
    which ends every interactive bake and writes its report."""
    _timer = None

    def __init__(self, backer, szene):
        self._backer, self._szene, self.berichte = backer, szene, []

    def report(self, art, text):
        self.berichte.append((set(art), text))


class _SimAttrappe:
    """Takes the load_* calls of zwischenstand.laden and remembers their names."""

    def __init__(self):
        self.aufrufe = []

    def __getattr__(self, name):
        return lambda *a: self.aufrufe.append(name)


@abschnitt("Saved states: failed frames, engine errors, old engines, damaged states")
def zwischenstand_test(hk):
    """Resume goes on from a state that belongs to its frame (review of
    05.11.2026): a failed frame, an engine error included, saves nothing;
    an engine without the resume functions bakes without states; a cut-off
    state is refused, the one a crash leaves between the two renames of a
    save is found; every file reaches the disk before the swap."""
    from importlib import import_module
    backen = import_module(hk.__name__ + ".sim.backen")
    cache = import_module(hk.__name__ + ".sim.cache")
    zs = import_module(hk.__name__ + ".sim.zwischenstand")
    fs = import_module(hk.__name__ + ".ffengine.fluidsimulation")
    sim_ops = import_module(hk.__name__ + ".operators.sim_ops")
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")
    faehig = backen.engine_erweiterungen()
    pruefe(faehig.get("zwischenstand") is True and "Saved states" not in backen.fehlende_erweiterungen(),
           "the loaded engine can save and load states (extension 'Saved states')")
    text = backen.engine_fehler_text(7, RuntimeError("FluidSimulation_update - Error: std::bad_alloc"))
    pruefe(text == "Frame 7: the engine stopped: out of memory (std::bad_alloc)",
           f"an engine error reads '{text}'")
    if not faehig.get("zwischenstand") or not faehig.get("adaptiv"):
        return

    bpy.ops.scene.new(type='EMPTY')
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 8

    def kiste(name, loc, scale, typ):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        o = bpy.context.active_object
        o.name, o.scale = name, scale
        o.hydrokit.typ = typ
        return o

    dom = kiste("Resume Domain", (0.0, 0.0, 1.0), (1.0, 0.5, 1.0), 'DOMAIN')
    d = dom.hydrokit
    d.aufloesung, d.rechengeraet, d.gischt, d.linse_an = 24, 'CPU', False, False
    d.adaptiv_an, d.adaptiv_teilungsdauer = True, 0.1
    kiste("Resume Water", (0.0, 0.0, 0.5), (1.0, 0.5, 0.5), 'FLUID')
    zu = kiste("Resume Inflow", (-0.7, 0.0, 1.6), (0.2, 0.2, 0.2), 'INFLOW')
    zu.hydrokit.zufluss_geschwindigkeit = (2.0, 0.0, 0.0)     # the volume grows every frame
    bpy.context.view_layer.update()

    def einheiten(sim):
        """Water volume in standard particles (level L counts 2^L)."""
        return int(sum(n * 2 ** L for L, n in enumerate(sim.get_adaptive_particle_level_counts())))

    def backer(name, weiter=False, alle=3):
        d.cache_pfad = os.path.join(ARBEIT, "cache_zs_" + name)
        d.zwischenstand_alle = alle
        b = backen.Backer(scene)
        return b, b.vorbereiten(weiter)

    def resume_knopf():
        """(enabled, panel texts) of Resume, the second Bake button."""
        p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
        knoepfe = [a for k, n, a in p if k == "operator" and n == "hydrokit.sim_bake"]
        return (knoepfe[1] if len(knoepfe) > 1 else None), _texte(p)

    def platte_voll(*a, **k):
        raise OSError(28, "No space left on device")

    def funktion_fehlt(*a, **k):
        raise AttributeError("function 'FluidSimulation_get_marker_particle_level_data_range' "
                             "not found")

    gepatcht = []

    def ersetzen(obj, name, neu):
        gepatcht.append((obj, name, getattr(obj, name)))
        setattr(obj, name, neu)

    def zuruecksetzen():
        while gepatcht:
            obj, name, alt = gepatcht.pop()
            setattr(obj, name, alt)

    # The same random numbers in every bake: resumed frames can be compared
    # with the uninterrupted ones
    init = fs.FluidSimulation.__init__

    def mit_saat(self, *a, **k):
        init(self, *a, **k)
        self.set_random_seed(1234)
    neustart = backen._neustart
    try:
        fs.FluidSimulation.__init__ = mit_saat
        b, ok = backer("ganz", alle=100)
        if not ok:
            pruefe(False, f"saved-state bake prepared: {b.fehler}")
            return
        ref = {}
        while True:
            f = b.frame
            w = b.schritt()
            ref[f] = einheiten(b.sim)
            if not w:
                break
        b.aufraeumen()

        # --- frame 6 fails after the engine computed it (disk full while
        # writing its mesh); the modal's error path then cancels with a save
        b, ok = backer("fehler", alle=3)
        while b.frame <= 5:
            b.schritt()
        ersetzen(cache, "mesh_schreiben", platte_voll)
        try:
            b.schritt()
            geworfen = None
        except OSError as e:
            geworfen = e
        finally:
            zuruecksetzen()
        gesichert = b.zwischenstand_sichern()
        st = zs.lesen(d.cache_pfad) or {}
        b.aufraeumen()
        pruefe(geworfen is not None and gesichert is False
               and (st.get("frame"), st.get("engine_frame")) == (3, 3),
               f"frame 6 failed while writing: no save after it, the state stays at frame "
               f"{st.get('frame')} with engine frame {st.get('engine_frame')} (before: frame 5 "
               "holding the particles of frame 6, every resumed frame one ahead)")
        b, ok = backer("fehler", weiter=True, alle=3)
        erster, geladen, nach = b.frame, einheiten(b.sim) if ok else None, {}
        while ok:
            f = b.frame
            w = b.schritt()
            nach[f] = einheiten(b.sim)
            if not w:
                break
        b.aufraeumen()
        abw = max((abs(nach[f] - ref[f]) for f in nach), default=-1)
        zuwachs = min(ref[f + 1] - ref[f] for f in range(3, 8))
        pruefe(ok and erster == 4 and geladen == ref[3] and sorted(nach) == list(range(4, 9))
               and zuwachs > 0 and 0 <= abw * 4 < zuwachs,
               f"resumed at frame {erster} with the {geladen} units of frame 3 ({ref[3]} "
               f"uninterrupted); frames 4-8 differ by at most {abw} units from the "
               f"uninterrupted bake, a frame adds at least {zuwachs}")

        # --- an engine error (an exception in an engine thread arrives the same way)
        b, ok = backer("engine", alle=2)
        while b.frame <= 3:
            b.schritt()                         # state at frame 2

        def kaputt(dt):
            raise RuntimeError("FluidSimulation_update - Error: std::bad_alloc")
        b.sim.update = kaputt
        try:
            b.schritt()
            art, text = None, ""
        except Exception as e:
            art, text = type(e), str(e)
        danach = b.schritt()
        pruefe(art is backen.EngineFehler and issubclass(art, RuntimeError)
               and text == "Frame 4: the engine stopped: out of memory (std::bad_alloc)"
               and danach is False and b.fertig == 3 and b.zwischenstand_sichern() is False
               and (zs.lesen(d.cache_pfad) or {}).get("frame") == 2
               and cache.gebackene_frames(d.cache_pfad) == [1, 2, 3],
               f"engine error in frame 4: EngineFehler '{text}', nothing computed after it, "
               "no save from the broken engine, frames 1-3 and the state of frame 2 stay")
        op = _BakeOpAttrappe(b, scene)
        erg = sim_ops.HYDROKIT_OT_sim_bake._beenden(op, bpy.context, abgebrochen=True)
        letzter = op.berichte[-1] if op.berichte else (set(), "")
        pruefe(erg == {'CANCELLED'} and letzter[0] == {'ERROR'}
               and letzter[1] == ("Frame 4: the engine stopped: out of memory (std::bad_alloc)"
                                  " — 3 frames baked")
               and (zs.lesen(d.cache_pfad) or {}).get("frame") == 2 and not sim_ops.BAKE["laeuft"],
               f"the bake operator ends with the error as its last report: '{letzter[1]}'")
        echt_update = fs.FluidSimulation.update
        zaehler = [0]

        def dritter(self, dt):
            zaehler[0] += 1
            if zaehler[0] == 3:
                raise RuntimeError("FluidSimulation_update - std::bad_array_new_length")
            return echt_update(self, dt)
        ersetzen(fs.FluidSimulation, "update", dritter)
        try:
            d.cache_pfad = os.path.join(ARBEIT, "cache_zs_hintergrund")
            n, _, fehler = backen.backen(scene)
        finally:
            zuruecksetzen()
        pruefe(n == 2 and fehler == ("Frame 3: the engine stopped: out of memory "
                                     "(std::bad_array_new_length)")
               and (zs.lesen(d.cache_pfad) or {}).get("frame") == 2,
               f"background bake: {n} frames, error '{fehler}', the state of frame 2 stays")

        # --- a save that raises anything costs the state, never the bake
        b, ok = backer("schreibfehler", alle=2)
        ersetzen(zs, "schreiben", funktion_fehlt)
        try:
            for _ in range(5):
                b.schritt()
            op = _BakeOpAttrappe(b, scene)
            erg = sim_ops.HYDROKIT_OT_sim_bake._beenden(op, bpy.context, abgebrochen=True)
        finally:
            zuruecksetzen()
        letzter = op.berichte[-1] if op.berichte else (set(), "")
        pruefe(erg == {'CANCELLED'} and letzter[0] == {'WARNING'}
               and letzter[1].startswith("Cancelled after 5 frames")
               and " — no saved state (AttributeError: " in letzter[1]
               and zs.lesen(d.cache_pfad) is None
               and cache.gebackene_frames(d.cache_pfad) == [1, 2, 3, 4, 5],
               f"saves raising AttributeError: all 5 frames baked, the report says so: "
               f"'{letzter[1][:72]}…'")

        # --- an engine without the resume functions (before 05.11.2026 the
        # first save raised AttributeError and ended the bake)
        faehig["zwischenstand"] = False
        backen._neustart = None
        try:
            fehlt = backen.fehlende_erweiterungen()
            b, ok = backer("alt", alle=2)
            while ok and b.schritt():
                pass
            b.aufraeumen()
            alt_frames = cache.gebackene_frames(d.cache_pfad)
            alt_stand = zs.lesen(d.cache_pfad)
            d.cache_pfad = os.path.join(ARBEIT, "cache_zs_engine")       # holds a state
            scene.frame_end = 12
            b = backen.Backer(scene)
            abgelehnt, grund = not b.vorbereiten(True), b.fehler
            knopf, texte = resume_knopf()
        finally:
            faehig["zwischenstand"] = True
            backen._neustart = neustart
            scene.frame_end = 8
        pruefe("Saved states" in fehlt and len(alt_frames) == 8 and alt_stand is None
               and abgelehnt and (grund or "").startswith("This engine cannot resume")
               and knopf is False and "This engine cannot resume" in texte,
               f"engine without the resume functions: all {len(alt_frames)} frames baked "
               f"without a state; Resume greyed out: '{grund}'")

        # --- a state cut off (power cut before its data reached the disk)
        d.cache_pfad = os.path.join(ARBEIT, "cache_zs_ganz")
        st = zs.lesen(d.cache_pfad)
        pos = os.path.join(st["pfad"], "marker_position.data")
        with open(pos, "r+b") as f:
            f.truncate(os.path.getsize(pos) // 2)
        scene.frame_end = 10
        b = backen.Backer(scene)
        abgelehnt, grund = not b.vorbereiten(True), b.fehler
        knopf, texte = resume_knopf()
        pruefe(abgelehnt and grund == "Saved state incomplete (marker_position.data): bake again"
               and knopf is False and grund in texte
               and len(cache.gebackene_frames(d.cache_pfad)) == 8,
               f"a state cut to half is refused before loading: '{grund}'; the frames stay")
        ersetzen(zs, "unvollstaendig", lambda stand: None)     # cut off after the check
        try:
            b = backen.Backer(scene)
            abgelehnt, grund = not b.vorbereiten(True), b.fehler
        finally:
            zuruecksetzen()
        pruefe(abgelehnt and grund == ("Saved state unreadable: saved state truncated: "
                                       "marker_position.data"),
               f"loading reads exactly what info.json counts: '{grund}'")
        with open(os.path.join(st["pfad"], zs.INFO), "w") as f:
            json.dump({"format": zs.FORMAT}, f)
        knopf, texte = resume_knopf()
        pruefe(knopf is False and "Saved state unreadable: bake again" in texte,
               "an info.json without counts: the panel draws, Resume greyed out")
        d.cache_pfad = os.path.join(ARBEIT, "cache_zs_fehler")
        scene.frame_end = 8
        knopf_ende, texte_ende = resume_knopf()
        scene.frame_end = 10
        knopf_weiter, _ = resume_knopf()
        pruefe(knopf_ende is False and "Already baked to frame 8: raise End to go on" in texte_ende
               and knopf_weiter is True,
               "a finished bake: Resume asks to raise End, then goes on")

        # --- a crash between the two renames of a save
        ziel = os.path.join(d.cache_pfad, zs.ORDNER)
        shutil.copytree(ziel, ziel + "_alt")
        os.rename(ziel, ziel + "_neu")
        st_neu = zs.lesen(d.cache_pfad) or {}
        with open(os.path.join(ziel + "_neu", "marker_position.data"), "r+b") as f:
            f.truncate(12)
        st_alt = zs.lesen(d.cache_pfad) or {}
        pruefe(st_neu.get("pfad") == ziel + "_neu" and st_neu.get("frame") == 8
               and st_alt.get("pfad") == ziel + "_alt",
               "a crash between the renames: the new state is found, the old one when the "
               "new is incomplete")
        scene.frame_end = 9
        gezaehlt = []
        echt_fsync = os.fsync

        def zaehlen(fd):
            gezaehlt.append(fd)
            return echt_fsync(fd)
        b, ok = backer("fehler", weiter=True, alle=3)
        ersetzen(os, "fsync", zaehlen)
        try:
            w = b.schritt() if ok else None
        finally:
            zuruecksetzen()
        b.aufraeumen()
        st = zs.lesen(d.cache_pfad) or {}
        dateien = len(os.listdir(st["pfad"])) if st else 0
        reste = sorted(n for n in os.listdir(d.cache_pfad) if n.startswith(zs.ORDNER))
        pruefe(ok and w is False and st.get("frame") == 9 and reste == [zs.ORDNER]
               and dateien > 0 and len(gezaehlt) == dateien + 2,
               f"resumed from it to frame {st.get('frame')}; the save fsyncs its {dateien} "
               f"files and both folders ({len(gezaehlt)} calls); left: {reste}")
        for rest in ("_neu", "_alt"):
            os.makedirs(ziel + rest)
        cache.leeren(d.cache_pfad)
        pruefe(not any(n.startswith(zs.ORDNER) for n in os.listdir(d.cache_pfad)),
               "Bake Again and Clear Cache also delete what a crashed save left")
        scene.frame_end = 8

        # --- whitewater switched off before Resume: none loaded (the engine
        # would never move it again, and every later state would save it)
        ordner = os.path.join(ARBEIT, "zs_attrappe")
        os.makedirs(ordner)
        stand = {"pfad": ordner, "wasser": 2, "gischt": 3, "apic": False, "stufen": True,
                 "engine_frame": 5}
        for name, groesse in zs.WASSER + zs.STUFE:
            with open(os.path.join(ordner, name + ".data"), "wb") as f:
                f.write(b"\0" * 2 * groesse)
        for name, groesse in zs.GISCHT:
            with open(os.path.join(ordner, name + ".data"), "wb") as f:
                f.write(b"\0" * 3 * groesse)
        mit, ohne = _SimAttrappe(), _SimAttrappe()
        zs.laden(mit, stand)
        zs.laden(ohne, stand, gischt=False)
        pruefe(zs.unvollstaendig(stand) is None and "load_diffuse_particle_data" in mit.aufrufe
               and "load_diffuse_particle_data" not in ohne.aufrufe
               and "load_marker_particle_data" in ohne.aufrufe,
               "whitewater off: Resume loads the water without the saved whitewater")

        # --- jobs on the same cache block each other, also from the operator
        # search (the panel hides these buttons while baking)
        frei = bpy.ops.hydrokit.sim_bake.poll() and bpy.ops.hydrokit.sim_clear.poll()
        sim_ops.LINSE_LAEUFT = True
        bei_linse = bpy.ops.hydrokit.sim_bake.poll() or bpy.ops.hydrokit.sim_clear.poll()
        sim_ops.LINSE_LAEUFT = False
        sim_ops.BAKE["laeuft"] = True
        d.linse_an = True
        bpy.ops.object.camera_add(location=(0.0, -4.0, 1.0))
        kamera = bpy.context.active_object
        scene.camera = kamera
        beim_backen = (bpy.ops.hydrokit.sim_bake.poll() or bpy.ops.hydrokit.sim_clear.poll()
                       or bpy.ops.hydrokit.linse_berechnen.poll())
        sim_ops.BAKE["laeuft"] = False
        linse_frei = bpy.ops.hydrokit.linse_berechnen.poll()
        d.linse_an = False
        bpy.data.objects.remove(kamera)
        pruefe(frei and linse_frei and not bei_linse and not beim_backen,
               "while the lens drops compute: no bake, no Clear Cache; while baking: no "
               "Clear Cache, no lens drops; free again after both")

        # --- Resolution: the slider ends at 2000, typing goes on to 8192; the
        # panel warns when even the low estimate exceeds this computer's memory
        rna = d.bl_rna.properties["aufloesung"]
        pruefe(rna.soft_max == 2000 and rna.hard_max == 8192,
               f"Resolution slider up to {rna.soft_max}, typed up to {rna.hard_max}")
        ram = sim_panel._arbeitsspeicher()
        d.aufloesung = 1400
        masse = backen.geometrie.gitter_masse(dom, d.aufloesung)
        voxel = masse[0] * masse[1] * masse[2]
        texte = _texte(zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context))
        d.aufloesung = 24
        warnung = any(t.startswith("More than this computer's ") for t in texte)
        pruefe(ram > 0 and not backen.geometrie.gitter_zu_gross(masse)
               and warnung == (voxel * 150 > ram),
               f"Resolution 1400 ({voxel / 1e9:.2f} billion cells, {voxel * 150 / 1024**3:.0f} GB "
               f"at least) on {ram / 1024**3:.0f} GB: memory warning "
               + ("shown" if warnung else "not shown"))
    finally:
        zuruecksetzen()
        sim_ops.LINSE_LAEUFT = False
        sim_ops.BAKE["laeuft"] = False
        fs.FluidSimulation.__init__ = init
        faehig["zwischenstand"] = True
        backen._neustart = neustart


# ------------------------------------------------ bake in a worker thread
class _Ereignis:
    def __init__(self, typ, wert='NOTHING'):
        self.type, self.value = typ, wert


@abschnitt("Bake in a worker thread: Blender stays usable")
def hintergrund_test(hk):
    """The interactive bake computes its frames in a worker thread
    (backen.Hintergrund) and the modal operator only looks whether it is
    done: the same cache as in one go, the main thread free meanwhile, a
    cancel that waits for the frame, errors raised on the main thread, and
    the scene found again after a rename or not at all once deleted."""
    import collections
    import threading
    import time as zeit
    import types
    from importlib import import_module
    backen = import_module(hk.__name__ + ".sim.backen")
    cache = import_module(hk.__name__ + ".sim.cache")
    zs = import_module(hk.__name__ + ".sim.zwischenstand")
    fs = import_module(hk.__name__ + ".ffengine.fluidsimulation")
    sim_ops = import_module(hk.__name__ + ".operators.sim_ops")
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")

    bpy.ops.scene.new(type='EMPTY')
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 6

    def kiste(name, loc, scale, typ):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        o = bpy.context.active_object
        o.name, o.scale = name, scale
        o.hydrokit.typ = typ
        return o

    dom = kiste("Faden Domain", (0.0, 0.0, 1.0), (1.0, 1.0, 1.0), 'DOMAIN')
    d = dom.hydrokit
    d.aufloesung, d.rechengeraet, d.gischt, d.linse_an = 40, 'CPU', False, False
    d.zwischenstand_an, d.zwischenstand_alle = True, 4
    kiste("Faden Water", (0.0, 0.0, 0.7), (0.5, 0.5, 0.5), 'FLUID')
    kiste("Faden Block", (0.4, 0.3, 0.2), (0.2, 0.2, 0.2), 'OBSTACLE')
    bpy.context.view_layer.update()

    def backer(name):
        d.cache_pfad = os.path.join(ARBEIT, "cache_faden_" + name)
        b = backen.Backer(scene)
        return b, b.vorbereiten()

    def dateien():
        ordner = os.path.join(d.cache_pfad, cache.FRAMES)
        erg = {}
        for name in sorted(os.listdir(ordner)):
            with open(os.path.join(ordner, name), "rb") as f:
                erg[name] = f.read()
        return erg

    def laufen(lauf, bis=None, sekunden=180.0):
        """tick() as the operator's timer calls it, until FERTIG or bis()."""
        zahl = collections.Counter()
        ende = zeit.time() + sekunden
        z = None
        while zeit.time() < ende:
            z = lauf.tick()
            zahl[z] += 1
            if z == lauf.FERTIG or (bis is not None and bis(z)):
                break
            if z == lauf.RECHNET:
                zeit.sleep(0.002)
        return z, zahl

    init = fs.FluidSimulation.__init__

    def mit_saat(self, *a, **k):
        init(self, *a, **k)
        self.set_random_seed(1234)
    schreiben = cache.mesh_schreiben
    try:
        fs.FluidSimulation.__init__ = mit_saat
        # --- in one go, as scripts bake: the reference
        b, ok = backer("ganz")
        if not ok:
            pruefe(False, f"worker-thread bake prepared: {b.fehler}")
            return
        while b.schritt():
            pass
        b.abschliessen()
        b.aufraeumen()
        ref = dateien()

        # --- the same bake through the worker thread
        b, ok = backer("faden")
        faeden = []
        rechnen = b.schritt_rechnen

        def gemerkt(dt):
            faeden.append(threading.current_thread())
            rechnen(dt)
        b.schritt_rechnen = gemerkt
        lauf = backen.Hintergrund(b)
        z, zahl = laufen(lauf)
        fertig = b.fertig
        b.abschliessen()
        b.aufraeumen()
        neu = dateien()
        st = zs.lesen(d.cache_pfad) or {}
        pruefe(z == lauf.FERTIG and not lauf.abgebrochen and fertig == 6 and len(faeden) == 6
               and threading.main_thread() not in faeden and zahl[lauf.RECHNET] > 6,
               f"6 frames computed off the main thread, which was free meanwhile "
               f"({zahl[lauf.RECHNET]} idle timer calls)")
        pruefe(sorted(ref) == sorted(neu) and all(ref[n] == neu[n] for n in ref)
               and not any(n.endswith(".tmp") for n in neu),
               f"the cache is the same byte for byte as baked in one go ({len(neu)} files)")
        pruefe(st.get("frame") == 6, "the bake leaves its saved state at the last frame")

        # --- cancelled while frame 3 computes: the frame is kept, then the end
        b, ok = backer("abbruch")
        lauf = backen.Hintergrund(b)
        laufen(lauf, bis=lambda z: b.fertig >= 2)
        lauf.abbrechen = True
        rechnete = lauf._faden is not None and lauf._art == 'frame'
        z, _ = laufen(lauf)
        n_abbruch = b.fertig
        b.abschliessen()
        b.aufraeumen()
        st = zs.lesen(d.cache_pfad) or {}
        pruefe(rechnete and z == lauf.FERTIG and lauf.abgebrochen and n_abbruch == 3
               and cache.gebackene_frames(d.cache_pfad) == [1, 2, 3] and st.get("frame") == 3,
               f"cancelled while frame 3 computed: it is kept, the bake ends after it with "
               f"{n_abbruch} frames and the saved state of frame {st.get('frame')}")

        # --- an error in the worker is raised on the main thread
        b, ok = backer("fehler")
        lauf = backen.Hintergrund(b)
        laufen(lauf, bis=lambda z: b.fertig >= 1)

        def platte_voll(*a, **k):
            raise OSError(28, "No space left on device")
        # frame 2 computes by now; its write (or the next frame's) fails
        cache.mesh_schreiben = platte_voll
        geworfen = None
        try:
            laufen(lauf)
        except OSError as e:
            geworfen = e
        cache.mesh_schreiben = schreiben
        gesichert = b.zwischenstand_sichern()
        n_fehler = b.fertig
        b.aufraeumen()
        pruefe(geworfen is not None and gesichert is False and n_fehler < 6,
               f"disk full in the worker: raised on the main thread after {n_fehler} frames, "
               "no saved state from the failed frame")

        # --- the operator's modal loop: Esc while a frame computes
        K = sim_ops.HYDROKIT_OT_sim_bake
        b, ok = backer("modal")
        op = _BakeOpAttrappe(b, scene)
        for name in ("modal", "_beenden", "_stand_merken", "_status"):
            setattr(op, name, types.MethodType(getattr(K, name), op))
        op._neu_zeichnen = K._neu_zeichnen
        op._lauf = backen.Hintergrund(b)
        sim_ops.BAKE.update(laeuft=True, abbrechen=False, szene=scene.name, start=b.frame,
                            sekunden=0.0)
        op._stand_merken()
        op._zeiten = collections.deque(maxlen=sim_ops.ZEIT_FRAMES)
        op._letzter = zeit.time()
        ende = zeit.time() + 180.0
        erg = None
        while b.fertig < 2 and zeit.time() < ende:
            erg = op.modal(bpy.context, _Ereignis('TIMER'))
            zeit.sleep(0.002)
        durch = erg == {'PASS_THROUGH'}             # other events reach Blender
        maus = op.modal(bpy.context, _Ereignis('MOUSEMOVE'))
        esc = op.modal(bpy.context, _Ereignis('ESC', 'PRESS'))
        beim_abbruch = sim_ops.BAKE["laeuft"] and sim_ops.BAKE["abbrechen"]
        p = zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context)
        knopf = _aktiv(p, "operator", "hydrokit.sim_stop")
        while erg not in ({'CANCELLED'}, {'FINISHED'}) and zeit.time() < ende:
            erg = op.modal(bpy.context, _Ereignis('TIMER'))
            zeit.sleep(0.002)
        letzter = op.berichte[-1] if op.berichte else (set(), "")
        st = zs.lesen(d.cache_pfad) or {}
        pruefe(durch and maus == {'PASS_THROUGH'} and esc == {'RUNNING_MODAL'} and beim_abbruch
               and knopf is False,
               "while a frame computes the operator passes every event on; Esc is taken, the "
               "bake goes on to the end of the frame and Cancel is greyed out meanwhile")
        pruefe(erg == {'CANCELLED'} and letzter[0] == {'WARNING'}
               and letzter[1].startswith("Cancelled after 3 frames")
               and not sim_ops.BAKE["laeuft"] and op._backer is None and st.get("frame") == 3
               and cache.gebackene_frames(d.cache_pfad) == [1, 2, 3],
               f"then it ends: '{letzter[1]}', frames 1-3 and their saved state kept")

        # --- the scene is looked up anew: renamed it is found, deleted the bake ends
        b, ok = backer("szene")
        name_alt = scene.name
        scene.name = "HKT Faden umbenannt"
        gefunden = b.scene == scene and b.schritt()
        scene.name = name_alt
        b._szene = (-1, "HKT gibt es nicht")        # as after deleting the scene
        ohne = b.scene is None and b.schritt_beginnen() is None and b.schritt() is False
        b.abschliessen()
        text, _ = b.gpu_bericht()
        b.aufraeumen()
        pruefe(gefunden and ohne,
               "the baked scene is found again after renaming; once it is gone the bake ends "
               "without an error")
    finally:
        cache.mesh_schreiben = schreiben
        fs.FluidSimulation.__init__ = init
        sim_ops.BAKE.update(laeuft=False, abbrechen=False, sekunden=0.0)


# ------------------------------------------- liquid: viscosity, tension
@abschnitt("Liquid: viscosity and surface tension")
def fluessigkeit_test(hk):
    """Measured liquids set three values; the bake passes them per kilogram
    to the engine; off leaves the engine as before; honey flows slower than
    water; the panel says what the values mean here."""
    from importlib import import_module
    backen = import_module(hk.__name__ + ".sim.backen")
    cache = import_module(hk.__name__ + ".sim.cache")
    op = import_module(hk.__name__ + ".properties.object_properties")
    sim_panel = import_module(hk.__name__ + ".ui.sim_panel")

    bpy.ops.scene.new(type='EMPTY')
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, 12

    def kiste(name, loc, scale, typ):
        bpy.ops.mesh.primitive_cube_add(location=loc)
        o = bpy.context.active_object
        o.name, o.scale = name, scale
        o.hydrokit.typ = typ
        return o

    # a dam break: a block of liquid at one end of a flat box
    dom = kiste("Liquid Domain", (0.0, 0.0, 0.25), (1.0, 0.25, 0.25), 'DOMAIN')
    d = dom.hydrokit
    d.aufloesung, d.rechengeraet, d.gischt, d.linse_an = 40, 'CPU', False, False
    d.zwischenstand_an = False
    kiste("Liquid Block", (-0.75, 0.0, 0.2), (0.24, 0.24, 0.19), 'FLUID')
    bpy.context.view_layer.update()

    d.fluessigkeit = 'HONIG'
    honig = (d.zaehigkeit, d.dichte, d.oberflaechenspannung)
    eigen_vorher = op.fluessigkeit_eigen(d)
    d.zaehigkeit = 12.0
    eigen_nachher = op.fluessigkeit_eigen(d)
    d.fluessigkeit = 'WASSER'
    wasser = (d.zaehigkeit, d.dichte, d.oberflaechenspannung)
    def gleich(a, b):       # Blender keeps floats in single precision
        return all(abs(x - y) <= 1e-6 * abs(y) for x, y in zip(a, b))
    pruefe(gleich(honig, op.FLUESSIGKEITEN['HONIG'][1:]) and gleich(wasser, (0.0010, 998.0, 0.0727))
           and not eigen_vorher and eigen_nachher and not op.fluessigkeit_eigen(d),
           f"a liquid sets viscosity, density and surface tension {tuple(round(x, 6) for x in honig)}; changed by hand "
           "it counts as custom")

    def vorbereitet(name):
        d.cache_pfad = os.path.join(ARBEIT, "cache_fluessig_" + name)
        b = backen.Backer(scene)
        ok = b.vorbereiten()
        return b, ok

    # off: nothing reaches the engine
    b, ok = vorbereitet("aus")
    aus = ok and b.sim.viscosity == 0.0 and b.sim.surface_tension == 0.0
    b.aufraeumen()
    # on: per kilogram
    d.fluessigkeit = 'HONIG'
    d.zaehigkeit_an = d.oberflaechenspannung_an = True
    b, ok = vorbereitet("an")
    nu, k = (b.sim.viscosity, b.sim.surface_tension) if ok else (0.0, 0.0)
    kondition = b.sim.surface_tension_condition_number if ok else None
    schritte = b.sim.max_time_steps_per_frame if ok else 0
    masse = backen.geometrie.gitter_masse(dom, d.aufloesung)
    noetig = backen.oberflaechen_schritte(masse[3], k, 1.0 / scene.render.fps) if k else 0
    b.aufraeumen()
    pruefe(kondition == backen.OBERFLAECHE_KONDITION and schritte >= noetig,
           f"surface tension: step limit {kondition} (FLIP Fluids' default accuracy), up to "
           f"{schritte} steps per frame where it needs {noetig}")
    pruefe(aus and abs(nu - 10.0 / 1420.0) < 1e-9 and abs(k - 0.064 / 1420.0) < 1e-10,
           f"off: no viscosity, no surface tension in the engine; on: {nu:.4g} m²/s and "
           f"{k:.4g} m³/s² (honey per kilogram)")

    # behaviour: the front of a dam break after 12 frames
    def front(name, liquid, an):
        d.fluessigkeit = liquid
        d.zaehigkeit_an, d.oberflaechenspannung_an = an, False
        b, ok = vorbereitet(name)
        if not ok:
            return None
        while b.schritt():
            pass
        b.aufraeumen()
        daten = cache.mesh_lesen_np(d.cache_pfad, 12)
        return float(daten[0][0::3].max()) if daten is not None and len(daten[0]) else None

    x_wasser = front("wasser", 'WASSER', False)
    x_honig = front("honig", 'HONIG', True)
    pruefe(x_wasser is not None and x_honig is not None and x_honig < x_wasser - 0.2,
           f"dam break after 12 frames: water reaches x = {x_wasser:.2f} m, honey "
           f"{x_honig:.2f} m" if x_wasser is not None and x_honig is not None
           else "dam break baked for water and honey")

    # panel: what the values mean here
    d.fluessigkeit = 'HONIG'
    d.zaehigkeit_an = d.oberflaechenspannung_an = True
    texte = _texte(zeichnen(sim_panel.HYDROKIT_PT_sim, bpy.context))
    pruefe(any("x as viscous as water" in t for t in texte)
           and any(t.startswith("About ") or t.startswith("Too coarse") for t in texte)
           and any(t.startswith("Liquid: Honey") for t in texte),
           "panel: the liquid in the header, how viscous, and what surface tension costs here")
    d.zaehigkeit_an = d.oberflaechenspannung_an = False


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
        hebel_test(hk)
        linse_test(hk)
        bake_test(hk)
        netz_detail_test(hk)
        aniso_netz_test(hk)
        turbulenz_test(hk)
        abspielen_test(hk)
        abspielen_zusammen_test(hk)
        abspielen_undo_test(hk)
        material_stand_test(hk)
        studio_wasser_test(hk)
        leicht_nebel_test(hk)
        adaptiv_test(hk)
        gelaende_test(hk)
        szene_pause_test(hk)
        hebel_backen_test(hk)
        zwischenstand_test(hk)
        hintergrund_test(hk)
        fluessigkeit_test(hk)
        try:
            import addon_utils
            addon_utils.disable(hk.__name__, default_set=True,
                                handle_error=lambda ex: FEHLER.append(
                                    "Disabling: Traceback\n" + traceback.format_exc()))
            pruefe(not hasattr(bpy.types.Scene, "hydrokit"),
                   "add-on disabled (unregister() without errors)")
            pruefe(not any(getattr(h, "__name__", "").startswith(("_aussehen_", "_abspielen_",
                                                                  "_pause_"))
                           for liste in ("animation_playback_pre", "animation_playback_post",
                                         "frame_change_pre", "frame_change_post", "save_pre",
                                         "save_post", "load_pre", "load_post", "undo_post",
                                         "redo_post", "render_init", "render_complete",
                                         "render_cancel", "exit_pre")
                           for h in getattr(bpy.app.handlers, liste)),
                   "add-on disabled: no playback handlers left (light mist, Light Playback, "
                   "Pause Other Animation)")
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
