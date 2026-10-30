# HyprW — operating HyprW's own simulation
# SPDX-License-Identifier: GPL-3.0-or-later
#
# These operators work on HyprW's OWN simulation layer
# (hydrokit/sim/), not on FLIP Fluids. They need no FLIP add-on.

import os
import time

import bpy

from ..sim import anzeige, backen, cache, geometrie


class HYDROKIT_OT_set_type(bpy.types.Operator):
    bl_idname = "hydrokit.set_type"
    bl_label = "Assign Role"
    bl_description = "Marks the selected object for the simulation"
    bl_options = {'REGISTER', 'UNDO'}

    typ: bpy.props.StringProperty(default='NONE')

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        if self.typ == 'DOMAIN':
            vorhanden = geometrie.finde_domain(context.scene)
            if vorhanden is not None and vorhanden is not obj:
                self.report({'WARNING'},
                            f"'{vorhanden.name}' is already the domain. "
                            "Clear its role there first")
                return {'CANCELLED'}
        vorher = obj.hydrokit.typ
        obj.hydrokit.typ = self.typ
        if self.typ in geometrie.HILFSROLLEN:
            # Wireframe only: a solid white box where the initial water sits
            # would otherwise hide the baked water in the viewport.
            obj.display_type = 'WIRE'
            obj.hide_render = True
            geometrie.strahlen_setzen(obj, False)
        elif vorher in geometrie.HILFSROLLEN:
            # back to a normal object
            obj.display_type = 'TEXTURED'
            obj.hide_render = False
            geometrie.strahlen_setzen(obj, True)
        self.report({'INFO'}, f"{obj.name}: {self.typ}")
        return {'FINISHED'}


