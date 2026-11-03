# HyprW — operating HyprW's own simulation
# SPDX-License-Identifier: GPL-3.0-or-later
#
# These operators work on HyprW's OWN simulation layer
# (hydrokit/sim/), not on FLIP Fluids. They need no FLIP add-on.

import collections
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

    @classmethod
    def description(cls, context, properties):
        """Each role button explains its role, a taken Domain who has it."""
        if properties.typ == 'NONE':
            return "Removes the role"
        if properties.typ == 'DOMAIN':
            vorhanden = geometrie.finde_domain(context.scene)
            if vorhanden is not None and vorhanden is not context.active_object:
                return f"'{vorhanden.name}' is already the domain. Clear its role there first"
        typen = bpy.types.Object.bl_rna.properties["hydrokit"].fixed_type.bl_rna.properties["typ"]
        eintrag = typen.enum_items.get(properties.typ)
        return eintrag.description if eintrag is not None else cls.bl_description

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


def dauer_text(sekunden):
    s = int(round(sekunden))
    if s < 60:
        return f"{s} s"
    m, s = divmod(s, 60)
    if m < 60:
        return f"{m} min {s:02d} s"
    h, m = divmod(m, 60)
    return f"{h} h {m:02d} min"


def anzahl(n, wort):
    """'1 frame', '2 frames'."""
    return f"{n} {wort}" + ("" if n == 1 else "s")


# The running bake. Module state like LINSE_LAEUFT, not scene properties: an
# undo step during a bake brought back an older Scene.hydrokit.backt — the
# bake stopped, or a baking box stayed after it with nothing running.
# frame: the one being computed; start: where the bake began; ende: the
# scene's End, live (Backer.schritt follows an End changed meanwhile).
BAKE = {"laeuft": False, "abbrechen": False, "szene": "", "frame": 0,
        "start": 1, "ende": 1, "sekunden": 0.0}


def bake_stand():
    """(frame line, time line) while baking — in the panel and the status
    bar. The frame is the one being computed, numbered as in the timeline;
    a range that does not start at 1 also counts from its start."""
    frame, start, ende, sek = BAKE["frame"], BAKE["start"], BAKE["ende"], BAKE["sekunden"]
    if start == 1:
        zeile = f"Baking frame {frame} of {ende}"
    else:
        zeile = f"Baking frame {frame} ({frame - start + 1} of {ende - start + 1})"
    if sek <= 0.0:
        return zeile, ""
    rest = max(ende - frame + 1, 0) * sek
    return zeile, f"{sek:.1f} s per frame, about {dauer_text(rest)} left"


# Frames the time left is averaged over: the waterfall's inflow keeps adding
# water and whitewater, so later frames take longer than the first ones
ZEIT_FRAMES = 10


