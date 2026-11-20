# HyprW — faster scrolling in the HyprW sidebar tab
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Blender scrolls panels a fixed 40 pixels per wheel notch (the deltay of
# view2d.scroll_down is ignored for the wheel) and a touchpad exactly by
# the finger movement, so the long HyprW tab takes a lot of scrolling.
# Over the HyprW tab a wheel notch scrolls several steps and a touchpad
# swipe moves that many times as far; everywhere else the events are passed
# on to Blender unchanged. The factor is in Preferences > Add-ons > HyprW.

import bpy

KATEGORIE = "HyprW"
_ADDON = __package__.rpartition(".")[0]
_eintraege = []             # (keymap, item) registered by us


class HYDROKIT_AddonPreferences(bpy.types.AddonPreferences):
    bl_idname = _ADDON

    scroll_schritte: bpy.props.IntProperty(
        name="Sidebar Scroll Speed",
        description="How many times faster the HyprW sidebar tab scrolls: wheel "
                    "steps per notch and touchpad distance per swipe (1 = Blender's "
                    "normal speed)",
        default=8, min=1, max=10)

    def draw(self, context):
        zeile = self.layout.row(align=True)
        zeile.prop(self, "scroll_schritte")
        zeile.operator("hydrokit.hilfe", text="", icon='INFO', emboss=False).thema = "scroll_schritte"


def _schritte(context):
    addon = context.preferences.addons.get(_ADDON)
    return addon.preferences.scroll_schritte if addon else 1


class HYDROKIT_OT_seitenleiste_scrollen(bpy.types.Operator):
    """Scroll the HyprW sidebar tab faster (mouse wheel and touchpad)"""
    bl_idname = "hydrokit.seitenleiste_scrollen"
    bl_label = "Scroll HyprW Sidebar"
    bl_options = {'INTERNAL'}

    hoch: bpy.props.BoolProperty(options={'SKIP_SAVE'})

    def invoke(self, context, event):
        bereich, region = context.area, context.region
        if (bereich is None or region is None or bereich.type != 'VIEW_3D'
                or region.type != 'UI'
                or getattr(region, "active_panel_category", "") != KATEGORIE):
            return {'PASS_THROUGH'}     # other editors and tabs: Blender's own scrolling
        if event.type == 'TRACKPADPAN':
            # The same delta view2d.pan takes from the event, times the factor
            dx = event.mouse_prev_x - event.mouse_x
            dy = event.mouse_prev_y - event.mouse_y
            if dx == 0 and dy == 0:
                return {'PASS_THROUGH'}
            bpy.ops.view2d.pan(deltax=dx, deltay=dy * _schritte(context))
            return {'FINISHED'}
        schritt = bpy.ops.view2d.scroll_up if self.hoch else bpy.ops.view2d.scroll_down
        for _ in range(_schritte(context)):
            schritt()
        return {'FINISHED'}


_klassen = (HYDROKIT_AddonPreferences, HYDROKIT_OT_seitenleiste_scrollen)


def register():
    for k in _klassen:
        bpy.utils.register_class(k)
    kc = bpy.context.window_manager.keyconfigs.addon
    if kc is None:                      # background mode: no key maps
        return
    km = kc.keymaps.new(name="View2D Buttons List", space_type='EMPTY', region_type='WINDOW')
    for taste, hoch in (('WHEELUPMOUSE', True), ('WHEELDOWNMOUSE', False),
                        ('TRACKPADPAN', False)):
        kmi = km.keymap_items.new(HYDROKIT_OT_seitenleiste_scrollen.bl_idname, taste,
                                  'ANY' if taste == 'TRACKPADPAN' else 'PRESS')
        kmi.properties.hoch = hoch
        _eintraege.append((km, kmi))


def unregister():
    for km, kmi in _eintraege:
        try:
            km.keymap_items.remove(kmi)
        except (ReferenceError, RuntimeError):
            pass
    _eintraege.clear()
    for k in reversed(_klassen):
        bpy.utils.unregister_class(k)