class HYDROKIT_OT_sim_bake(bpy.types.Operator):
    bl_idname = "hydrokit.sim_bake"
    bl_label = "Bake"
    bl_description = ("Runs the simulation with HyprW's own engine. "
                      "Works step by step, Blender stays usable")

    _timer = None
    _backer = None

    @classmethod
    def poll(cls, context):
        return geometrie.finde_domain(context.scene) is not None

    def execute(self, context):
        # In background mode there is no timer: compute in one go
        n, dauer, fehler = backen.backen(context.scene)
        if fehler:
            self.report({'ERROR'}, fehler)
            return {'CANCELLED'}
        self.report({'INFO'}, f"{n} frames in {dauer:.1f}s")
        return {'FINISHED'}

    def invoke(self, context, event):
        self._backer = backen.Backer(context.scene)
        if not self._backer.vorbereiten():
            self.report({'ERROR'}, self._backer.fehler or "Preparation failed")
            return {'CANCELLED'}
        context.scene.hydrokit.backt = True
        context.scene.hydrokit.bake_fortschritt = 0.0
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.hydrokit
        if event.type == 'ESC' or not props.backt:
            return self._beenden(context, abgebrochen=True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        try:
            weiter = self._backer.schritt()
        except Exception as e:
            self.report({'ERROR'}, f"{type(e).__name__}: {e}")
            return self._beenden(context, abgebrochen=True)
        props.bake_fortschritt = self._backer.fertig / max(self._backer.gesamt, 1)
        for bereich in context.screen.areas:
            if bereich.type == 'VIEW_3D':
                bereich.tag_redraw()
        if not weiter:
            return self._beenden(context)
        return {'RUNNING_MODAL'}

    def _beenden(self, context, abgebrochen=False):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        props = context.scene.hydrokit
        props.backt = False
        props.bake_fortschritt = 0.0
        n, dauer = self._backer.fertig, self._backer.dauer
        verdickt = (self._backer.info or {}).get("verdickt", [])
        gpu_text, gpu_warnung = self._backer.gpu_bericht()
        self._backer.aufraeumen()
        self._backer = None
        anzeige.handler_an()
        if context.scene.camera is not None and \
                geometrie.finde_domain(context.scene).hydrokit.linse_an:
            linse_im_hintergrund_starten()     # step by step, Blender stays usable
        anzeige.frame_laden(context.scene)
        if abgebrochen:
            self.report({'WARNING'}, f"Cancelled after {n} frames")
            return {'CANCELLED'}
        teile = [f"{n} frames in {dauer:.1f}s"]
        if gpu_text:
            teile.append(gpu_text)
        if verdickt:
            teile.append(f"open obstacle(s) given 3 cells of thickness: {', '.join(verdickt)}")
        self.report({'WARNING'} if (verdickt or gpu_warnung) else {'INFO'}, " — ".join(teile))
        return {'FINISHED'}


class HYDROKIT_OT_sim_stop(bpy.types.Operator):
    bl_idname = "hydrokit.sim_stop"
    bl_label = "Cancel"
    bl_description = "Stops the running bake"

    def execute(self, context):
        context.scene.hydrokit.backt = False
        return {'FINISHED'}


class HYDROKIT_OT_sim_clear(bpy.types.Operator):
    bl_idname = "hydrokit.sim_clear"
    bl_label = "Clear Cache"
    bl_description = "Deletes all baked frames of this scene"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return geometrie.finde_domain(context.scene) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        dom = geometrie.finde_domain(context.scene)
        pfad = cache.pfad_aufloesen(dom.hydrokit.cache_pfad)
        n = len(cache.gebackene_frames(pfad))
        cache.leeren(pfad)
        anzeige.frame_laden(context.scene)
        self.report({'INFO'}, f"{n} frames deleted")
        return {'FINISHED'}


class HYDROKIT_OT_sim_display(bpy.types.Operator):
    bl_idname = "hydrokit.sim_display"
    bl_label = "Refresh Display"
    bl_description = ("Loads the current frame from the cache and keeps the "
                      "display up to date while scrubbing")

    def execute(self, context):
        anzeige.handler_an()
        erg = anzeige.frame_laden(context.scene)
        if erg is None:
            self.report({'WARNING'}, "Nothing baked")
            return {'CANCELLED'}
        teile = ", ".join(f"{k.replace('HK_','')}: {v}" for k, v in erg.items())
        self.report({'INFO'}, teile or "leer")
        return {'FINISHED'}


class HYDROKIT_OT_sim_look(bpy.types.Operator):
    bl_idname = "hydrokit.sim_look"
    bl_label = "Apply Look"
    bl_description = (
        "Gives water and whitewater materials and geometry. Without it the "
        "particles are bare vertices and render black. Does not need a "
        "new bake"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return geometrie.finde_domain(context.scene) is not None

    def execute(self, context):
        from ..sim import aussehen
        erledigt = aussehen.anwenden(context)
        if not erledigt:
            self.report({'WARNING'}, "Nothing found — bake first")
            return {'CANCELLED'}
        self.report({'INFO'}, ", ".join(f"{k} {v}" for k, v in erledigt.items()))
        return {'FINISHED'}


# Runs a step-by-step lens computation right now? Module state, not a scene
# property: a property saved as True by a crash would block it forever.
LINSE_LAEUFT = False


class HYDROKIT_OT_linse_berechnen(bpy.types.Operator):
    bl_idname = "hydrokit.linse_berechnen"
    bl_label = "Compute Lens Drops"
    bl_description = ("Computes from the baked cache which drops land on the "
                      "camera lens, stick and slide down. No new bake "
                      "needed — run again after every slider change")

    @classmethod
    def poll(cls, context):
        domain = geometrie.finde_domain(context.scene)
        return (domain is not None and domain.hydrokit.linse_an
                and context.scene.camera is not None)

    _timer = None
    _schritte = None

    def execute(self, context):
        # In one go — for scripts and background mode
        from ..sim import linse
        n_frames, n_tropfen = linse.berechnen(context.scene)
        if not n_frames:
            self.report({'WARNING'}, "Nothing baked or no camera")
            return {'CANCELLED'}
        anzeige.frame_laden(context.scene)
        self.report({'INFO'}, f"{n_frames} frames, {n_tropfen} drops on the lens at the end")
        return {'FINISHED'}

    def invoke(self, context, event):
        # Step by step, so Blender stays usable; Esc cancels
        from ..sim import linse
        global LINSE_LAEUFT
        props = context.scene.hydrokit
        if LINSE_LAEUFT:
            return {'CANCELLED'}
        LINSE_LAEUFT = True
        self._schritte = linse.berechnen_schritte(context.scene)
        props.linse_rechnet = True
        props.linse_fortschritt = 0.0
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        props = context.scene.hydrokit
        if event.type == 'ESC':
            return self._fertig(context, None, abgebrochen=True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        start = time.time()
        try:
            while time.time() - start < 0.05:      # about 20 UI updates per second
                f, n, gesamt = next(self._schritte)
                props.linse_fortschritt = (f - context.scene.frame_start + 1) / max(gesamt, 1)
        except StopIteration as ende:
            return self._fertig(context, ende.value or (0, 0))
        except Exception as e:
            self.report({'ERROR'}, f"{type(e).__name__}: {e}")
            return self._fertig(context, None, abgebrochen=True)
        # only the sidebar with the progress bar: redrawing the whole viewport
        # (water, mist) every step halved the compute speed
        for bereich in context.screen.areas:
            if bereich.type == 'VIEW_3D':
                for region in bereich.regions:
                    if region.type == 'UI':
                        region.tag_redraw()
        return {'RUNNING_MODAL'}

    def _fertig(self, context, ergebnis, abgebrochen=False):
        global LINSE_LAEUFT
        LINSE_LAEUFT = False
        if self._schritte is not None:
            self._schritte.close()                 # restores frame and display
            self._schritte = None
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        props = context.scene.hydrokit
        props.linse_rechnet = False
        props.linse_fortschritt = 0.0
        anzeige.frame_laden(context.scene)
        if abgebrochen:
            self.report({'WARNING'}, "Lens drops cancelled")
            return {'CANCELLED'}
        n_frames, n_tropfen = ergebnis
        if not n_frames:
            self.report({'WARNING'}, "Nothing baked or no camera")
            return {'CANCELLED'}
        self.report({'INFO'}, f"{n_frames} frames, {n_tropfen} drops on the lens at the end")
        return {'FINISHED'}


def linse_im_hintergrund_starten():
    """Start the step-by-step lens computation from code that must not block
    or has no suitable context (a property update, the end of a bake)."""
    def starten():
        wm = bpy.context.window_manager
        for fenster in wm.windows:
            for bereich in fenster.screen.areas:
                if bereich.type == 'VIEW_3D':
                    with bpy.context.temp_override(window=fenster, area=bereich,
                                                   screen=fenster.screen):
                        if bpy.ops.hydrokit.linse_berechnen.poll():
                            bpy.ops.hydrokit.linse_berechnen('INVOKE_DEFAULT')
                    return None
        return None
    bpy.app.timers.register(starten, first_interval=0.05)


# --- Fastest device per step -------------------------------------------------
#
# A probe bake in a separate Blender process (sim/geraete_probe.py) on a copy
# of the file: every step runs both ways on the same input and is timed. The
# user's Blender stays usable, their file and cache are not touched.

PROBE_FRAMES = 4
MESSUNG = {"laeuft": False, "frame": 0}     # for the panel


def _messung_anwenden(domain, ergebnis):
    """Pick the faster device per step from a probe result. Returns (text, warning)."""
    import json
    d = domain.hydrokit
    namen = dict((e[0], e[1]) for e in backen.STUFEN)
    teile = []
    for key, werte in ergebnis.get("steps", {}).items():
        t_gpu, t_cpu = werte["gpu_seconds"], werte["cpu_seconds"]
        # the GPU only if it was faster AND never had to fall back
        gpu_besser = werte["gpu_calls"] > 0 and werte["cpu_fallbacks"] == 0 and t_gpu < t_cpu
        setattr(d, "geraet_" + key, 'GPU' if gpu_besser else 'CPU')
        if gpu_besser:
            teile.append(f"{namen[key]} GPU {t_cpu / t_gpu:.1f}x")
        elif t_cpu > 0:
            teile.append(f"{namen[key]} CPU {t_gpu / t_cpu:.1f}x" if t_gpu > 0 else f"{namen[key]} CPU")
    d.rechengeraet = 'BEIDES'
    d.rechengeraet_messung = json.dumps({"device": ergebnis.get("device", ""),
                                         "steps": ergebnis.get("steps", {})})
    return "Fastest: " + ", ".join(teile), False


def _probe_starten():
    """Save a copy and start the probe process. Returns (process, folder, result path)."""
    import subprocess, tempfile
    ordner = tempfile.mkdtemp(prefix="hk_probe_")
    kopie = os.path.join(ordner, "probe.blend")
    ergebnis = os.path.join(ordner, "probe.json")
    bpy.ops.wm.save_as_mainfile(filepath=kopie, copy=True, check_existing=False)
    skript = os.path.join(os.path.dirname(os.path.dirname(__file__)), "sim", "geraete_probe.py")
    modul = __package__.rpartition(".")[0]
    log = open(os.path.join(ordner, "probe.log"), "w")
    prozess = subprocess.Popen(
        [bpy.app.binary_path, "-b", "--factory-startup", kopie, "--python", skript, "--",
         modul, ergebnis, os.path.join(ordner, "cache"), str(PROBE_FRAMES)],
        stdout=log, stderr=subprocess.STDOUT)
    log.close()
    return prozess, ordner, ergebnis


def _probe_lesen(ordner, ergebnis):
    import json, shutil
    try:
        with open(ergebnis) as f:
            daten = json.load(f)
    except (OSError, ValueError):
        daten = {"error": "The probe ended without a result"}
    shutil.rmtree(ordner, ignore_errors=True)
    return daten


class HYDROKIT_OT_rechengeraet_messen(bpy.types.Operator):
    """Measure every step on the GPU and on the CPU with a short probe bake of a
    copy of this file (in the background, about 15-60 s), then set each step to
    the faster one"""
    bl_idname = "hydrokit.rechengeraet_messen"
    bl_label = "Pick the Fastest per Step"

    _timer = None
    _prozess = None
    _ordner = None
    _ergebnis = None

    @classmethod
    def poll(cls, context):
        return (geometrie.finde_domain(context.scene) is not None
                and not context.scene.hydrokit.backt and not MESSUNG["laeuft"])

    def _ohne_gpu(self, domain):
        """No usable GPU: everything on the CPU, nothing to measure."""
        grund = backen.gpu_info()[2]
        for key, *_ in backen.STUFEN:
            setattr(domain.hydrokit, "geraet_" + key, 'CPU')
        domain.hydrokit.rechengeraet = 'BEIDES'
        domain.hydrokit.rechengeraet_messung = ""
        self.report({'WARNING'}, f"No usable GPU ({grund}): every step on the CPU")
        return {'FINISHED'}

    def execute(self, context):
        # Scripts and tests: wait for the probe
        domain = geometrie.finde_domain(context.scene)
        if not backen.gpu_info()[0]:
            return self._ohne_gpu(domain)
        prozess, ordner, ergebnis = _probe_starten()
        try:
            prozess.wait(timeout=1800)
        except Exception:
            prozess.kill()
        return self._fertig(context, _probe_lesen(ordner, ergebnis))

    def invoke(self, context, event):
        domain = geometrie.finde_domain(context.scene)
        if not backen.gpu_info()[0]:
            return self._ohne_gpu(domain)
        self._prozess, self._ordner, self._ergebnis = _probe_starten()
        MESSUNG["laeuft"] = True
        MESSUNG["frame"] = 0
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.5, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type == 'ESC':
            self._prozess.kill()
            self._aufraeumen(context)
            _probe_lesen(self._ordner, self._ergebnis)
            self.report({'WARNING'}, "Measurement cancelled")
            return {'CANCELLED'}
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        # Progress from the probe's log: "HKPROBE frame k/n"
        try:
            with open(os.path.join(self._ordner, "probe.log")) as f:
                zeilen = [z for z in f.read().splitlines() if z.startswith("HKPROBE frame")]
            if zeilen:
                MESSUNG["frame"] = int(zeilen[-1].split()[2].split("/")[0])
        except (OSError, ValueError, IndexError):
            pass
        for bereich in context.screen.areas:
            if bereich.type == 'VIEW_3D':
                for region in bereich.regions:
                    if region.type == 'UI':
                        region.tag_redraw()
        if self._prozess.poll() is None:
            return {'RUNNING_MODAL'}
        self._aufraeumen(context)
        return self._fertig(context, _probe_lesen(self._ordner, self._ergebnis))

    def _aufraeumen(self, context):
        MESSUNG["laeuft"] = False
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

    def _fertig(self, context, daten):
        if daten.get("error"):
            self.report({'ERROR'}, "Measurement failed: " + daten["error"])
            return {'CANCELLED'}
        domain = geometrie.finde_domain(context.scene)
        text, warnung = _messung_anwenden(domain, daten)
        self.report({'WARNING'} if warnung else {'INFO'}, text)
        return {'FINISHED'}


classes = (HYDROKIT_OT_set_type, HYDROKIT_OT_linse_berechnen, HYDROKIT_OT_sim_bake, HYDROKIT_OT_sim_stop,
           HYDROKIT_OT_sim_clear, HYDROKIT_OT_sim_display,
           HYDROKIT_OT_sim_look, HYDROKIT_OT_rechengeraet_messen)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
