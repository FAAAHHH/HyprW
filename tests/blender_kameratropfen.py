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
    main_panel = import_module(hk.__name__ + ".ui.main_panel")
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
    pruefe(_aktiv(p, "panel", "hk_sim_linse") is not None, "own section hk_sim_linse")
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

    bpy.context.scene.hydrokit.backt = True
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(_aktiv(p, "operator", "hydrokit.sim_stop") is True, "sim panel while baking")
    bpy.context.scene.hydrokit.backt = False

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
    pruefe(_aktiv(p, "operator", "hydrokit.rechengeraet_messen") is not None,
           "compute device Both: 'Pick the Fastest per Step' button")
    d.rechengeraet = 'CPU'
    p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
    pruefe(_aktiv(p, "prop", "geraet_mesh") is None, "compute device CPU: no per-step list")
    d.rechengeraet = 'GPU'

    sim_ops = import_module(hk.__name__ + ".operators.sim_ops")
    sim_ops.LINSE_LAEUFT = True
    bpy.context.scene.hydrokit.linse_fortschritt = 0.4
    try:
        p = zeichnen(sim_panel.HYDROKIT_PT_sim, ctx)
        pruefe(any(k == "label" and "40" in n for k, n, _ in p)
               and _aktiv(p, "operator", "hydrokit.linse_berechnen") is None,
               "sim panel while lens drops compute: progress instead of the button")
    finally:
        sim_ops.LINSE_LAEUFT = False

    p = zeichnen(main_panel.HYDROKIT_PT_main, ctx)
    melden(f"main panel (FLIP Fluids): {[n for k, n, _ in p if k == 'label']}")


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
    # The update function of the look sliders swallows errors; so call it directly
    aussehen = import_module(hk.__name__ + ".sim.aussehen")
    scene.frame_set(6)
    erledigt = aussehen.anwenden(bpy.context)
    melden(f"aussehen.anwenden on real cache: {erledigt}")

    # --- drawn size spread and velocity for motion blur
    anzeige_m = import_module(hk.__name__ + ".sim.anzeige")
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
    sh.wasser_variante = 'KLAR'              # the variant fills the measured sliders
    wasser = bpy.data.objects[anzeige_mod.OBERFLAECHE].data.materials[0]
    streu = [n for n in wasser.node_tree.nodes if n.type == 'VOLUME_SCATTER']
    pruefe(abs(sh.wasser_sichtweite - 10.75) < 1e-6 and wasser.name == "HK Wasser Klar"
           and streu and abs(streu[0].inputs["Anisotropy"].default_value - 0.924) < 1e-6
           and bpy.context.scene.cycles.volume_bounces >= 16,
           f"water variant sets visibility and bounces ({sh.wasser_sichtweite} m, "
           f"{bpy.context.scene.cycles.volume_bounces} volume bounces)")
    sh.wasser_variante = 'GEBIRGSBACH'
    d.spray_farbe = (0.5, 0.7, 1.0)          # update re-applies the look
    sp = knoten("HK Spray", 'BSDF_PRINCIPLED')
    pruefe(sp and tuple(round(x, 3) for x in sp[0].inputs["Base Color"].default_value[:3])
           == (0.5, 0.7, 1.0), "spray colour reaches the material")
    d.schaum_blasen = 0
    pruefe(knoten("HK Foam", 'TEX_VORONOI') == [], "foam bubbles 0: plain foam")
    d.schaum_blasen = 4
    schaum_ng = bpy.data.node_groups.get("HK Foam")
    pruefe(schaum_ng is not None and any(n.bl_idname == "GeometryNodeStoreNamedAttribute"
                                         for n in schaum_ng.nodes),
           "points store their centre (texture travels with the particle)")

    # --- results are click-through, so clicks reach the objects inside the domain
    anzeige = import_module(hk.__name__ + ".sim.anzeige")
    da = [bpy.data.objects[n] for n in anzeige.ERGEBNISSE if n in bpy.data.objects]
    pruefe(len(da) >= 2 and all(o.hide_select for o in da),
           f"result objects click-through by default ({len(da)} objects)")
    scene.hydrokit.ergebnisse_waehlbar = True
    pruefe(not any(o.hide_select for o in da), "Selectable Results on: all selectable")
    scene.hydrokit.ergebnisse_waehlbar = False
    pruefe(all(o.hide_select for o in da), "Selectable Results off again: click-through")

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
