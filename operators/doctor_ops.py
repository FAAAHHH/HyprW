# HyprW — scene doctor
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Checks a FLIP Fluids scene for the mistakes that cost hours most often in
# practice — and does so BEFORE baking.

import os
import bpy
from ..utils import flip_bridge

# Rough estimate of the memory needed per voxel (bytes).
# FLIP needs several floating point fields per cell plus particles; whitewater
# comes on top. The values come from comparison with real bakes.
BYTES_PRO_VOXEL = 1024
BYTES_PRO_VOXEL_WHITEWATER = 1536

SCHWERE = {'INFO': 0, 'WARNUNG': 1, 'FEHLER': 2}


class Befund:
    def __init__(self, schwere, titel, text, fix=None):
        self.schwere = schwere
        self.titel = titel
        self.text = text
        self.fix = fix          # operator idname or None


def _verfuegbarer_ram():
    """Available memory in bytes, or None."""
    try:
        with open("/proc/meminfo") as f:
            for zeile in f:
                if zeile.startswith("MemAvailable:"):
                    return int(zeile.split()[1]) * 1024
    except OSError:
        pass
    return None


def pruefe_speicher(context):
    grid = flip_bridge.get_grid_dimensions(context)
    props = flip_bridge.get_domain_props(context)
    if grid is None or props is None:
        return None
    *_, voxel_gesamt = grid
    whitewater = getattr(props.whitewater, "enable_whitewater_simulation", False)
    pro_voxel = BYTES_PRO_VOXEL_WHITEWATER if whitewater else BYTES_PRO_VOXEL
    bedarf = voxel_gesamt * pro_voxel
    frei = _verfuegbarer_ram()

    bedarf_gb = bedarf / 1024 ** 3
    text = f"Estimated {bedarf_gb:.1f} GB"
    if whitewater:
        text += " (with whitewater)"
    if frei is None:
        return Befund('INFO', "Memory needed", text)

    frei_gb = frei / 1024 ** 3
    text += f", available {frei_gb:.1f} GB"
    if bedarf > frei:
        return Befund('FEHLER', "Not enough memory", text +
                      " — lower the resolution or bake on a machine with more RAM")
    if bedarf > frei * 0.75:
        return Befund('WARNUNG', "Memory tight", text +
                      " — little headroom, close other programs")
    return Befund('INFO', "Enough memory", text)


def pruefe_hindernisdicke(context):
    """Obstacles thinner than ~3 voxels let the simulation fall through."""
    voxel = flip_bridge.get_voxel_size(context)
    if not voxel:
        return None
    mindest = voxel * 3
    duenne = []
    for obj in context.scene.objects:
        ff = getattr(obj, "flip_fluid", None)
        if ff is None or getattr(ff, "object_type", "") != "TYPE_OBSTACLE":
            continue
        dicke = min(obj.dimensions)          # thinnest extent
        solidify = next((m for m in obj.modifiers if m.type == 'SOLIDIFY'), None)
        if solidify is not None:
            dicke = max(dicke, abs(solidify.thickness))
        if dicke < mindest:
            duenne.append(f"{obj.name} ({dicke * 100:.1f} cm)")
    if not duenne:
        return Befund('INFO', "Obstacles thick enough",
                      f"Minimum thickness {mindest * 100:.1f} cm met")
    return Befund('FEHLER', "Obstacle too thin",
                  f"At least {mindest * 100:.1f} cm needed — "
                  "otherwise water falls through: " + ", ".join(duenne[:3]))


def pruefe_cfl(context):
    props = flip_bridge.get_domain_props(context)
    if props is None:
        return None
    cfl = getattr(props.advanced, "CFL_condition_number", None)
    if cfl is None:
        return None
    if cfl >= 5:
        return Befund('WARNUNG', "CFL high",
                      f"Value {cfl}: particles may jump {cfl} voxels per step "
                      "and can skip thin obstacles. "
                      "2 recommended for fast water",
                      fix="hydrokit.fix_cfl")
    return Befund('INFO', "CFL fine", f"Value {cfl}")


def pruefe_massstab(context):
    dom = flip_bridge.get_domain(context)
    if dom is None:
        return None
    groesse = max(dom.dimensions)
    if groesse > 15:
        return Befund('WARNUNG', "Domain very large",
                      f"Longest side {groesse:.1f} m. Water behaves "
                      "scale-dependently — at this size it looks sluggish. "
                      "2-4 m is realistic for a stream")
    if groesse < 0.2:
        return Befund('WARNUNG', "Domain very small",
                      f"Longest side {groesse * 100:.0f} cm. Surface tension "
                      "matters here — enable it under World")
    return Befund('INFO', "Scale plausible", f"Longest side {groesse:.1f} m")


