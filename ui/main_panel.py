# HyprW — main panel
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from ..utils import flip_bridge
from ..operators import (doctor_ops, whitewater_ops, wind_ops, materials_ops,
                         boundary_ops, mist_ops, camera_ops,
                         preset_ops, optimize_ops)


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


class HYDROKIT_PT_main(bpy.types.Panel):
    bl_label = "HyprW"
    bl_idname = "HYDROKIT_PT_main"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "HyprW"

    def draw(self, context):
        layout = self.layout

        if not flip_bridge.is_available():
            box = layout.box()
            box.label(text="FLIP Fluids add-on not found", icon='INFO')
            col = box.column(align=True)
            col.scale_y = 0.8
            col.label(text="These tools work on FLIP Fluids domains.")
            col.label(text="Own simulation: panel 'HyprW — Own Engine'.")
            return

        domain = flip_bridge.get_domain(context)
        if domain is None:
            box = layout.box()
            box.label(text="No domain in the scene", icon='INFO')
            box.label(text="Create a FLIP Fluids domain object first.")
            return

        props = context.scene.hydrokit
        self._draw_preset(layout, context, props)
        self._draw_doctor(layout, context, props)
        self._draw_info(layout, context, domain, props)
        self._draw_optimierung(layout, context, props)
        self._draw_material(layout, context, props)
        self._draw_whitewater(layout, context, props)
        self._draw_wind(layout, context, props)
        self._draw_ufer(layout, context, props)
        self._draw_nebel(layout, context, props)
        self._draw_tropfen(layout, context, props)

    # ------------------------------------------------------------ Preset
    def _draw_preset(self, layout, context, props):
        header, body = layout.panel("hydrokit_preset", default_closed=False)
        header.label(text="Water Type", icon='PRESET')
        if body is None:
            return

        body.prop(props, "preset", text="")
        preset = preset_ops.PRESETS.get(props.preset)
        if preset:
            info = body.column(align=True)
            info.scale_y = 0.78
            for zeile in _umbrechen(preset["info"], 44):
                info.label(text=zeile)
            info.separator()
            info.label(text=f"Scale: {preset['massstab']}")

        body.separator()
        z = body.row()
        z.scale_y = 1.3
        z.operator("hydrokit.apply_preset", icon='CHECKMARK')

    # ------------------------------------------------------------ Doctor
    def _draw_doctor(self, layout, context, props):
        befunde = doctor_ops.befunde_holen()
        fehler = [b for b in befunde if b.schwere == 'FEHLER']
        warnungen = [b for b in befunde if b.schwere == 'WARNUNG']

        if fehler:
            titel, icon = f"Scene Doctor: {len(fehler)} errors", 'ERROR'
            zu = False
        elif warnungen:
            titel, icon = f"Scene Doctor: {len(warnungen)} notes", 'INFO'
            zu = False
        elif befunde:
            titel, icon = "Scene Doctor: all good", 'CHECKMARK'
            zu = True
        else:
            titel, icon = "Scene Doctor", 'VIEWZOOM'
            zu = False

        header, body = layout.panel("hydrokit_doc", default_closed=zu)
        header.label(text=titel, icon=icon)
        if body is None:
            return

        for b in befunde:
            if b.schwere == 'INFO' and (fehler or warnungen):
                continue                      # with problems, only what matters
            box = body.box()
            box.alert = (b.schwere == 'FEHLER')
            zeile = box.row()
            zeile.label(
                text=b.titel,
                icon={'FEHLER': 'ERROR', 'WARNUNG': 'INFO'}.get(b.schwere, 'DOT'),
            )
            col = box.column(align=True)
            col.scale_y = 0.75
            for stueck in _umbrechen(b.text, 46):
                col.label(text=stueck)
            if b.fix:
                knopf = box.row()
                knopf.scale_y = 1.1
                knopf.operator(b.fix, icon='TOOL_SETTINGS')

        body.separator()
        zeile = body.row()
        zeile.scale_y = 1.3
        zeile.operator("hydrokit.run_doctor",
                       text="Check Again" if befunde else "Check Scene",
                       icon='VIEWZOOM')
        if not befunde:
            hinweis = body.column(align=True)
            hinweis.scale_y = 0.75
            hinweis.label(text="Checks memory, obstacle thickness,")
            hinweis.label(text="scale, CFL and cache path.")

    # ------------------------------------------------------------ Material
    def _draw_material(self, layout, context, props):
        header, body = layout.panel("hydrokit_mat", default_closed=False)
        header.label(text="Materials", icon='MATERIAL')
        if body is None:
            return

        body.prop(props, "wasser_variante")
        body.prop(props, "wasser_sichtweite")
        body.prop(props, "wasser_geloest")
        body.prop(props, "wasser_kraeuselung")
        hin = body.column(align=True)
        hin.scale_y = 0.78
        hin.label(text="Measured optics: thin water stays")
        hin.label(text="clear, deep water takes its colour.")

        info = body.column(align=True)
        info.scale_y = 0.8
        info.label(text="Water: IOR 1.333 + Volume Absorption")
        info.label(text="Whitewater: its own material per type")

        surface = flip_bridge.get_surface_object(context)
        if surface is not None and surface.data.materials:
            aktuell = surface.data.materials[0]
            if aktuell is not None:
                info.separator()
                info.label(text=f"Current: {aktuell.name}")

        body.separator()
        row = body.row()
        row.scale_y = 1.4
        row.operator("hydrokit.apply_materials", icon='CHECKMARK')

    # ------------------------------------------------------------ Overview
    def _draw_info(self, layout, context, domain, props):
        header, body = layout.panel("hydrokit_info", default_closed=False)
        header.label(text="Scene", icon='MOD_FLUIDSIM')
        if body is None:
            return

        col = body.column(align=True)
        col.scale_y = 0.85
        col.label(text=f"Domain: {domain.name}")

        grid = flip_bridge.get_grid_dimensions(context)
        voxel = flip_bridge.get_voxel_size(context)
        if grid:
            gx, gy, gz, total = grid
            col.label(text=f"Grid: {gx} x {gy} x {gz}")
            col.label(text="Voxels: {:,}".format(total).replace(",", ","))
        if voxel:
            col.label(text=f"Voxel size: {voxel * 100:.1f} cm")

        groesse = max(domain.dimensions)
        col.separator()
        col.label(text=f"Longest side: {groesse:.1f} m")

        ww = flip_bridge.get_whitewater_objects(context)
        surface = flip_bridge.get_surface_object(context)
        col.label(text="Surface: " + ("present" if surface else "missing"))
        col.label(text=f"Whitewater objects: {len(ww)} of 4")

        if ww:
            fehlend = flip_bridge.check_sockets(next(iter(ww.values())))
            if fehlend:
                warn = body.box()
                warn.alert = True
                warn.label(text="Node sockets differ", icon='ERROR')
                wcol = warn.column(align=True)
                wcol.scale_y = 0.8
                wcol.label(text="FLIP Fluids has changed its node")
                wcol.label(text="groups. Not found:")
                for name in fehlend:
                    wcol.label(text=f"   {name}")

    # ------------------------------------------------------------ Whitewater
    def _draw_whitewater(self, layout, context, props):
        header, body = layout.panel("hydrokit_ww", default_closed=False)
        header.label(text="Whitewater Size", icon='PARTICLES')
        if body is None:
            return

        ww = flip_bridge.get_whitewater_objects(context)
        if not ww:
            body.label(text="Not baked with whitewater yet", icon='INFO')
            return

        skalen = whitewater_ops.compute_scales(context)
        if skalen:
            col = body.column(align=True)
            col.scale_y = 0.8
            col.label(text="Computed from the voxel size:")
            for art in ("foam", "bubble", "spray", "dust"):
                if art in ww:
                    aktuell = flip_bridge.get_whitewater_particle_scale(ww[art])
                    ziel = skalen[art]
                    hinweis = ""
                    if aktuell is not None and ziel > 0:
                        verhaeltnis = aktuell / ziel
                        if verhaeltnis < 0.4:
                            hinweis = "  (far too small)"
                        elif verhaeltnis > 2.5:
                            hinweis = "  (far too large)"
                    ist = f"{aktuell * 100:.1f}" if aktuell is not None else "?"
                    col.label(text=f"{art}: {ist} cm  ->  {ziel * 100:.1f} cm{hinweis}")

        body.separator()
        body.prop(props, "whitewater_scale_multiplier")

        sub = body.column(align=True)
        sub.scale_y = 0.9
        row = sub.row(align=True)
        row.prop(props, "scale_foam")
        row.prop(props, "scale_bubble")
        row = sub.row(align=True)
        row.prop(props, "scale_spray")
        row.prop(props, "scale_dust")

        body.separator()
        op = body.row()
        op.scale_y = 1.4
        op.operator("hydrokit.auto_whitewater_scale", icon='CHECKMARK')

        body.separator()
        hinweis = body.column(align=True)
        hinweis.scale_y = 0.78
        hinweis.label(text="FLIP shows whitewater as a point cloud —")
        hinweis.label(text="Cycles turns it into equally sized")
        hinweis.label(text="spheres. This stretches them along")
        hinweis.label(text="the motion and varies the size.")
        z = body.row()
        z.scale_y = 1.3
        z.operator("hydrokit.whitewater_look", icon='PARTICLEMODE')
        body.operator("hydrokit.whitewater_look_reset", icon='LOOP_BACK')


    # ------------------------------------------------------------ Wind
    def _draw_wind(self, layout, context, props):
        header, body = layout.panel("hydrokit_wind", default_closed=True)
        header.label(text="Wind", icon='FORCE_WIND')
        if body is None:
            return

        winde = wind_ops.finde_windobjekte(context)
        aktiv = wind_ops.gespeicherter_wind(context)

        info = body.column(align=True)
        info.scale_y = 0.8
        if winde:
            info.label(text=f"{len(winde)} wind object(s):")
            for o in winde[:3]:
                info.label(text=f"   {o.name}  ({o.field.strength:.1f})")
            v = wind_ops.windvektor(context, props.wind_faktor)
            info.separator()
            info.label(text=f"gives {v.length:.1f} m/s")
            info.label(text=f"({v.x:+.1f}, {v.y:+.1f}, {v.z:+.1f})")
        else:
            info.label(text="No wind object in the scene.")
            info.label(text="Add -> Force Field -> Wind")

        if aktiv is not None:
            info.separator()
            info.label(text=f"In cache: {aktiv.length:.1f} m/s", icon='CHECKMARK')

        info.separator()
        info.label(text="Acts on spray and mist in the")
        info.label(text="air. Water in the pool and bubbles")
        info.label(text="stay untouched.")

        body.separator()
        body.prop(props, "wind_faktor")
        zeile = body.row()
        zeile.scale_y = 1.3
        zeile.enabled = bool(winde)
        zeile.operator("hydrokit.sync_wind", icon='CHECKMARK')
        if aktiv is not None:
            body.operator("hydrokit.remove_wind", icon='TRASH')

    # ------------------------------------------------------------ Banks
    def _draw_ufer(self, layout, context, props):
        header, body = layout.panel("hydrokit_ufer", default_closed=True)
        header.label(text="Banks and Borders", icon='MOD_FLUID')
        if body is None:
            return

        d = flip_bridge.get_domain_props(context)
        if d is None:
            return

        kol = list(d.simulation.fluid_boundary_collisions)
        offen = [boundary_ops.SEITEN[i] for i, zu in enumerate(kol) if not zu]

        info = body.column(align=True)
        info.scale_y = 0.8
        richtung = boundary_ops.fliessrichtung(context)
        if richtung is None:
            info.label(text="Flow direction: not detectable")
            info.label(text="Inflow needs a horizontal")
            info.label(text="velocity, or set wind")
        else:
            achse, vz = richtung
            info.label(text="Flow direction: "
                            + boundary_ops.SEITEN[achse * 2 + (1 if vz > 0 else 0)])
        info.separator()
        info.label(text="Open: " + (", ".join(offen) if offen else "no side"))
        if not offen:
            info.label(text="All sides are solid walls —")
            info.label(text="water piles up and overflows.")

        body.separator()
        body.prop(props, "beide_ufer")
        zeile = body.row()
        zeile.scale_y = 1.3
        zeile.enabled = richtung is not None
        zeile.operator("hydrokit.open_banks", icon='CHECKMARK')
        if offen:
            body.operator("hydrokit.close_banks", icon='LOOP_BACK')


    # ------------------------------------------------------------ Mist
    def _draw_nebel(self, layout, context, props):
        header, body = layout.panel("hydrokit_nebel", default_closed=True)
        header.label(text="Mist", icon='VOLUME_DATA')
        if body is None:
            return

        spray = flip_bridge.get_whitewater_objects(context).get("spray")
        if spray is None:
            body.label(text="No spray — bake with whitewater", icon='INFO')
            return

        vorhanden = bpy.data.objects.get(mist_ops.OBJEKT) is not None
        info = body.column(align=True)
        info.scale_y = 0.8
        info.label(text=f"Source: {spray.name}")
        info.label(text="Spray becomes a real volume.")
        info.label(text="No new bake needed.")

        v = mist_ops.vorschlag(context)
        if v is not None:
            info.separator()
            info.label(text=f"Suggestion: {v[0]*100:.1f} cm / {v[1]:.2f}")

        body.separator()
        body.operator("hydrokit.auto_mist", icon='DRIVER')
        body.prop(props, "nebel_dichte")
        body.prop(props, "nebel_radius")
        body.prop(props, "nebel_hoehe_an")
        unter = body.row()
        unter.enabled = props.nebel_hoehe_an
        unter.prop(props, "nebel_hoehe")

        body.separator()
        zeile = body.row()
        zeile.scale_y = 1.3
        zeile.operator("hydrokit.create_mist",
                       text="Update Mist" if vorhanden else "Create Mist",
                       icon='CHECKMARK')
        if vorhanden:
            body.operator("hydrokit.remove_mist", icon='TRASH')


    # ------------------------------------------------------------ Lens drops
    def _draw_tropfen(self, layout, context, props):
        header, body = layout.panel("hydrokit_tropfen", default_closed=True)
        header.label(text="Drops on the Lens", icon='CAMERA_DATA')
        if body is None:
            return

        if context.scene.camera is None:
            body.label(text="No camera in the scene", icon='INFO')
            return

        da = bpy.data.objects.get(camera_ops.OBJEKT) is not None
        spray = flip_bridge.get_whitewater_objects(context).get("spray")

        info = body.column(align=True)
        info.scale_y = 0.8
        info.label(text=f"Camera: {context.scene.camera.name}")
        if spray is None:
            info.label(text="No spray — bake with whitewater")
        else:
            info.label(text="Strength comes from the simulation:")
            info.label(text="drops appear when the camera")
            info.label(text="really stands in the spray.")

        body.separator()
        body.prop(props, "tropfen_radius")
        body.prop(props, "tropfen_staerke")
        body.prop(props, "tropfen_traegheit")
        body.prop(props, "tropfen_abstand")

        body.separator()
        zeile = body.row()
        zeile.scale_y = 1.3
        zeile.operator("hydrokit.create_lens_drops",
                       text="Rebuild" if da else "Set Up", icon='CHECKMARK')
        if da:
            z2 = body.row()
            z2.scale_y = 1.2
            z2.enabled = spray is not None
            z2.operator("hydrokit.bake_lens_drops", icon='DRIVER')
            body.operator("hydrokit.remove_lens_drops", icon='TRASH')


    # ------------------------------------------------------------ Optimisation
    def _draw_optimierung(self, layout, context, props):
        header, body = layout.panel("hydrokit_opt", default_closed=True)
        header.label(text="Compute Cost", icon='TIME')
        if body is None:
            return

        body.prop(props, "qualitaet", text="")
        titel, info_text, werte = optimize_ops.STUFEN[props.qualitaet]
        ziel = next((w for p_, w in werte if p_ == "simulation.resolution"), None)

        info = body.column(align=True)
        info.scale_y = 0.78
        for zeile in _umbrechen(info_text, 44):
            info.label(text=zeile)

        d = flip_bridge.get_domain_props(context)
        if d is not None and ziel:
            jetzt = d.simulation.resolution
            info.separator()
            info.label(text=f"Resolution {jetzt}  ->  {ziel}")
            schaetzung = optimize_ops.kosten_schaetzen(context, ziel)
            if schaetzung:
                voxel, gb, faktor = schaetzung
                info.label(text="Voxels: {:,}".format(voxel).replace(",", ","))
                info.label(text=f"Memory: about {gb:.1f} GB")
                if abs(faktor - 1.0) > 0.05:
                    richtung = "slower" if faktor > 1 else "faster"
                    info.label(text=f"Compute time: ~{max(faktor, 1/faktor):.1f}x {richtung}")

        info.separator()
        info.label(text="Resolution counts CUBICALLY:")
        info.label(text="double = eight times the cost.")

        body.separator()
        z = body.row()
        z.scale_y = 1.3
        z.operator("hydrokit.apply_quality", icon='CHECKMARK')
        body.operator("hydrokit.clean_cache", icon='TRASH')


classes = (HYDROKIT_PT_main,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