class HYDROKIT_OT_sim_bake(bpy.types.Operator):
    bl_idname = "hydrokit.sim_bake"
    bl_label = "Bake"
    bl_description = ("Runs the simulation with HyprW's own engine. "
                      "Works step by step, Blender stays usable")

    _timer = None
    _backer = None
    _szene = None           # the baked scene, also when the window shows another
    _zeiten = None          # seconds of the last frames (ZEIT_FRAMES)
    _letzter = 0.0

    @classmethod
    def poll(cls, context):
        domain = geometrie.finde_domain(context.scene)
        if domain is None or BAKE["laeuft"]:
            return False
        if not cache.pfad_aufloesen(domain.hydrokit.cache_pfad):
            cls.poll_message_set(backen.cache_fehlt_text(domain))
            return False
        return True

    def execute(self, context):
        # In background mode there is no timer: compute in one go
        n, dauer, fehler = backen.backen(context.scene)
        if fehler:
            self.report({'ERROR'}, fehler)
            return {'CANCELLED'}
        self.report({'INFO'}, f"{anzahl(n, 'frame')} in {dauer:.1f}s")
        return {'FINISHED'}

    def invoke(self, context, event):
        self._backer = backen.Backer(context.scene)
        if not self._backer.vorbereiten():
            self.report({'ERROR'}, self._backer.fehler or "Preparation failed")
            return {'CANCELLED'}
        self._szene = context.scene
        BAKE.update(laeuft=True, abbrechen=False, szene=context.scene.name,
                    start=self._backer.frame, sekunden=0.0)
        self._stand_merken()
        self._zeiten = collections.deque(maxlen=ZEIT_FRAMES)
        self._letzter = time.time()
        self._status(context)
        wm = context.window_manager
        self._timer = wm.event_timer_add(0.01, window=context.window)
        wm.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _stand_merken(self):
        BAKE["frame"] = self._backer.frame
        BAKE["ende"] = self._szene.frame_end

    def modal(self, context, event):
        # Only the press: after Esc closed a menu or aborted a move, its
        # release reached the bake as well and cancelled it
        if (event.type == 'ESC' and event.value == 'PRESS') or BAKE["abbrechen"]:
            return self._beenden(context, abgebrochen=True)
        if event.type != 'TIMER':
            return {'PASS_THROUGH'}
        try:
            weiter = self._backer.schritt()
        except Exception as e:
            self.report({'ERROR'}, f"{type(e).__name__}: {e}")
            return self._beenden(context, abgebrochen=True)
        jetzt = time.time()
        self._zeiten.append(jetzt - self._letzter)
        self._letzter = jetzt
        BAKE["sekunden"] = sum(self._zeiten) / len(self._zeiten)
        self._stand_merken()
        self._status(context)
        for bereich in context.screen.areas:
            if bereich.type == 'VIEW_3D':
                bereich.tag_redraw()
        if not weiter:
            return self._beenden(context)
        return {'RUNNING_MODAL'}

    def cancel(self, context):
        """Opening a file ends the bake without modal(): only this runs. The
        GPU memory goes back, the cache keeps what was baked so far."""
        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None
        BAKE.update(laeuft=False, abbrechen=False, sekunden=0.0)
        if self._backer is not None:
            try:
                self._backer.abschliessen()
                self._backer.aufraeumen()
            except Exception as e:
                print(f"HyprW: cleaning up the bake ({e})")
            self._backer = None

    def _status(self, context):
        """The frame in Blender's status bar too, visible with the sidebar closed."""
        frame, zeit = bake_stand()
        text = f"HyprW: {frame}" + (f", {zeit}" if zeit else "") + "  (Esc cancels)"
        try:
            context.workspace.status_text_set(text)
        except AttributeError:
            pass

    def _beenden(self, context, abgebrochen=False):
        wm = context.window_manager
        if self._timer is not None:
            wm.event_timer_remove(self._timer)
            self._timer = None
        try:
            context.workspace.status_text_set(None)
        except AttributeError:
            pass
        BAKE.update(laeuft=False, abbrechen=False, sekunden=0.0)
        szene = self._szene
        n, dauer = self._backer.fertig, self._backer.dauer
        verdickt = (self._backer.info or {}).get("verdickt", [])
        gpu_text, gpu_warnung = self._backer.gpu_bericht()
        # before the lens job: its drops count as current when they are
        # newer than info.json (linse.ist_aktuell)
        self._backer.abschliessen()
        self._backer.aufraeumen()
        self._backer = None
        anzeige.handler_an()
        anzeige.frame_laden(szene)
        # The lens job and the look work on the window's scene: only while
        # it still shows the baked one (else Apply Look there later)
        gleich = context.scene == szene
        domain = geometrie.finde_domain(szene)
        if gleich and szene.camera is not None and domain is not None and domain.hydrokit.linse_an:
            linse_im_hintergrund_starten()     # step by step, Blender stays usable
        # New whitewater gets its look at once: bare points render black
        from ..sim import aussehen
        look = False
        if gleich:
            try:
                look = aussehen.nach_bake(context)
            except Exception as e:
                print(f"HyprW: no look after the bake ({e}); press Apply Look")
        if abgebrochen:
            self.report({'WARNING'}, f"Cancelled after {anzahl(n, 'frame')}"
                                     + (" — look applied" if look else ""))
            return {'CANCELLED'}
        teile = [f"{anzahl(n, 'frame')} in {dauer:.1f}s"]
        if look:
            teile.append("look applied")
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
        BAKE["abbrechen"] = True        # the bake's modal ends on its next event
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
        dom = geometrie.finde_domain(context.scene)
        n = len(cache.gebackene_frames(cache.pfad_aufloesen(dom.hydrokit.cache_pfad)))
        return context.window_manager.invoke_confirm(
            self, event, title="Delete Bake",
            message=f"Delete {'the' if n == 1 else 'all'} {anzahl(n, 'baked frame')}?",
            confirm_text="Delete", icon='WARNING')

    def execute(self, context):
        dom = geometrie.finde_domain(context.scene)
        pfad = cache.pfad_aufloesen(dom.hydrokit.cache_pfad)
        n = len(cache.gebackene_frames(pfad))
        cache.leeren(pfad)
        # frame_laden stops without info.json: emptied here, else the
        # viewport and every render kept showing the deleted frame
        anzeige.ergebnisse_leeren()
        self.report({'INFO'}, f"{anzahl(n, 'frame')} deleted")
        return {'FINISHED'}


