# HyprW — panel for HyprW's own simulation
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The sections in working order: Object Role, Bake, Look (acts at once, no
# new bake), Domain (acts with the next bake), Lens Drops.

import math

import bpy

from ..sim import anzeige, backen, cache, geometrie, zwischenstand


def _umbrechen(text, breite):
    """Spread long text over several panel lines."""
    worte, zeilen, aktuell = text.split(), [], ""
    for wort in worte:
        if len(aktuell) + len(wort) + 1 > breite:
            zeilen.append(aktuell)
            aktuell = wort
        else:
            aktuell = f"{aktuell} {wort}".strip()
    if aktuell:
        zeilen.append(aktuell)
    return zeilen


ROLLEN = (
    ('DOMAIN',   "Domain",    'MESH_CUBE'),
    ('FLUID',    "Water",     'MOD_FLUIDSIM'),
    ('OBSTACLE', "Obstacle",  'MESH_ICOSPHERE'),
    ('INFLOW',   "Inflow",    'IMPORT'),
    ('OUTFLOW',  "Outflow",   'EXPORT'),
    ('MIST_DOMAIN', "Mist Domain", 'OUTLINER_OB_VOLUME'),
    ('SPRAY_DOMAIN', "Spray Domain", 'PARTICLES'),
)

# Roles a bake leaves out while their Enabled switch is off (sim/backen.py)
ABSCHALTBAR = ('FLUID', 'OBSTACLE', 'INFLOW', 'OUTFLOW')


# Beaufort scale, upper limits in m/s (WMO)
BEAUFORT = ((0.3, "calm"), (1.6, "light air"), (3.4, "light breeze"), (5.5, "gentle breeze"),
            (8.0, "moderate breeze"), (10.8, "fresh breeze"), (13.9, "strong breeze"),
            (17.2, "near gale"), (20.8, "gale"), (24.5, "strong gale"), (28.5, "storm"),
            (32.7, "violent storm"), (float("inf"), "hurricane force"))


def _beaufort(v):
    for stufe, (grenze, name) in enumerate(BEAUFORT):
        if v < grenze:
            return stufe, name
    return 12, BEAUFORT[-1][1]


def _hilfe(layout, thema):
    """The small (i) next to a setting: the short text as a tooltip, the whole
    explanation on click (ui/hilfe.py)."""
    layout.operator("hydrokit.hilfe", text="", icon='INFO', emboss=False).thema = thema


def _regler(layout, daten, name, **kw):
    """A setting with its (i) in one row."""
    zeile = layout.row(align=True)
    zeile.prop(daten, name, **kw)
    _hilfe(zeile, name)


def _hinweis(layout):
    """A column of small hint lines."""
    spalte = layout.column(align=True)
    spalte.scale_y = 0.8
    return spalte


_ram = None


def _arbeitsspeicher():
    """Bytes of RAM in this computer, or 0 where the system does not say.
    The Resolution goes up to 8192 since 04.11.2026, while the cell limit of
    the engine lies far above what fits in memory here: 2.1 billion cells
    need at least 300 GB at the measured 150 bytes per voxel."""
    global _ram
    if _ram is None:
        import os
        try:
            _ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (AttributeError, ValueError, OSError):
            _ram = 0
    return _ram


def _zeilen(layout, text, icon='NONE', breite=36):
    """Text too long for the sidebar over several lines, continued lines
    indented as everywhere in this panel."""
    for n, zeile in enumerate(_umbrechen(text, breite)):
        if n == 0:
            layout.label(text=zeile, icon=icon)
        else:
            layout.label(text="   " + zeile)


def _aenderungen(scene, d, info):
    """What the next bake would do differently from the baked one, one short
    line each. Keys that older caches lack are skipped."""
    def an_aus(wert):
        return "on" if wert else "off"

    teile = []
    if "aufloesung" in info and info["aufloesung"] != d.aufloesung:
        teile.append(f"Resolution {info['aufloesung']} → {d.aufloesung}")
    netz = info.get("netz") or {}
    detail = max(1, d.oberflaechen_unterteilung)
    if "detail" in netz and netz["detail"] != detail:
        teile.append(f"Mesh Detail {netz['detail']} → {detail}")
    if "vorschau" in netz:
        # Only a second mesh the viewport wants and the cache lacks: without
        # it the viewport shows the render mesh, as it would after a new bake
        soll = backen.zweites_netz(d)
        if soll == "leicht" and netz["vorschau"] != "leicht":
            teile.append("Lighter Viewport: not in the cache")
        elif soll == "grob" and netz["vorschau"] != "grob":
            teile.append("Coarse Preview: not in the cache")
        elif (soll == "leicht" and "viewport" in netz
              and netz["viewport"] != d.unterteilung_viewport):
            teile.append(f"Viewport Detail {netz['viewport']} → {d.unterteilung_viewport}")
    if "detail" in netz:
        # caches from before the switch were baked with the spheres
        gebacken = netz.get("aniso")
        jetzt = (round(d.netz_glaettung, 3)
                 if d.netz_anisotrop and backen.engine_erweiterungen().get("aniso") else None)
        if (gebacken is None) != (jetzt is None):
            teile.append(f"Anisotropic: {an_aus(gebacken is not None)} → {an_aus(jetzt is not None)}")
        elif gebacken is not None and gebacken != jetzt:
            teile.append(f"Smoothness {gebacken:g} → {jetzt:g}")
    if "gischt" in info and bool(info["gischt"]) != d.gischt:
        teile.append(f"Whitewater: {an_aus(info['gischt'])} → {an_aus(d.gischt)}")
    elif "nebel" in info and bool(info["nebel"]) != (d.nebel_an and d.gischt):
        teile.append(f"Mist: {an_aus(info['nebel'])} → {an_aus(d.nebel_an and d.gischt)}")
    bereich = info.get("frames")
    if bereich and list(bereich) != [scene.frame_start, scene.frame_end]:
        teile.append(f"Frames {bereich[0]}–{bereich[1]} → "
                     f"{scene.frame_start}–{scene.frame_end}")
    return teile


class HYDROKIT_PT_sim(bpy.types.Panel):
    bl_label = "HyprW — Own Engine"
    bl_idname = "HYDROKIT_PT_sim"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "HyprW"

    def draw(self, context):
        from ..operators import sim_ops
        layout = self.layout
        scene = context.scene
        props = scene.hydrokit
        domain = geometrie.finde_domain(scene)

        if sim_ops.BAKE["laeuft"]:
            self._backt(layout, scene)
            return

        self._rollen(layout, context, domain)
        if domain is None:
            hinweis = _hinweis(layout)
            hinweis.separator()
            hinweis.label(text="No domain yet. Select an object", icon='INFO')
            hinweis.label(text="and click Domain above.")
            return
        # Read once per redraw: several sections show what the bake holds
        pfad = cache.pfad_aufloesen(domain.hydrokit.cache_pfad)
        frames = cache.gebackene_frames(pfad) if pfad else []
        info = (cache.info_lesen(pfad) if frames else None) or {}
        wind = geometrie.windvektor(scene, props.wind_faktor)
        # Bake right under the roles — the button people look for first. The
        # look next: it acts at once, the domain only with the next bake.
        self._backen(layout, context, domain, frames, info, wind)
        self._aussehen(layout, context, domain, info)
        self._domain(layout, context, domain, info, wind)
        self._kameratropfen(layout, context, domain, frames)

    # ------------------------------------------------------------ Baking
    def _backt(self, layout, scene):
        """While baking only this box: the frame being computed, the time
        left and Cancel. Blender stays usable meanwhile."""
        from ..operators import sim_ops
        b = sim_ops.BAKE
        frame, zeit = sim_ops.bake_stand()
        fertig = max(b["frame"] - b["start"], 0)
        gesamt = max(b["ende"] - b["start"] + 1, 1)
        anteil = min(fertig / gesamt, 1.0)
        box = layout.box()
        box.label(text=frame, icon='TIME')
        if b["szene"] != scene.name:
            _hinweis(box).label(text=f"   in the scene '{b['szene']}'")
        # A bar, not a property: a slider could be grabbed and dragged
        box.progress(factor=anteil, type='BAR',
                     text=f"{fertig} of {gesamt} done · {anteil * 100:.0f} %")
        if zeit:
            _hinweis(box).label(text=zeit)
        zeile = box.row()
        zeile.scale_y = 1.3
        if b["abbrechen"]:
            # the engine cannot stop in the middle of a frame
            zeile.enabled = False
            zeile.operator("hydrokit.sim_stop", text="Cancelling when this frame is done",
                           icon='CANCEL')
        else:
            zeile.operator("hydrokit.sim_stop", text="Cancel (Esc)", icon='CANCEL')
        if fertig:
            _hinweis(box).label(
                text=f"Keeps the {fertig} frame{'s' if fertig != 1 else ''} baked so far")

    # ------------------------------------------------------------ Roles
    def _rollen(self, layout, context, domain):
        obj = context.active_object
        aktuell = obj.hydrokit.typ if obj is not None else 'NONE'
        namen = dict((r[0], r[1]) for r in ROLLEN)
        header, body = layout.panel("hk_sim_rollen", default_closed=False)
        # The selected object and its role: readable while collapsed
        if obj is None:
            header.label(text="Object Role — nothing selected", icon='OBJECT_DATA')
        else:
            header.label(text=f"{obj.name} — {namen.get(aktuell, 'no role')}",
                         icon=dict((r[0], r[2]) for r in ROLLEN).get(aktuell, 'OBJECT_DATA'))
        if body is None:
            return
        if obj is None:
            body.label(text="Select an object to give it a role", icon='INFO')
        elif aktuell == 'NONE':
            self._rollen_gitter(body, obj, domain, aktuell)
        else:
            # Roles are set once, settings changed often: settings first
            self._rollen_einstellungen(body, context, obj, aktuell)
            kopf, inhalt = body.panel("hk_sim_rolle_wechseln", default_closed=True)
            kopf.label(text="Change Role")
            if inhalt is not None:
                self._rollen_gitter(inhalt, obj, domain, aktuell)
        self._rollen_uebersicht(body, context.scene)

    def _rollen_gitter(self, layout, obj, domain, aktuell):
        zeile = layout.row()
        zeile.label(text="Role")
        _hilfe(zeile, "rolle")
        gitter = layout.grid_flow(columns=2, even_columns=True, align=True)
        for key, beschriftung, icon in ROLLEN:
            zelle = gitter.row(align=True)
            # One domain per scene; the tooltip says which object has it
            zelle.enabled = not (key == 'DOMAIN' and domain is not None and domain is not obj)
            op = zelle.operator("hydrokit.set_type", text=beschriftung, icon=icon,
                                depress=aktuell == key)
            op.typ = key
        if aktuell != 'NONE':
            op = layout.operator("hydrokit.set_type", text="Clear Role", icon='X')
            op.typ = 'NONE'

    def _rollen_uebersicht(self, layout, scene):
        """The scene in two lines: the domain and what the next bake takes."""
        objekte = geometrie.sammle_objekte(scene)
        teile = []
        for key, beschriftung, _ in ROLLEN[1:]:
            liste = objekte.get(key, [])
            if not liste:
                continue
            aus = sum(1 for o in liste if key in ABSCHALTBAR and not o.hydrokit.rolle_aktiv)
            teile.append(f"{beschriftung} {len(liste)}" + (f" ({aus} off)" if aus else ""))
        hinweis = _hinweis(layout)
        hinweis.separator()
        domain = objekte['DOMAIN'][0] if objekte['DOMAIN'] else None
        hinweis.label(text=f"Domain: {domain.name if domain else 'none'}")
        for zeile in _umbrechen(" · ".join(teile), 40):
            hinweis.label(text=zeile)

    def _rollen_einstellungen(self, body, context, obj, aktuell):
        """Settings of the active object's role (FLIP Fluids' per-object
        settings with its defaults, where the engine has them)."""
        if aktuell == 'NONE':
            return
        h = obj.hydrokit
        kasten = body.box()
        kasten.label(text=dict((r[0], r[1]) for r in ROLLEN)[aktuell] + " Settings",
                     icon=dict((r[0], r[2]) for r in ROLLEN)[aktuell])
        if aktuell in ('DOMAIN', 'MIST_DOMAIN', 'SPRAY_DOMAIN'):
            hinweis = _hinweis(kasten)
            if aktuell == 'DOMAIN':
                hinweis.label(text="Settings: Domain section below", icon='INFO')
            else:
                art = "Mist" if aktuell == 'MIST_DOMAIN' else "Spray"
                hinweis.label(text=f"{art} keeps flying inside this box", icon='INFO')
                hinweis.label(text="   after leaving the domain; no other settings")
                g = obj.dimensions
                hinweis.label(text=f"   Size {g.x:.1f} x {g.y:.1f} x {g.z:.1f} m")
            return

        _regler(kasten, h, "rolle_aktiv")
        spalte = kasten.column(align=True)
        spalte.enabled = h.rolle_aktiv
        if aktuell == 'INFLOW':
            _regler(spalte, h, "zufluss_geschwindigkeit")
            wirbel = spalte.row(align=True)
            wirbel.enabled = (backen.engine_erweiterungen().get("turbulenz", False)
                              and h.zufluss_erzwingen)
            wirbel.prop(h, "zufluss_turbulenz")
            groesse = wirbel.row(align=True)
            groesse.enabled = h.zufluss_turbulenz > 0.0
            groesse.prop(h, "zufluss_wirbel", text="Eddies")
            _hilfe(wirbel, "zufluss_turbulenz")
            _regler(spalte, h, "zufluss_erzwingen")
            _regler(spalte, h, "zufluss_substeps")
            if h.zufluss_erzwingen and not any(h.zufluss_geschwindigkeit):
                hinweis = _hinweis(kasten)
                hinweis.label(text="Velocity 0: the water stays", icon='INFO')
                hinweis.label(text="   in the inflow and only seeps out")
        elif aktuell == 'FLUID':
            _regler(spalte, h, "start_geschwindigkeit")
        elif aktuell == 'OBSTACLE':
            _regler(spalte, h, "hindernis_umkehren")
            _regler(spalte, h, "hindernis_reibung", slider=True)
            _regler(spalte, h, "hindernis_ausdehnung")
            spalte.separator()
            d = geometrie.finde_domain(context.scene)
            dh = d.hydrokit if d is not None else None
            gischt = spalte.column(align=True)
            gischt.enabled = bool(dh and dh.gischt)
            _regler(gischt, h, "hindernis_gischt")
            nebel = spalte.column(align=True)
            nebel.enabled = bool(dh and dh.gischt and dh.nebel_an)
            _regler(nebel, h, "hindernis_nebel")
            sheet = spalte.column(align=True)
            sheet.enabled = bool(dh and dh.sheeting)
            _regler(sheet, h, "hindernis_sheeting")
            # Why a field is greyed out: it needs a switch on the domain
            gruende = []
            if not (dh and dh.gischt):
                gruende.append("Whitewater Influence and Mist Emission need "
                               "Whitewater on the domain")
            elif not dh.nebel_an:
                gruende.append("Mist Emission needs Mist on the domain")
            if not (dh and dh.sheeting):
                gruende.append("Sheeting Strength needs Sheeting on the domain")
            if gruende:
                hinweis = _hinweis(kasten)
                for grund in gruende:
                    _zeilen(hinweis, grund, breite=40)
        elif aktuell == 'OUTFLOW':
            _regler(spalte, h, "abfluss_wasser")
            _regler(spalte, h, "abfluss_gischt")
            _regler(spalte, h, "abfluss_umkehren")
            _regler(spalte, h, "abfluss_allmaehlich")
            rate = spalte.column(align=True)
            rate.enabled = h.abfluss_allmaehlich
            _regler(rate, h, "abfluss_rate")

    # ------------------------------------------------------------ Bake
    def _backen(self, layout, context, domain, frames, info, wind):
        header, body = layout.panel("hk_sim_backen", default_closed=False)
        header.label(text="Bake", icon='PHYSICS')
        if body is None:
            return
        scene = context.scene
        d = domain.hydrokit

        fehlt = backen.fehlende_erweiterungen()
        if fehlt:
            warn = body.box().column(align=True)
            warn.alert = True
            if backen.neustart_noetig():
                warn.label(text="Engine was updated:", icon='ERROR')
                warn.label(text="   restart Blender")
                for name in fehlt:
                    warn.label(text=f"   until then {name} has no effect")
            else:
                warn.label(text="Engine without the HyprW extension:", icon='ERROR')
                for name in fehlt:
                    warn.label(text=f"   {name} has no effect")
                warn.label(text="See engine_patch/README.md")

        # Where to and which frames: the cache folder next to the button that
        # empties it, the range that the timeline and F12 use as well
        zeile = body.row(align=True)
        zeile.prop(d, "cache_pfad", text="Cache")
        loeschen = zeile.row(align=True)
        loeschen.enabled = bool(frames)
        loeschen.operator("hydrokit.sim_clear", text="", icon='TRASH')
        _hilfe(zeile, "cache_pfad")
        if not cache.pfad_aufloesen(d.cache_pfad):
            warn = _hinweis(body)
            warn.alert = True
            warn.label(text=backen.cache_fehlt_text(domain), icon='ERROR')
        zeile = body.row(align=True)
        zeile.prop(d, "zwischenstand_an", text="Saved States")
        alle = zeile.row(align=True)
        alle.enabled = d.zwischenstand_an
        alle.prop(d, "zwischenstand_alle", text="Every")
        _hilfe(zeile, "zwischenstand_an")
        zeile = body.row(align=True)
        zeile.prop(scene, "frame_start", text="Start")
        zeile.prop(scene, "frame_end", text="End")
        _hilfe(zeile, "frame_start")

        self._bake_stand(body, scene, d, frames, info)
        body.separator()
        self._rechengeraet(body, domain)
        self._vor_dem_backen(body, scene, d, wind)

        body.separator()
        zeile = body.row()
        zeile.scale_y = 1.4
        zeile.operator("hydrokit.sim_bake", text="Bake Again" if frames else "Bake", icon='PLAY')
        self._weiterrechnen(body, scene, domain)
        if frames:
            self._anzeige_stand(body, scene)

    def _weiterrechnen(self, body, scene, domain):
        """Resume, if the cache holds a saved state."""
        stand = zwischenstand.lesen(cache.pfad_aufloesen(domain.hydrokit.cache_pfad))
        if stand is None:
            return
        masse = geometrie.gitter_masse(domain, domain.hydrokit.aufloesung)
        versatz = geometrie.domain_bereich(domain)[0]
        try:
            grund = zwischenstand.warum_nicht(stand, masse, versatz,
                                              scene.frame_start, scene.frame_end)
            # Every finished bake leaves its state at End: then raising End
            # is all it takes, a hint and not an error
            nur_ende = grund is not None and zwischenstand.warum_nicht(
                stand, masse, versatz, scene.frame_start, stand["frame"] + 1) is None
            text = f"Resume from Frame {stand['frame'] + 1}"
        except (KeyError, TypeError, ValueError):
            grund, nur_ende, text = "Saved state unreadable: bake again", False, "Resume"
        if not backen.engine_erweiterungen().get("zwischenstand"):
            grund, nur_ende = "This engine cannot resume", False
        zeile = body.row(align=True)
        zeile.scale_y = 1.4
        knopf = zeile.row(align=True)
        knopf.enabled = grund is None
        knopf.operator("hydrokit.sim_bake", text=text, icon='FILE_REFRESH').weiter = True
        _hilfe(zeile, "zwischenstand_weiter")
        spalte = _hinweis(body)
        spalte.label(text=f"Saved state: {zwischenstand.groesse(stand) / 1024**3:.1f} GB")
        if grund:
            spalte.alert = not nur_ende
            spalte.label(text=grund, icon='INFO' if nur_ende else 'ERROR')

    def _bake_stand(self, body, scene, d, frames, info):
        """What the cache holds, and what the next bake would change."""
        from ..operators import sim_ops
        hinweis = _hinweis(body)
        if not frames:
            hinweis.label(text="Nothing baked yet")
            return
        bereich = info.get("frames")
        soll = bereich[1] - bereich[0] + 1 if bereich else len(frames)
        if len(frames) < soll:
            # Bake Again starts over; with a saved state Resume below goes on
            hinweis.label(text=f"Stopped after {len(frames)} of {soll} frames", icon='INFO')
            hinweis.label(text=f"   Bake Again restarts at frame {scene.frame_start}")
        else:
            bereich_text = f" ({frames[0]}–{frames[-1]})" if len(frames) > 1 else f" (frame {frames[0]})"
            hinweis.label(text=f"{sim_ops.anzahl(len(frames), 'frame')} baked{bereich_text}",
                          icon='CHECKMARK')
        teile = []
        if "aufloesung" in info:
            teile.append(f"Resolution {info['aufloesung']}")
        if (info.get("netz") or {}).get("detail") is not None:
            teile.append(f"Mesh Detail {info['netz']['detail']}")
        if info.get("dauer"):
            teile.append(sim_ops.dauer_text(info["dauer"]))
        if teile:
            hinweis.label(text="   " + " · ".join(teile))

        aenderungen = _aenderungen(scene, d, info)
        if aenderungen:
            warn = body.column(align=True)
            warn.alert = True
            warn.label(text="Changed since this bake:", icon='ERROR')
            for text in aenderungen[:3]:
                warn.label(text="   " + text)
            if len(aenderungen) > 3:
                warn.label(text=f"   +{len(aenderungen) - 3} more")

    def _rechengeraet(self, body, domain):
        """CPU / GPU / Both in one row, the GPU below — and with Both a
        choice per step."""
        d = domain.hydrokit
        zeile = body.row(align=True)
        zeile.label(text="Device")
        zeile.prop(d, "rechengeraet", expand=True)
        _hilfe(zeile, "rechengeraet")
        verfuegbar, name, grund = backen.gpu_info()
        hinweis = _hinweis(body)
        stufen = [key for key, *_ in backen.STUFEN if backen.hat_gpu(key)]
        if verfuegbar:
            hinweis.label(text=name, icon='CHECKMARK')
            n = sum(1 for key in stufen if backen.geraet(d, key) == 'GPU')
            if d.rechengeraet == 'GPU':
                from ..operators import sim_ops
                hinweis.label(text=f"   {sim_ops.anzahl(n, 'step')} on the GPU")
            elif d.rechengeraet == 'BEIDES':
                hinweis.label(text=f"   {n} of {len(stufen)} steps on the GPU")
        else:
            hinweis.alert = d.rechengeraet != 'CPU'
            hinweis.label(text=f"No GPU: {grund}", icon='INFO')
            if d.rechengeraet != 'CPU':
                hinweis.label(text="   everything runs on the CPU")
        if d.rechengeraet != 'BEIDES':
            return
        kopf, inhalt = body.panel("hk_sim_geraet", default_closed=False)
        kopf.label(text="Per Step")
        if inhalt is None:
            return
        from ..operators import sim_ops
        if sim_ops.MESSUNG["laeuft"]:
            inhalt.label(text=f"Measuring … frame {sim_ops.MESSUNG['frame']} of "
                              f"{sim_ops.PROBE_FRAMES}  (Esc cancels)", icon='TIME')
        else:
            zeile = inhalt.row()
            zeile.enabled = verfuegbar
            zeile.operator("hydrokit.rechengeraet_messen", icon='TIME')
        gemessen = {}
        if d.rechengeraet_messung:
            import json
            try:
                gemessen = json.loads(d.rechengeraet_messung).get("steps", {})
            except ValueError:
                gemessen = {}
        spalte = inhalt.column(align=True)
        for key, beschriftung, *_ in backen.STUFEN:
            if backen.hat_gpu(key):
                zeile = spalte.row(align=True)
                _regler(zeile, d, "geraet_" + key, text=beschriftung)
                werte = gemessen.get(key)
                if werte and werte["gpu_seconds"] > 0 and werte["cpu_seconds"] > 0:
                    faktor = werte["cpu_seconds"] / werte["gpu_seconds"]
                    zeile.label(text=f"GPU {faktor:.1f}x" if faktor >= 1.0
                                else f"CPU {1.0 / faktor:.1f}x")
                _hilfe(zeile, "geraet_" + key)
            else:
                zeile = spalte.row()
                zeile.enabled = False
                zeile.label(text=f"{beschriftung}: CPU (no GPU version yet)")

    def _vor_dem_backen(self, body, scene, d, wind):
        """Problems that spoil a bake, right above the button."""
        objekte = geometrie.sammle_objekte(scene)
        if not any(o.hydrokit.rolle_aktiv for rolle in ('FLUID', 'INFLOW')
                   for o in objekte[rolle]):
            warn = body.column(align=True)
            warn.alert = True
            _zeilen(warn, "No enabled Water or Inflow — the bake stays empty", icon='ERROR')
        # A typo in a wind strength turns a breeze into a hurricane that
        # shreds every drop (seen 29.10.2026: 40 m/s)
        stufe, name = _beaufort(wind.length)
        if stufe >= 8:
            warn = body.column(align=True)
            warn.alert = True
            warn.label(text=f"Wind {wind.length:.1f} m/s — Beaufort {stufe}", icon='ERROR')
            warn.label(text=f"   {name}")
        if d.sheeting:
            body.label(text="Sheeting is on: it adds water", icon='INFO')

    def _anzeige_stand(self, body, scene):
        """Whether the viewport water follows the timeline: not while lens
        drops compute, nor in a file opened before the add-on was enabled.
        Renders take the right frame either way."""
        from ..operators import sim_ops
        body.separator()
        if sim_ops.LINSE_LAEUFT:
            hinweis = _hinweis(body)
            hinweis.label(text=f"Viewport paused: lens drops "
                               f"{scene.hydrokit.linse_fortschritt * 100:.0f} %", icon='TIME')
            hinweis.label(text="   (Esc cancels)")
        elif anzeige.handler_laeuft():
            zeile = body.row(align=True)
            zeile.label(text="Viewport follows the timeline", icon='CHECKMARK')
            zeile.operator("hydrokit.sim_display", text="", icon='FILE_REFRESH')
        else:
            warn = body.column(align=True)
            warn.alert = True
            _zeilen(warn, "Viewport water does not follow the timeline", icon='ERROR')
            zeile = body.row()
            zeile.scale_y = 1.2
            zeile.operator("hydrokit.sim_display", text="Follow Timeline", icon='PLAY')

    # ------------------------------------------------------------ Look
    def _aussehen(self, layout, context, domain, info):
        """Everything that changes the look without a new bake: the water
        body, the whitewater and the shutter."""
        from ..sim import aussehen
        header, body = layout.panel("hk_sim_aussehen", default_closed=False)
        header.label(text="Look", icon='SHADING_RENDERED')
        if body is None:
            return
        _hinweis(body).label(text="Changes show at once — no new bake")
        stand, text = aussehen.look_stand(context, info)
        zustand = _hinweis(body)
        if stand in ('fehlt', 'veraltet'):
            zustand.alert = True
            _zeilen(zustand, text, icon='ERROR')
        else:
            zustand.label(text=text, icon='CHECKMARK' if stand == 'ok' else 'INFO')
        zeile = body.row()
        zeile.scale_y = 1.3
        zeile.enabled = stand is not None
        zeile.alert = stand == 'fehlt'
        zeile.operator("hydrokit.sim_look", text="Re-apply Look" if stand == 'ok' else "Apply Look",
                       icon='MATERIAL')

        self._wasser(body, context, domain, info)
        self._gischt_aussehen(body, context, domain, info)
        # A real shutter turns fast drops into streaks; without it every drop
        # is a sharp ball and whitewater looks like hail
        rd = context.scene.render
        kopf, inhalt = body.panel("hk_sim_bewegung", default_closed=False)
        kopf.prop(rd, "use_motion_blur", text="")
        kopf.label(text="Motion Blur (render)")
        _hilfe(kopf, "use_motion_blur")
        if inhalt is not None:
            zeile = inhalt.row(align=True)
            zeile.active = rd.use_motion_blur
            zeile.prop(rd, "motion_blur_shutter", text="Shutter (frames)")
            _hilfe(zeile, "motion_blur_shutter")

    def _wasser(self, layout, context, domain, info):
        """The water body, from measured optics (operators/materials_ops.py):
        pick a water, click the water in the viewport and press Apply Water,
        like a material on the selected object. Foam, bubbles, spray and mist
        keep their look."""
        from ..operators import materials_ops, sim_ops
        sh = context.scene.hydrokit
        flaeche = bpy.data.objects.get(anzeige.OBERFLAECHE)
        name, offen = materials_ops.wasser_stand(context.scene, flaeche)
        header, body = layout.panel("hk_sim_wasser", default_closed=False)
        header.label(text="Water", icon='MATERIAL')
        if name:
            header.label(text=name)
        if body is None:
            return
        _regler(body, sh, "wasser_variante", text="")
        zeile = body.row()
        zeile.scale_y = 1.4
        zeile.alert = bool(offen)
        zeile.operator("hydrokit.sim_water", icon='CHECKMARK')
        hinweis = _hinweis(body)
        # After Clear Cache the water object stays, empty
        gebacken = flaeche is not None and bool(info)
        if gebacken and sim_ops.gewaehltes_wasser(context) is None:
            hinweis.label(text="Click the water in the viewport first",
                          icon='RESTRICT_SELECT_OFF')
        if not gebacken:
            hinweis.label(text="No water yet: bake first", icon='INFO')
        elif offen:
            hinweis.alert = True
            namen = ", ".join(offen[:3]) + (", …" if len(offen) > 3 else "")
            _zeilen(hinweis, f"Not applied yet: {namen}", icon='ERROR')
        elif name:
            hinweis.label(text=f"Applied: {name}", icon='CHECKMARK')
        else:
            hinweis.label(text="No water material yet", icon='INFO')
        if gebacken:
            # Measured optics: the colour builds up over about a metre of
            # water, so a thin falling stream looks alike for most types
            _zeilen(_hinweis(body), "The type shows in deep water; thin falling water "
                    "stays clear", icon='INFO')

        kopf, inhalt = body.panel("hk_sim_wasser_fein", default_closed=True)
        kopf.label(text="Fine-tune")
        if inhalt is None:
            return
        _hinweis(inhalt).label(text="Press Apply Water after changes")
        wasser = inhalt.column(align=True)
        _regler(wasser, sh, "wasser_sichtweite")
        _regler(wasser, sh, "wasser_geloest")
        _regler(wasser, sh, "wasser_kraeuselung")
        _regler(wasser, sh, "wasser_schatten", slider=True)
        unter = wasser.column(align=True)
        unter.enabled = sh.wasser_schatten > 0.0
        _regler(unter, sh, "wasser_kaustik", slider=True)
        _regler(unter, sh, "wasser_kaustik_groesse")
        _regler(wasser, sh, "wasser_wellen")
        _regler(wasser, sh, "wasser_dispersion")

    def _gischt_aussehen(self, layout, context, domain, info):
        """How much of the baked whitewater shows, and how it looks. Usable
        while the bake on display has whitewater or the next one will: the
        switch under Domain only acts on the next bake."""
        d = domain.hydrokit
        hat = d.gischt or bool(info.get("gischt"))
        kopf, inhalt = layout.panel("hk_sim_gischt_aussehen", default_closed=False)
        kopf.label(text="Whitewater", icon='PARTICLES')
        if inhalt is None:
            return
        if not hat:
            _hinweis(inhalt).label(text="Whitewater is off (Domain › Whitewater)", icon='INFO')
        spalte = inhalt.column(align=True)
        spalte.enabled = hat
        spalte.label(text="Amount shown")
        for k, t in (("menge_foam", "Foam"), ("menge_bubble", "Bubbles"),
                     ("menge_spray", "Spray"), ("menge_nebel", "Mist")):
            _regler(spalte, d, k, text=t, slider=True)
        spalte.separator()
        _regler(spalte, d, "nebel_dichte_anzeige", text="Mist Density")
        _regler(spalte, d, "schaum_blasen")
        _regler(spalte, d, "schaum_luftanteil", slider=True)
        _regler(spalte, d, "spray_farbe")
        _regler(spalte, context.scene.hydrokit, "whitewater_scale_multiplier")

    # ------------------------------------------------------------ Domain
    def _domain(self, layout, context, domain, info, wind):
        d = domain.hydrokit
        masse = geometrie.gitter_masse(domain, d.aufloesung)
        header, body = layout.panel("hk_sim_domain", default_closed=False)
        header.label(text="Domain", icon='MESH_CUBE')
        if masse:
            header.label(text=f"{masse[0]} x {masse[1]} x {masse[2]} · {masse[3]*100:.1f} cm")
        if body is None:
            return
        _hinweis(body).label(text="Takes effect with the next bake")
        _regler(body, d, "aufloesung")
        if masse:
            voxel = masse[0] * masse[1] * masse[2]
            info_spalte = _hinweis(body)
            info_spalte.label(text=f"{voxel:,} voxels · {masse[3]*100:.1f} cm each")
            # measured 04.11.2026 at 300 and 600: 147-157 bytes per voxel with
            # little water; up to about 1 KB per voxel full of water (estimate)
            wenig, voll = voxel * 150 / 1024**3, voxel * 1024 / 1024**3
            info_spalte.label(text=f"{wenig:.1f} to {voll:.1f} GB, by amount of water"
                              + ("  + whitewater" if d.gischt else ""))
            if geometrie.gitter_zu_gross(masse):
                warn = body.column(align=True)
                warn.alert = True
                warn.label(text="Too many cells for the engine (max about 2.1 billion)", icon='ERROR')
            elif _arbeitsspeicher() and voxel * 150 > _arbeitsspeicher():
                # even the low estimate does not fit: the bake would swap
                # and then run out of memory
                warn = body.column(align=True)
                warn.alert = True
                warn.label(text=f"More than this computer's {_arbeitsspeicher() / 1024**3:.0f} GB "
                                "of memory", icon='ERROR')
        self._adaptiv(body, d, masse)
        self._fluessigkeit(body, context.scene, d, masse)
        _regler(body, d, "cfl", text="CFL (cells per step)")
        _regler(body, d, "sheeting")
        if d.sheeting:
            warn = body.column(align=True)
            warn.alert = True
            warn.label(text="Adds water — tall falls fill up", icon='ERROR')
        zeile = body.row()
        zeile.label(text="Open Borders")
        _hilfe(zeile, "offene_raender")
        zeile = body.row(align=True)
        for n, seite in enumerate(("-X", "+X", "-Y", "+Y", "-Z", "+Z")):   # as in sim/backen.py
            zeile.prop(d, "offene_raender", index=n, text=seite, toggle=True)

        self._netz(body, d, info)
        self._gischt(body, context, d, masse)
        self._wind(body, context.scene.hydrokit, d, wind)
        self._viewport_anzeige(body, context.scene.hydrokit)

    def _netz(self, body, d, info):
        """Water mesh detail (sim/backen.py); a lighter viewport mesh only
        when switched on."""
        kopf, inhalt = body.panel("hk_sim_netz", default_closed=False)
        kopf.label(text="Water Mesh", icon='MESH_DATA')
        if inhalt is None:
            return
        netz = inhalt.column(align=True)
        _regler(netz, d, "oberflaechen_unterteilung",
                text="Render Detail" if d.viewport_eigenes_netz else "Mesh Detail")
        kann_aniso = backen.engine_erweiterungen().get("aniso", False)
        aniso = netz.row(align=True)
        aniso.enabled = kann_aniso
        aniso.prop(d, "netz_anisotrop", text="Anisotropic")
        glatt = aniso.row(align=True)
        glatt.enabled = d.netz_anisotrop
        glatt.prop(d, "netz_glaettung", text="Smoothness")
        _hilfe(aniso, "netz_anisotrop")
        leicht = netz.row(align=True)
        leicht.enabled = not d.vorschau_im_viewport
        leicht.prop(d, "viewport_eigenes_netz")
        feld = leicht.row(align=True)
        feld.enabled = d.viewport_eigenes_netz
        feld.prop(d, "unterteilung_viewport", text="Detail")
        _hilfe(leicht, "viewport_eigenes_netz")
        grob = netz.row(align=True)
        grob.prop(d, "vorschau_im_viewport")
        aufloesung = grob.row(align=True)
        aufloesung.enabled = d.vorschau_im_viewport
        aufloesung.prop(d, "vorschau_aufloesung", text="Cells")
        _hilfe(grob, "vorschau_im_viewport")
        hinweis = _hinweis(inhalt)
        if (d.viewport_eigenes_netz and not d.vorschau_im_viewport
                and d.unterteilung_viewport >= d.oberflaechen_unterteilung):
            hinweis.label(text="Viewport shows the render mesh", icon='INFO')
        if not kann_aniso:
            hinweis.label(text="Anisotropic: restart Blender to load the new engine"
                          if backen.neustart_noetig() else "Anisotropic: engine too old",
                          icon='INFO')
        elif d.netz_anisotrop and d.oberflaechen_unterteilung < 2:
            hinweis.label(text="Anisotropic looks soft at Mesh Detail 1", icon='INFO')
        gebacken = (info.get("netz") or {}).get("detail")
        if gebacken is not None and gebacken != max(1, d.oberflaechen_unterteilung):
            hinweis.label(text=f"Baked with Mesh Detail {gebacken}")

    def _gischt(self, body, context, d, masse):
        """Whitewater with its switch in the header; greyed out while off, so
        it shows what it offers before it is switched on."""
        kopf, inhalt = body.panel("hk_sim_gischt", default_closed=False)
        kopf.prop(d, "gischt", text="")
        kopf.label(text="Whitewater", icon='PARTICLES')
        _hilfe(kopf, "gischt")
        if inhalt is None:
            return
        spalte = inhalt.column(align=True)
        spalte.enabled = d.gischt
        _regler(spalte, d, "schaum_mitfuehrung", slider=True)
        _regler(spalte, d, "blasen_mitfuehrung", slider=True)

        kopf, nebel = spalte.panel("hk_sim_nebel", default_closed=False)
        kopf.prop(d, "nebel_an", text="")
        kopf.label(text="Mist", icon='OUTLINER_OB_VOLUME')
        _hilfe(kopf, "nebel_an")
        if nebel is not None:
            unter = nebel.column(align=True)
            unter.enabled = d.nebel_an
            _regler(unter, d, "nebel_spritz_anteil")
            _regler(unter, d, "nebel_spritz_schwelle")
            _regler(unter, d, "nebel_staerke", text="Mist Strength")
            _regler(unter, d, "nebel_rate")
            nd = geometrie.finde_nebel_domain(context.scene)
            hinweis = _hinweis(unter)
            if nd is not None and masse:
                hinweis.label(text=f"Mist domain: {nd.name}", icon='OUTLINER_OB_VOLUME')
                hinweis.label(text=f"   voxels {masse[3]*100:.1f} cm like the domain")
            else:
                _zeilen(hinweis, "Without a mist domain, mist ends at the border", icon='INFO')

        # Tuned on a waterfall (object_properties.py): closed by default
        kopf, emission = spalte.panel("hk_sim_gischt_emission", default_closed=True)
        kopf.label(text="Emission")
        if emission is not None:
            paare = emission.column(align=True)
            for von, bis in (("gischt_tempo_min", "gischt_tempo_max"),
                             ("gischt_kruemmung_min", "gischt_kruemmung_max"),
                             ("gischt_turbulenz_min", "gischt_turbulenz_max")):
                zeile = paare.row(align=True)
                zeile.prop(d, von)
                zeile.prop(d, bis)
                _hilfe(zeile, von)
            raten = emission.column(align=True)
            _regler(raten, d, "gischt_rate_wellen")
            _regler(raten, d, "gischt_rate_turbulenz")
            _regler(raten, d, "gischt_hoechstzahl")
            _regler(raten, d, "gischt_lebensdauer")
            _regler(emission, d, "gischt_an_waenden")

        kopf, groessen = spalte.panel("hk_sim_gischt_groessen", default_closed=True)
        kopf.label(text="Droplet Sizes")
        if groessen is not None:
            liste = groessen.column(align=True)
            _regler(liste, d, "nebel_groesse")
            _regler(liste, d, "spray_groesse")

    def _fluessigkeit(self, body, scene, d, masse=None):
        """Liquid: a measured liquid, its viscosity and surface tension, and
        what they mean for this domain."""
        from ..properties.object_properties import FLUESSIGKEITEN, fluessigkeit_eigen
        name = FLUESSIGKEITEN[d.fluessigkeit][0] if d.fluessigkeit in FLUESSIGKEITEN else "Liquid"
        if fluessigkeit_eigen(d):
            name += " · custom"
        an = [t for t, ja in (("viscous", d.zaehigkeit_an), ("surface tension",
                                                            d.oberflaechenspannung_an)) if ja]
        kopf, inhalt = body.panel("hk_sim_fluessigkeit", default_closed=False)
        kopf.label(text=f"Liquid: {name}" + (f" ({', '.join(an)})" if an else ""),
                   icon='MOD_FLUIDSIM')
        _hilfe(kopf, "fluessigkeit")
        if inhalt is None:
            return
        _regler(inhalt, d, "fluessigkeit", text="")
        spalte = inhalt.column(align=True)
        _regler(spalte, d, "zaehigkeit_an")
        zeile = spalte.column(align=True)
        zeile.enabled = d.zaehigkeit_an
        _regler(zeile, d, "zaehigkeit", text="Pa·s")
        _regler(spalte, d, "oberflaechenspannung_an")
        zeile = spalte.column(align=True)
        zeile.enabled = d.oberflaechenspannung_an
        _regler(zeile, d, "oberflaechenspannung", text="N/m")
        _regler(inhalt, d, "dichte", text="Density (kg/m³)")
        hinweis = _hinweis(inhalt)
        if d.zaehigkeit_an and d.zaehigkeit > 0.0:
            nu = d.zaehigkeit / d.dichte
            hinweis.label(text=f"{nu:.3g} m²/s, {nu / 1.0e-6:,.0f}x as viscous as water")
        if d.oberflaechenspannung_an and d.oberflaechenspannung > 0.0 and masse:
            frame = scene.render.fps_base / scene.render.fps
            schritte = backen.oberflaechen_schritte(masse[3], d.oberflaechenspannung / d.dichte, frame)
            if schritte > 1:
                hinweis.label(text=f"About {schritte} steps per frame for it")
            else:
                hinweis.label(text="Too coarse to show here: cells "
                                   f"{masse[3] * 100:.0f} cm", icon='INFO')

    def _adaptiv(self, body, d, masse=None):
        """Hybrid Water: one lever, and under it what its position means for
        this domain, in metres. The five engine values it sets stay usable
        under Advanced, closed by default."""
        from ..properties.object_properties import hybrid_eigen, hybrid_kennzahlen
        kopf, inhalt = body.panel("hk_sim_adaptiv", default_closed=False)
        kopf.prop(d, "adaptiv_an", text="")
        kopf.label(text="Hybrid Water", icon='PARTICLE_DATA')
        _hilfe(kopf, "adaptiv_an")
        if inhalt is None:
            return
        spalte = inhalt.column(align=True)
        spalte.enabled = d.adaptiv_an
        _regler(spalte, d, "adaptiv_hebel", text="Strength", slider=True)
        dx = masse[3] if masse else None
        k = hybrid_kennzahlen(d, dx)

        def laenge(zellen):
            if not dx:
                return f"{zellen:.1f} cells"
            m = zellen * dx
            return f"{m:.1f} m" if m >= 1.0 else f"{m * 100:.0f} cm"

        hinweis = _hinweis(spalte)
        hinweis.label(text=f"Deep water: {k['aufloesung']:.0f} % of the resolution")
        teil = f"Largest particle: {k['faktor']}x volume"
        if dx:
            v = k["volumen"]
            teil += (f", {v:.3g} m³" if v >= 1.0 else f", {v * 1e3:.3g} L" if v >= 1e-3
                     else f", {v * 1e6:.3g} cm³")
        hinweis.label(text=teil)
        hinweis.label(text=f"Stays fine {laenge(k['fein_tiefe'])} below the surface")
        hinweis.label(text=f"   and {laenge(k['fein_abstand'])} from obstacles")
        if d.adaptiv_max_stufe > 1:
            hinweis.label(text=f"Largest from {laenge(k['grob_tiefe'])} deep")

        kopf, technik = spalte.panel("hk_sim_adaptiv_technik", default_closed=True)
        kopf.label(text="Advanced · custom" if hybrid_eigen(d) else "Advanced")
        if technik is None:
            return
        liste = technik.column(align=True)
        _regler(liste, d, "adaptiv_max_stufe")
        _regler(liste, d, "adaptiv_teilungsdauer")
        _regler(liste, d, "adaptiv_teil_faktor")
        _regler(liste, d, "adaptiv_verschmelz_faktor")
        _regler(liste, d, "adaptiv_hindernis_abstand")

    def _wind(self, body, sh, d, wind):
        """Wind from Blender's wind objects, its strength in the header."""
        stufe, name = _beaufort(wind.length)
        weht = wind.length > 0.05
        sturm = weht and stufe >= 8
        kopf, inhalt = body.panel("hk_sim_wind", default_closed=True)
        kopf.alert = sturm
        kopf.label(text="Wind", icon='ERROR' if sturm else 'FORCE_WIND')
        kopf.label(text=f"{wind.length:.1f} m/s · Bft {stufe}" if weht else "none")
        if inhalt is None:
            return
        hinweis = _hinweis(inhalt)
        if weht:
            hinweis.alert = sturm
            hinweis.label(text=f"Wind {wind.length:.1f} m/s — Beaufort {stufe}",
                          icon='ERROR' if sturm else 'FORCE_WIND')
            hinweis.label(text=f"   {name}")
        else:
            hinweis.label(text="No wind object in the scene", icon='INFO')
            hinweis.label(text="   Add › Force Field › Wind")
        _regler(inhalt, sh, "wind_faktor")
        spalte = inhalt.column(align=True)
        _regler(spalte, d, "wasser_wind")
        _regler(spalte, d, "wasser_wind_tiefe")

    def _viewport_anzeige(self, body, sh):
        """Viewport only: acts at once and changes nothing in the bake."""
        kopf, inhalt = body.panel("hk_sim_anzeige", default_closed=True)
        kopf.label(text="Viewport Display", icon='HIDE_OFF')
        andere = self._andere_animation_kopf(kopf, sh)
        if inhalt is None:
            return
        zeile = inhalt.row(align=True)
        zeile.prop(sh, "domains_anzeigen", icon='MESH_CUBE')
        _hilfe(zeile, "domains_anzeigen")
        zeile.prop(sh, "gitter_anzeigen", icon='MESH_GRID')
        _hilfe(zeile, "gitter_anzeigen")
        if sh.gitter_anzeigen:
            _regler(inhalt, sh, "gitter_seiten")
        _regler(inhalt, sh, "ergebnisse_waehlbar", icon='RESTRICT_SELECT_OFF')
        _regler(inhalt, sh, "abspielen_leicht", icon='PLAY')
        self._andere_animation(inhalt, sh, andere)

    def _andere_animation_kopf(self, kopf, sh):
        """Pause Other Animation (sim/szene_pause.py): the section is closed
        by default, so slow other animation shows in its header too."""
        from ..sim import szene_pause
        daten = szene_pause.anzeige_daten(sh.id_data)
        if (not sh.abspielen_andere_pausieren and daten["objekte"]
                and daten["ms"] >= szene_pause.SPUERBAR_MS):
            warn = kopf.row()
            warn.alert = True
            warn.label(text="Slow playback", icon='ERROR')
        return daten

    def _andere_animation(self, layout, sh, daten):
        """The switch and one line: how many objects it holds still (or
        would) and what a frame of them costs, measured once frames changed
        and estimated from the vertices before."""
        from ..operators import sim_ops
        from ..sim import szene_pause
        _regler(layout, sh, "abspielen_andere_pausieren", icon='PAUSE')
        hinweis = _hinweis(layout)
        if daten["pausiert"]:
            hinweis.label(text=f"Paused: {sim_ops.anzahl(daten['pausiert'], 'object')} "
                               f"at frame {daten['frame']}", icon='PAUSE')
        elif daten["objekte"] and sh.abspielen_andere_pausieren:
            hinweis.label(text=f"Pauses {sim_ops.anzahl(daten['objekte'], 'object')}, "
                               f"~{daten['ersparnis']:.0f} ms/frame", icon='INFO')
        elif daten["objekte"] and daten["ms"] >= szene_pause.SPUERBAR_MS:
            warn = hinweis.row()
            warn.alert = True
            warn.label(text=f"{sim_ops.anzahl(daten['objekte'], 'object')} cost "
                            f"~{daten['ms']:.0f} ms per frame", icon='ERROR')
        elif sh.abspielen_andere_pausieren and not daten["nicht"]:
            hinweis.label(text="No heavy animation besides HyprW's", icon='INFO')
        if daten["nicht"]:
            n = len(daten["nicht"])
            _zeilen(hinweis, f"{sim_ops.anzahl(n, 'heavy object')} cannot be paused "
                             f"({daten['nicht'][0][1]})", icon='INFO')

    # ------------------------------------------------------------ Lens drops
    def _kameratropfen(self, layout, context, domain, frames):
        from ..operators import sim_ops
        from ..sim import linse
        d = domain.hydrokit
        props = context.scene.hydrokit
        header, body = layout.panel("hk_sim_linse", default_closed=True)
        # Main switch in the header: reachable even when the section is collapsed
        header.prop(d, "linse_an", text="")
        header.label(text="Lens Drops", icon='CAMERA_DATA')
        _hilfe(header, "linse_an")
        # The state in the header as well: the computation starts by itself
        # after every bake with Lens Drops on
        if sim_ops.LINSE_LAEUFT:
            header.progress(factor=props.linse_fortschritt, type='RING')
            header.label(text=f"{props.linse_fortschritt * 100:.0f} %")
        elif d.linse_an and context.scene.camera is None:
            header.label(text="no camera", icon='ERROR')
        elif d.linse_an and frames and not linse.ist_aktuell(context.scene):
            header.label(text="Compute needed")
        if body is None:
            return
        if d.linse_an and context.scene.camera is None:
            body.label(text="No scene camera", icon='ERROR')

        # Everything below is greyed out while the main switch is off
        teil = body.column()
        teil.enabled = d.linse_an
        glas = teil.column(align=True)
        _regler(glas, d, "linse_glas")
        if d.linse_glas == 'EIGEN':
            _regler(glas, d, "linse_winkel_vor")
            _regler(glas, d, "linse_winkel_rueck")
        _regler(glas, d, "linse_durchmesser")
        _regler(glas, d, "linse_fangradius")
        _regler(glas, d, "linse_unregelmaessig", slider=True)

        # Per type: the switch in the header, the share in the body
        for art, titel, icon in (("wasser", "Water on the Lens", 'MOD_FLUIDSIM'),
                                 ("spray", "Spray on the Lens", 'PARTICLES')):
            kopf, inhalt = teil.panel(f"hk_sim_linse_{art}", default_closed=False)
            kopf.prop(d, f"linse_{art}_haftet", text="")
            kopf.label(text=titel, icon=icon)
            _hilfe(kopf, f"linse_{art}_haftet")
            if inhalt is not None:
                menge = inhalt.row(align=True)
                menge.enabled = getattr(d, f"linse_{art}_haftet")
                _regler(menge, d, f"linse_{art}_haftanteil", slider=True)

        # Fine settings, collapsed: every value of the lens simulation
        kopf, inhalt = teil.panel("hk_sim_linse_aufprall", default_closed=True)
        kopf.label(text="Impact")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            _regler(spalte, d, "linse_spritzen")
            sub = spalte.column(align=True)
            sub.active = d.linse_spritzen
            _regler(sub, d, "linse_spritz_schwelle")
            _regler(sub, d, "linse_spritz_rest", slider=True)
            spalte.separator()
            _regler(spalte, d, "linse_ausbreiten")
            sub = spalte.column(align=True)
            sub.active = d.linse_ausbreiten
            _regler(sub, d, "linse_ausbreit_faktor")
            spalte.separator()
            _regler(spalte, d, "linse_wasser_groesse")

        kopf, inhalt = teil.panel("hk_sim_linse_rutschen", default_closed=True)
        kopf.prop(d, "linse_rutschen", text="")
        kopf.label(text="Sliding")
        _hilfe(kopf, "linse_rutschen")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            spalte.active = d.linse_rutschen
            _regler(spalte, d, "linse_haft_faktor")
            _regler(spalte, d, "linse_tempo_faktor")

        kopf, inhalt = teil.panel("hk_sim_linse_spuren", default_closed=True)
        kopf.prop(d, "linse_spuren", text="")
        kopf.label(text="Trails")
        _hilfe(kopf, "linse_spuren")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            spalte.active = d.linse_spuren and d.linse_rutschen
            _regler(spalte, d, "linse_perlen_schwelle")
            _regler(spalte, d, "linse_spur_groesse", slider=True)
            _regler(spalte, d, "linse_spur_streuung", slider=True)
            spalte.separator()
            _regler(spalte, d, "linse_spur_abstand")
            _regler(spalte, d, "linse_spur_abstand_streuung", slider=True)
            _regler(spalte, d, "linse_spur_wackeln")
            spalte.separator()
            _regler(spalte, d, "linse_spur_satelliten", slider=True)
            _regler(spalte, d, "linse_spur_satellit_groesse", slider=True)
            spalte.separator()
            zeile = spalte.row(align=True)
            zeile.prop(d, "linse_spur_streckung_min")
            zeile.prop(d, "linse_spur_streckung_max")
            _hilfe(zeile, "linse_spur_streckung_min")
            spalte.separator()
            _regler(spalte, d, "linse_spur_wischen")

        kopf, inhalt = teil.panel("hk_sim_linse_nebel", default_closed=True)
        kopf.prop(d, "linse_nebel_haftet", text="")
        kopf.label(text="Mist on the Lens", icon='OUTLINER_OB_VOLUME')
        _hilfe(kopf, "linse_nebel_haftet")
        if inhalt is not None:
            menge = inhalt.row(align=True)
            menge.enabled = d.linse_nebel_haftet
            _regler(menge, d, "linse_nebel_haftanteil", slider=True)
            spalte = inhalt.column(align=True)
            spalte.active = d.linse_nebel_haftet
            _regler(spalte, d, "linse_nebel_fang")
            _regler(spalte, d, "linse_nebel_menge")
            _regler(spalte, d, "linse_nebel_verschmelzen")
            sub = spalte.column(align=True)
            sub.active = d.linse_nebel_verschmelzen
            _regler(sub, d, "linse_nebel_zu_tropfen")

        kopf, inhalt = teil.panel("hk_sim_linse_grenzen", default_closed=True)
        kopf.label(text="Limits")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            _regler(spalte, d, "linse_max_tropfen")
            _regler(spalte, d, "linse_max_nebel")

        # Depth of field: the same settings as Camera > Depth of Field,
        # directly connected (changes the scene camera itself)
        cam = context.scene.camera
        if cam is not None and cam.type == 'CAMERA':
            dof = cam.data.dof
            kopf, inhalt = teil.panel("hk_sim_linse_dof", default_closed=False)
            kopf.prop(dof, "use_dof", text="")
            kopf.label(text="Depth of Field")
            _hilfe(kopf, "use_dof")
            if inhalt is not None:
                inhalt.active = dof.use_dof
                spalte = inhalt.column()
                spalte.use_property_split = True
                _regler(spalte, dof, "focus_object", text="Focus on Object")
                zeile = spalte.row(align=True)
                zeile.active = dof.focus_object is None
                zeile.prop(dof, "focus_distance", text="Focus Distance")
                zeile.operator("ui.eyedropper_depth", icon='EYEDROPPER',
                               text="").prop_data_path = "scene.camera.data.dof.focus_distance"
                _hilfe(zeile, "focus_distance")
                spalte.separator()
                spalte.label(text="Aperture")
                _regler(spalte, dof, "aperture_fstop", text="F-Stop")
                _regler(spalte, dof, "aperture_blades", text="Blades")
                _regler(spalte, dof, "aperture_rotation", text="Rotation")
                _regler(spalte, dof, "aperture_ratio", text="Ratio")

        # Last: the button that uses all the settings above
        teil.separator()
        if sim_ops.LINSE_LAEUFT:
            teil.label(text=f"Computing … {props.linse_fortschritt * 100:.0f} %  (Esc cancels)",
                       icon='TIME')
            teil.progress(factor=props.linse_fortschritt, type='BAR')
        else:
            zeile = teil.row()
            zeile.scale_y = 1.3
            zeile.operator("hydrokit.linse_berechnen", icon='FILE_REFRESH')


classes = (HYDROKIT_PT_sim,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