def pruefe_cache(context):
    pfad = flip_bridge.get_cache_directory(context)
    if not pfad:
        return Befund('WARNUNG', "No cache path", "Domain has no cache directory")
    if not os.path.isdir(pfad):
        # Before the first bake the folder does not exist yet — that is normal.
        # Only if it should be missing NEXT TO a saved file and the file
        # already exists is it a real problem.
        blend = bpy.data.filepath
        if not blend:
            return Befund('WARNUNG', "File not saved",
                          "The cache ends up in the temp folder and can get "
                          "lost. Save first, then bake")
        return Befund('INFO', "Not baked yet",
                      f"The cache is created when baking under {os.path.basename(pfad)}")

    # Is the cache in a volatile directory?
    tempdir = os.path.realpath(bpy.app.tempdir or "/tmp")
    echt = os.path.realpath(pfad)
    if echt.startswith(tempdir) or echt.startswith("/tmp"):
        return Befund('FEHLER', "Cache in temp folder",
                      f"{pfad} is in the working directory and can be deleted "
                      "at any time. Move it next to the .blend file")

    bakefiles = os.path.join(pfad, "bakefiles")
    anzahl = len(os.listdir(bakefiles)) if os.path.isdir(bakefiles) else 0
    leer = 0
    if os.path.isdir(bakefiles):
        for name in os.listdir(bakefiles):
            try:
                if os.path.getsize(os.path.join(bakefiles, name)) == 0:
                    leer += 1
            except OSError:
                pass
    if leer:
        return Befund('WARNUNG', "Empty cache files",
                      f"{leer} files with 0 bytes. They count as 'finished' and "
                      "are skipped — created by a cancelled bake")
    return Befund('INFO', "Cache fine", f"{anzahl} files in {pfad}")


def pruefe_gischt(context):
    props = flip_bridge.get_domain_props(context)
    if props is None:
        return None
    an = getattr(props.whitewater, "enable_whitewater_simulation", False)
    if not an:
        return Befund('INFO', "Whitewater off",
                      "Whitewater is part of the simulation — bake again to enable it")
    objekte = flip_bridge.get_whitewater_objects(context)
    if not objekte:
        return Befund('WARNUNG', "Whitewater on, but nothing baked",
                      "The setting is active, but there is no data")

    voxel = flip_bridge.get_voxel_size(context)
    zu_klein = []
    for art, obj in objekte.items():
        groesse = flip_bridge.get_whitewater_particle_scale(obj)
        if groesse is not None and voxel and groesse < voxel * 0.05:
            zu_klein.append(f"{art} ({groesse * 100:.2f} cm)")
    if zu_klein:
        return Befund('WARNUNG', "Whitewater barely visible",
                      "Particles much smaller than the voxel size: "
                      + ", ".join(zu_klein),
                      fix="hydrokit.auto_whitewater_scale")
    # Objects can exist without any data in the cache
    hat_daten = any(len(o.data.vertices) > 0 for o in objekte.values()
                    if o.type == 'MESH')
    if not hat_daten:
        return Befund('INFO', "Whitewater objects created",
                      f"{len(objekte)} types prepared, no particles in the current "
                      "frame yet")
    return Befund('INFO', "Whitewater present", f"{len(objekte)} types with data")


PRUEFUNGEN = (pruefe_speicher, pruefe_hindernisdicke, pruefe_cfl,
              pruefe_massstab, pruefe_cache, pruefe_gischt)


def untersuche(context):
    """Run all checks, most severe first."""
    befunde = []
    for pruefung in PRUEFUNGEN:
        try:
            b = pruefung(context)
        except Exception as e:                      # never lock up the UI
            b = Befund('WARNUNG', "Check failed",
                       f"{pruefung.__name__}: {type(e).__name__}: {e}")
        if b is not None:
            befunde.append(b)
    befunde.sort(key=lambda b: -SCHWERE[b.schwere])
    return befunde


# Results are cached: untersuche() reads files and must NOT run on every
# redraw of the panel, or the UI stutters.
_letzte_befunde = []
_letzter_lauf = None


def befunde_holen():
    """The most recent findings — without a new check."""
    return _letzte_befunde


def letzter_lauf():
    return _letzter_lauf


def auffrischen(context):
    global _letzte_befunde, _letzter_lauf
    import time
    _letzte_befunde = untersuche(context)
    _letzter_lauf = time.time()
    return _letzte_befunde


class HYDROKIT_OT_fix_cfl(bpy.types.Operator):
    bl_idname = "hydrokit.fix_cfl"
    bl_label = "Set CFL to 2"
    bl_description = "Lowers the safety factor so particles do not skip obstacles"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        props = flip_bridge.get_domain_props(context)
        if props is None:
            return {'CANCELLED'}
        props.advanced.CFL_condition_number = 2
        self.report({'INFO'}, "CFL set to 2 — needs a new bake")
        return {'FINISHED'}


class HYDROKIT_OT_run_doctor(bpy.types.Operator):
    bl_idname = "hydrokit.run_doctor"
    bl_label = "Check Scene"
    bl_description = "Looks for typical problems before baking"

    def execute(self, context):
        befunde = auffrischen(context)
        fehler = sum(1 for b in befunde if b.schwere == 'FEHLER')
        warn = sum(1 for b in befunde if b.schwere == 'WARNUNG')
        for b in befunde:
            if b.schwere != 'INFO':
                print(f"[HyprW] {b.schwere}: {b.titel} — {b.text}")
        if fehler:
            # deliberately WARNING instead of ERROR: a diagnostic tool must not
            # abort the call (bpy.ops raises an exception on ERROR)
            self.report({'WARNING'}, f"{fehler} errors, {warn} warnings")
        elif warn:
            self.report({'WARNING'}, f"{warn} warnings")
        else:
            self.report({'INFO'}, "No problems found")
        context.scene.hydrokit.doctor_lief = True
        return {'FINISHED'}


classes = (HYDROKIT_OT_fix_cfl, HYDROKIT_OT_run_doctor)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