class HYDROKIT_OT_sim_display(bpy.types.Operator):
    bl_idname = "hydrokit.sim_display"
    bl_label = "Refresh Display"
    bl_description = ("Loads the current frame from the cache and makes the "
                      "viewport follow the timeline again")

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


def gewaehltes_wasser(context):
    """The baked water, if it is selected or active: Apply Water acts on the
    water clicked on purpose, like a material on the selected object. It
    stays clickable while foam, spray and mist are click-through
    (sim/anzeige.py, waehlbar_machen)."""
    flaeche = bpy.data.objects.get(anzeige.OBERFLAECHE)
    if flaeche is None:
        return None
    # The view layer's active object: the 3D view's active_object is None
    # for a hidden one
    if context.view_layer.objects.active is flaeche:
        return flaeche
    try:
        return flaeche if flaeche.select_get() else None
    except RuntimeError:
        return None         # not in the current view layer


class HYDROKIT_OT_sim_water(bpy.types.Operator):
    bl_idname = "hydrokit.sim_water"
    bl_label = "Apply Water"
    bl_description = (
        "Gives the selected water the chosen water — click the water in the "
        "viewport first. Foam, bubbles, spray and mist stay as they are. Does "
        "not need a new bake"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if bpy.data.objects.get(anzeige.OBERFLAECHE) is None:
            cls.poll_message_set("No water yet: bake first")
            return False
        if gewaehltes_wasser(context) is None:
            cls.poll_message_set("Click the water in the viewport first")
            return False
        return True

    def execute(self, context):
        from ..sim import aussehen
        name = aussehen.wasser_anwenden(context)
        if name is None:
            self.report({'WARNING'}, "No water yet: bake first")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Water: {name}")
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
        if event.type == 'ESC' and event.value == 'PRESS':
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
                and not BAKE["laeuft"] and not MESSUNG["laeuft"])

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
        if event.type == 'ESC' and event.value == 'PRESS':
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

    def cancel(self, context):
        """Opening a file ends the measurement without modal(): stop the probe
        and remove its copy of the file."""
        if self._prozess is not None:
            self._prozess.kill()
            self._aufraeumen(context)
            _probe_lesen(self._ordner, self._ergebnis)

    def _fertig(self, context, daten):
        if daten.get("error"):
            self.report({'ERROR'}, "Measurement failed: " + daten["error"])
            return {'CANCELLED'}
        domain = geometrie.finde_domain(context.scene)
        text, warnung = _messung_anwenden(domain, daten)
        self.report({'WARNING'} if warnung else {'INFO'}, text)
        return {'FINISHED'}


classes = (HYDROKIT_OT_set_type, HYDROKIT_OT_linse_berechnen, HYDROKIT_OT_sim_bake, HYDROKIT_OT_sim_stop,
           HYDROKIT_OT_sim_clear, HYDROKIT_OT_sim_display, HYDROKIT_OT_sim_look,
           HYDROKIT_OT_sim_water, HYDROKIT_OT_rechengeraet_messen)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
