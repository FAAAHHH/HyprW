# HyprW — panel for HyprW's own simulation
# SPDX-License-Identifier: GPL-3.0-or-later
#
# A tab of its own, so it stays clear what runs on HyprW's engine and what
# on FLIP Fluids.

import bpy

from ..sim import anzeige, backen, cache, geometrie

ROLLEN = (
    ('DOMAIN',   "Domain",    'MESH_CUBE'),
    ('FLUID',    "Water",     'MOD_FLUIDSIM'),
    ('OBSTACLE', "Obstacle",  'MESH_ICOSPHERE'),
    ('INFLOW',   "Inflow",    'IMPORT'),
    ('OUTFLOW',  "Outflow",   'EXPORT'),
    ('MIST_DOMAIN', "Mist Domain", 'OUTLINER_OB_VOLUME'),
    ('SPRAY_DOMAIN', "Spray Domain", 'PARTICLES'),
)


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


class HYDROKIT_PT_sim(bpy.types.Panel):
    bl_label = "HyprW — Own Engine"
    bl_idname = "HYDROKIT_PT_sim"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "HyprW"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        props = scene.hydrokit
        domain = geometrie.finde_domain(scene)

        if props.backt:
            box = layout.box()
            box.label(text=f"Baking … {props.bake_fortschritt*100:.0f} %",
                      icon='TIME')
            box.prop(props, "bake_fortschritt", text="", slider=True)
            zeile = box.row()
            zeile.scale_y = 1.3
            zeile.operator("hydrokit.sim_stop", icon='CANCEL')
            return

        self._rollen(layout, context, domain)
        if domain is None:
            hinweis = layout.column(align=True)
            hinweis.scale_y = 0.8
            hinweis.separator()
            hinweis.label(text="No domain yet. Select an object", icon='INFO')
            hinweis.label(text="and click Domain above.")
            return
        # Bake right under the roles — the button people look for first
        self._backen(layout, context, domain)
        self._domain(layout, context, domain)
        self._kameratropfen(layout, context, domain)

    # ------------------------------------------------------------ Roles
    def _rollen(self, layout, context, domain):
        header, body = layout.panel("hk_sim_rollen", default_closed=False)
        header.label(text="Object Role", icon='OBJECT_DATA')
        if body is None:
            return
        obj = context.active_object
        if obj is None:
            body.label(text="Nothing selected", icon='INFO')
            return
        info = body.column(align=True)
        info.scale_y = 0.8
        aktuell = obj.hydrokit.typ
        info.label(text=f"{obj.name}: "
                        + dict((r[0], r[1]) for r in ROLLEN).get(aktuell, "none"))

        gitter = body.grid_flow(columns=2, even_columns=True, align=True)
        for key, beschriftung, icon in ROLLEN:
            zelle = gitter.row(align=True)
            zelle.enabled = aktuell != key
            if key == 'DOMAIN' and domain is not None and domain is not obj:
                zelle.enabled = False
            op = zelle.operator("hydrokit.set_type", text=beschriftung, icon=icon)
            op.typ = key
        self._rollen_einstellungen(body, context, obj, aktuell)

        zeile = body.row()
        zeile.enabled = aktuell != 'NONE'
        op = zeile.operator("hydrokit.set_type", text="Clear Role",
                            icon='X')
        op.typ = 'NONE'

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
            hinweis = kasten.column(align=True)
            hinweis.scale_y = 0.8
            if aktuell == 'DOMAIN':
                hinweis.label(text="In the Domain section below", icon='INFO')
            else:
                art = "Mist" if aktuell == 'MIST_DOMAIN' else "Spray"
                hinweis.label(text=f"{art} keeps flying inside this box", icon='INFO')
                hinweis.label(text="   after leaving the domain; no other settings")
                g = obj.dimensions
                hinweis.label(text=f"   Size {g.x:.1f} x {g.y:.1f} x {g.z:.1f} m")
            return

        kasten.prop(h, "rolle_aktiv")
        spalte = kasten.column(align=True)
        spalte.enabled = h.rolle_aktiv
        if aktuell == 'INFLOW':
            spalte.prop(h, "zufluss_geschwindigkeit")
            spalte.prop(h, "zufluss_erzwingen")
            spalte.prop(h, "zufluss_substeps")
            if h.zufluss_erzwingen and not any(h.zufluss_geschwindigkeit):
                hinweis = kasten.column(align=True)
                hinweis.scale_y = 0.8
                hinweis.label(text="Velocity 0: the water stays", icon='INFO')
                hinweis.label(text="   in the inflow and only seeps out")
        elif aktuell == 'FLUID':
            spalte.prop(h, "start_geschwindigkeit")
        elif aktuell == 'OBSTACLE':
            spalte.prop(h, "hindernis_umkehren")
            spalte.prop(h, "hindernis_reibung", slider=True)
            spalte.prop(h, "hindernis_ausdehnung")
            spalte.separator()
            d = geometrie.finde_domain(context.scene)
            dh = d.hydrokit if d is not None else None
            gischt = spalte.column(align=True)
            gischt.enabled = bool(dh and dh.gischt)
            gischt.prop(h, "hindernis_gischt")
            nebel = spalte.column(align=True)
            nebel.enabled = bool(dh and dh.gischt and dh.nebel_an)
            nebel.prop(h, "hindernis_nebel")
            sheet = spalte.column(align=True)
            sheet.enabled = bool(dh and dh.sheeting)
            sheet.prop(h, "hindernis_sheeting")
        elif aktuell == 'OUTFLOW':
            spalte.prop(h, "abfluss_wasser")
            spalte.prop(h, "abfluss_gischt")
            spalte.prop(h, "abfluss_umkehren")
            spalte.prop(h, "abfluss_allmaehlich")
            rate = spalte.column(align=True)
            rate.enabled = h.abfluss_allmaehlich
            rate.prop(h, "abfluss_rate")

    # ------------------------------------------------------------ Domain
    def _domain(self, layout, context, domain):
        header, body = layout.panel("hk_sim_domain", default_closed=False)
        header.label(text="Domain", icon='MESH_CUBE')
        if body is None:
            return
        d = domain.hydrokit
        body.prop(d, "aufloesung")

        masse = geometrie.gitter_masse(domain, d.aufloesung)
        info = body.column(align=True)
        info.scale_y = 0.8
        if masse:
            i, j, k, dx = masse
            voxel = i * j * k
            info.label(text=f"Grid {i} x {j} x {k}")
            info.label(text="Voxels: {:,}".format(voxel).replace(",", ","))
            info.label(text=f"Voxel size {dx*100:.1f} cm")
            # rough estimate, measured on our own bakes
            gb = voxel * 1024 / 1024**3
            info.label(text=f"Estimated memory {gb:.1f} GB"
                            + ("  (with whitewater)" if d.gischt else ""))

        zeile = body.row(align=True)
        zeile.prop(context.scene.hydrokit, "domains_anzeigen", icon='MESH_CUBE')
        zeile.prop(context.scene.hydrokit, "gitter_anzeigen", icon='MESH_GRID')
        body.prop(context.scene.hydrokit, "ergebnisse_waehlbar", icon='RESTRICT_SELECT_OFF')
        if context.scene.hydrokit.gitter_anzeigen:
            body.prop(context.scene.hydrokit, "gitter_seiten")

        body.separator()
        spalte = body.column(align=True)
        spalte.prop(d, "cfl")
        spalte.prop(d, "oberflaechen_unterteilung")
        spalte.prop(d, "vorschau_aufloesung")
        spalte.prop(d, "vorschau_im_viewport")
        spalte.prop(d, "sheeting")
        spalte.prop(d, "gischt")
        if d.gischt:
            nebel = body.column(align=True)
            nebel.prop(d, "nebel_an", text="Mist")
            unter = nebel.column(align=True)
            unter.enabled = d.nebel_an
            unter.prop(d, "nebel_spritz_anteil")
            unter.prop(d, "nebel_spritz_schwelle")
            nd = geometrie.finde_nebel_domain(context.scene)
            hinweis = unter.column(align=True)
            hinweis.scale_y = 0.8
            if nd is not None and masse:
                hinweis.label(text=f"Mist domain: {nd.name}", icon='OUTLINER_OB_VOLUME')
                hinweis.label(text=f"   voxels {masse[3]*100:.1f} cm like the domain")
            else:
                hinweis.label(text="Without a mist domain, mist ends at the border",
                              icon='INFO')
            folgen = body.column(align=True)
            folgen.prop(d, "schaum_mitfuehrung", slider=True)
            folgen.prop(d, "blasen_mitfuehrung", slider=True)
            body.label(text="Amount (no new bake needed):")
            menge = body.column(align=True)
            for k, t in (("menge_foam", "Foam"), ("menge_bubble", "Bubbles"),
                         ("menge_spray", "Spray"), ("menge_nebel", "Mist")):
                menge.prop(d, k, text=t, slider=True)
            menge.prop(d, "nebel_dichte_anzeige", text="Mist Density")
            menge.prop(d, "schaum_blasen")
            menge.prop(d, "schaum_luftanteil", slider=True)
            menge.prop(d, "spray_farbe")
            # The water body, from measured optics (operators/materials_ops.py)
            sh = context.scene.hydrokit
            wasser = body.column(align=True)
            wasser.label(text="Water (no new bake needed):")
            wasser.prop(sh, "wasser_variante", text="")
            wasser.prop(sh, "wasser_sichtweite")
            wasser.prop(sh, "wasser_geloest")
            wasser.prop(sh, "wasser_kraeuselung")
            # A real shutter turns fast drops into streaks; without it every
            # drop is a sharp ball and whitewater looks like hail
            rd = context.scene.render
            unschaerfe = body.column(align=True)
            unschaerfe.prop(rd, "use_motion_blur", text="Motion Blur (render)")
            zeile = unschaerfe.row(align=True)
            zeile.enabled = rd.use_motion_blur
            zeile.prop(rd, "motion_blur_shutter", text="Shutter (frames)")

        # Wind from Blender's wind objects — a typo in a strength turns a breeze
        # into a hurricane that shreds every drop (seen 29.10.2026: 40 m/s)
        wind = geometrie.windvektor(context.scene, context.scene.hydrokit.wind_faktor)
        if wind.length > 0.05:
            stufe, name = _beaufort(wind.length)
            zeile = body.row()
            zeile.alert = stufe >= 8
            zeile.label(text=f"Wind {wind.length:.1f} m/s — Beaufort {stufe}, {name}",
                        icon='ERROR' if stufe >= 8 else 'FORCE_WIND')

        body.separator()
        body.prop(d, "cache_pfad")

        zeilen = geometrie.sammle_objekte(context.scene)
        uebersicht = body.column(align=True)
        uebersicht.scale_y = 0.8
        uebersicht.separator()
        for key, beschriftung, _ in ROLLEN[1:]:
            n = len(zeilen.get(key, []))
            if n:
                uebersicht.label(text=f"{beschriftung}: {n}")

    # ------------------------------------------------------------ Lens drops
    def _kameratropfen(self, layout, context, domain):
        d = domain.hydrokit
        header, body = layout.panel("hk_sim_linse", default_closed=True)
        # Main switch in the header: reachable even when the section is collapsed
        header.prop(d, "linse_an", text="")
        header.label(text="Lens Drops", icon='CAMERA_DATA')
        if body is None:
            return
        if d.linse_an and context.scene.camera is None:
            body.label(text="No scene camera", icon='ERROR')

        # Everything below is greyed out while the main switch is off
        teil = body.column()
        teil.enabled = d.linse_an
        glas = teil.column(align=True)
        glas.prop(d, "linse_glas")
        if d.linse_glas == 'EIGEN':
            glas.prop(d, "linse_winkel_vor")
            glas.prop(d, "linse_winkel_rueck")
        glas.prop(d, "linse_durchmesser")
        glas.prop(d, "linse_fangradius")
        glas.prop(d, "linse_unregelmaessig", slider=True)

        for art, titel, icon in (("wasser", "Water", 'MOD_FLUIDSIM'),
                                 ("spray", "Spray", 'PARTICLES'),
                                 ("nebel", "Mist", 'OUTLINER_OB_VOLUME')):
            spalte = teil.column(align=True)
            zeile = spalte.row(align=True)
            zeile.label(text=titel, icon=icon)
            zeile.prop(d, f"linse_{art}_haftet", toggle=True)
            menge = spalte.row(align=True)
            menge.enabled = getattr(d, f"linse_{art}_haftet")
            menge.prop(d, f"linse_{art}_haftanteil", slider=True)

        # Fine settings, collapsed: every value of the lens simulation
        kopf, inhalt = teil.panel("hk_sim_linse_aufprall", default_closed=True)
        kopf.label(text="Impact")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            spalte.prop(d, "linse_spritzen")
            sub = spalte.column(align=True)
            sub.active = d.linse_spritzen
            sub.prop(d, "linse_spritz_schwelle")
            sub.prop(d, "linse_spritz_rest", slider=True)
            spalte.separator()
            spalte.prop(d, "linse_ausbreiten")
            sub = spalte.column(align=True)
            sub.active = d.linse_ausbreiten
            sub.prop(d, "linse_ausbreit_faktor")
            spalte.separator()
            spalte.prop(d, "linse_wasser_groesse")

        kopf, inhalt = teil.panel("hk_sim_linse_rutschen", default_closed=True)
        kopf.prop(d, "linse_rutschen", text="")
        kopf.label(text="Sliding")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            spalte.active = d.linse_rutschen
            spalte.prop(d, "linse_haft_faktor")
            spalte.prop(d, "linse_tempo_faktor")

        kopf, inhalt = teil.panel("hk_sim_linse_spuren", default_closed=True)
        kopf.prop(d, "linse_spuren", text="")
        kopf.label(text="Trails")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            spalte.active = d.linse_spuren and d.linse_rutschen
            spalte.prop(d, "linse_perlen_schwelle")
            spalte.prop(d, "linse_spur_groesse", slider=True)
            spalte.prop(d, "linse_spur_streuung", slider=True)
            spalte.separator()
            spalte.prop(d, "linse_spur_abstand")
            spalte.prop(d, "linse_spur_abstand_streuung", slider=True)
            spalte.prop(d, "linse_spur_wackeln")
            spalte.separator()
            spalte.prop(d, "linse_spur_satelliten", slider=True)
            spalte.prop(d, "linse_spur_satellit_groesse", slider=True)
            spalte.separator()
            zeile = spalte.row(align=True)
            zeile.prop(d, "linse_spur_streckung_min")
            zeile.prop(d, "linse_spur_streckung_max")
            spalte.separator()
            spalte.prop(d, "linse_spur_wischen")

        kopf, inhalt = teil.panel("hk_sim_linse_nebel", default_closed=True)
        kopf.label(text="Mist on the Lens")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            spalte.active = d.linse_nebel_haftet
            spalte.prop(d, "linse_nebel_fang")
            spalte.prop(d, "linse_nebel_menge")
            spalte.prop(d, "linse_nebel_verschmelzen")
            sub = spalte.column(align=True)
            sub.active = d.linse_nebel_verschmelzen
            sub.prop(d, "linse_nebel_zu_tropfen")

        kopf, inhalt = teil.panel("hk_sim_linse_grenzen", default_closed=True)
        kopf.label(text="Limits")
        if inhalt is not None:
            spalte = inhalt.column(align=True)
            spalte.prop(d, "linse_max_tropfen")
            spalte.prop(d, "linse_max_nebel")

        teil.separator()
        props = context.scene.hydrokit
        from ..operators import sim_ops
        if sim_ops.LINSE_LAEUFT:
            zeile = teil.row()
            zeile.label(text=f"Computing … {props.linse_fortschritt*100:.0f} %  (Esc cancels)",
                        icon='TIME')
            teil.prop(props, "linse_fortschritt", text="", slider=True)
        else:
            teil.operator("hydrokit.linse_berechnen", icon='FILE_REFRESH')

        # Depth of field: the same settings as Camera > Depth of Field,
        # directly connected (changes the scene camera itself)
        cam = context.scene.camera
        if cam is None or cam.type != 'CAMERA':
            return
        dof = cam.data.dof
        kopf, inhalt = teil.panel("hk_sim_linse_dof", default_closed=False)
        kopf.prop(dof, "use_dof", text="")
        kopf.label(text="Depth of Field")
        if inhalt is None:
            return
        inhalt.active = dof.use_dof
        spalte = inhalt.column()
        spalte.use_property_split = True
        spalte.prop(dof, "focus_object", text="Focus on Object")
        zeile = spalte.row(align=True)
        zeile.active = dof.focus_object is None
        zeile.prop(dof, "focus_distance", text="Focus Distance")
        zeile.operator("ui.eyedropper_depth", icon='EYEDROPPER',
                       text="").prop_data_path = "scene.camera.data.dof.focus_distance"
        spalte.separator()
        spalte.label(text="Aperture")
        spalte.prop(dof, "aperture_fstop", text="F-Stop")
        spalte.prop(dof, "aperture_blades", text="Blades")
        spalte.prop(dof, "aperture_rotation", text="Rotation")
        spalte.prop(dof, "aperture_ratio", text="Ratio")

    # ------------------------------------------------------------ Bake
    def _rechengeraet(self, body, domain):
        """CPU / GPU / Both — and with Both, a choice per step."""
        d = domain.hydrokit
        kasten = body.box()
        kasten.label(text="Compute Device", icon='SETTINGS')
        kasten.row(align=True).prop(d, "rechengeraet", expand=True)
        verfuegbar, name, grund = backen.gpu_info()
        hinweis = kasten.column(align=True)
        hinweis.scale_y = 0.8
        if verfuegbar:
            hinweis.label(text=name, icon='CHECKMARK')
        else:
            hinweis.alert = d.rechengeraet != 'CPU'
            hinweis.label(text=f"No GPU: {grund}", icon='INFO')
            if d.rechengeraet != 'CPU':
                hinweis.label(text="   everything runs on the CPU")
        if d.rechengeraet == 'BEIDES':
            from ..operators import sim_ops
            if sim_ops.MESSUNG["laeuft"]:
                kasten.label(text=f"Measuring … frame {sim_ops.MESSUNG['frame']} of "
                                  f"{sim_ops.PROBE_FRAMES}  (Esc cancels)", icon='TIME')
            else:
                zeile = kasten.row()
                zeile.enabled = verfuegbar
                zeile.operator("hydrokit.rechengeraet_messen", icon='TIME')
            gemessen = {}
            if d.rechengeraet_messung:
                import json
                try:
                    gemessen = json.loads(d.rechengeraet_messung).get("steps", {})
                except ValueError:
                    gemessen = {}
            spalte = kasten.column(align=True)
            for key, beschriftung, *_ in backen.STUFEN:
                if backen.hat_gpu(key):
                    zeile = spalte.row(align=True)
                    zeile.prop(d, "geraet_" + key, text=beschriftung)
                    werte = gemessen.get(key)
                    if werte and werte["gpu_seconds"] > 0 and werte["cpu_seconds"] > 0:
                        faktor = werte["cpu_seconds"] / werte["gpu_seconds"]
                        zeile.label(text=f"GPU {faktor:.1f}x" if faktor >= 1.0
                                    else f"CPU {1.0 / faktor:.1f}x")
                else:
                    zeile = spalte.row()
                    zeile.enabled = False
                    zeile.label(text=f"{beschriftung}: CPU (no GPU version yet)")
        elif d.rechengeraet == 'GPU' and verfuegbar:
            stufen = [name for key, name, *_ in backen.STUFEN if backen.hat_gpu(key)]
            hinweis.label(text="GPU: " + ", ".join(stufen))
            hinweis.label(text="   the rest on the CPU")

    def _backen(self, layout, context, domain):
        header, body = layout.panel("hk_sim_backen", default_closed=False)
        header.label(text="Bake", icon='PHYSICS')
        if body is None:
            return
        scene = context.scene
        pfad = cache.pfad_aufloesen(domain.hydrokit.cache_pfad)
        frames = cache.gebackene_frames(pfad) if pfad else []

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

        info = body.column(align=True)
        info.scale_y = 0.8
        info.label(text=f"Frames {scene.frame_start} to {scene.frame_end}")
        if frames:
            info.label(text=f"Baked: {len(frames)} frames", icon='CHECKMARK')
            fehlend = (scene.frame_end - scene.frame_start + 1) - len(frames)
            if fehlend > 0:
                info.label(text=f"{fehlend} still missing")
        else:
            info.label(text="Nothing baked yet")

        self._rechengeraet(body, domain)

        body.separator()
        zeile = body.row()
        zeile.scale_y = 1.4
        zeile.operator("hydrokit.sim_bake", icon='PLAY')
        if frames:
            body.separator()
            blick = body.row()
            blick.scale_y = 1.3
            blick.operator("hydrokit.sim_look", icon='MATERIAL')
            unten = body.row(align=True)
            unten.operator("hydrokit.sim_display", icon='FILE_REFRESH')
            unten.operator("hydrokit.sim_clear", text="", icon='TRASH')
            zustand = body.column(align=True)
            zustand.scale_y = 0.8
            zustand.label(text="Display follows the frame: "
                               + ("yes" if anzeige.handler_laeuft() else "no"))


classes = (HYDROKIT_PT_sim,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
