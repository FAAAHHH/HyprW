# HyprW — show the simulation grid in the viewport
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Draws the simulation cells onto the faces of the domains, so you can see
# how finely water and mist are resolved. A pure viewport overlay via the
# gpu module: changes no objects, does not appear in the render.
#
# Switch on and off: two buttons on the right of the viewport header
# ("Domains", "Grid") or in the HyprW panel.
#
#   Domain (yellow)         water: one cell = voxel size
#   Spray domain (blue)     outline only — spray are single drops, no grid
#   Mist domain (white)     mist volume: the same voxel size as the water
#
# The coloured outlines are always visible, the cell grid only with the switch.

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Vector

from ..sim import geometrie

_handler = None
_zwischenspeicher = {}      # key -> line points; grid rebuilt only on change

# Outline (strong) and grid (faint) per role
FARBEN = {
    'DOMAIN':       ((1.0, 0.85, 0.1, 1.0), (1.0, 0.85, 0.1, 0.3)),
    'SPRAY_DOMAIN': ((0.2, 0.55, 1.0, 1.0), (0.2, 0.55, 1.0, 0.25)),
    'MIST_DOMAIN':  ((1.0, 1.0, 1.0, 1.0),  (1.0, 1.0, 1.0, 0.10)),
}


# Faces as (axis, side): axis 0/1/2 = x/y/z, side 0 = min, 1 = max
FLAECHEN = [(a, seite) for a in range(3) for seite in (0, 1)]


def _linien_auf_flaeche(mn, mx, dx, achse, seite):
    """Grid lines with spacing dx on one face of the box."""
    def reihe(a0, a1):
        n = max(int(round((a1 - a0) / dx)), 1)
        return [a0 + (a1 - a0) * t / n for t in range(n + 1)]
    lo, hi = list(mn), list(mx)
    fest = (lo if seite == 0 else hi)[achse]
    u, v = [i for i in range(3) if i != achse]
    pkt = []
    for wu in reihe(lo[u], hi[u]):
        a = [0.0] * 3; b = [0.0] * 3
        a[achse] = b[achse] = fest
        a[u] = b[u] = wu
        a[v], b[v] = lo[v], hi[v]
        pkt += [tuple(a), tuple(b)]
    for wv in reihe(lo[v], hi[v]):
        a = [0.0] * 3; b = [0.0] * 3
        a[achse] = b[achse] = fest
        a[v] = b[v] = wv
        a[u], b[u] = lo[u], hi[u]
        pkt += [tuple(a), tuple(b)]
    return pkt


def _flaechen_waehlen(mn, mx, rv3d, modus):
    """Which of the six faces get the grid. VORNE: those facing the viewer,
    HINTEN: those facing away, ALLE: all."""
    if modus == 'ALLE':
        return list(FLAECHEN)
    sicht = rv3d.view_rotation @ Vector((0.0, 0.0, -1.0))
    auge = rv3d.view_matrix.inverted().translation
    mitte = (Vector(mn) + Vector(mx)) * 0.5
    ergebnis = []
    for achse, seite in FLAECHEN:
        n = Vector((0.0, 0.0, 0.0)); n[achse] = 1.0 if seite else -1.0
        zentrum = mitte.copy(); zentrum[achse] = (mx if seite else mn)[achse]
        richtung = sicht if rv3d.view_perspective == 'ORTHO' else (zentrum - auge)
        weg = n.dot(richtung) > 0.0
        if weg == (modus == 'HINTEN'):
            ergebnis.append((achse, seite))
    return ergebnis


def _umriss(mn, mx):
    e = [Vector((x, y, z)) for x in (mn.x, mx.x) for y in (mn.y, mx.y) for z in (mn.z, mx.z)]
    kanten = [(0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7),
              (0, 4), (1, 5), (2, 6), (3, 7)]
    return [tuple(e[i]) for k in kanten for i in k]


def _zeichnen():
    scene = bpy.context.scene
    if scene is None:
        return
    mit_gitter = scene.hydrokit.gitter_anzeigen
    if not (scene.hydrokit.domains_anzeigen or mit_gitter):
        return
    domain = geometrie.finde_domain(scene)
    if domain is None:
        return
    masse = geometrie.gitter_masse(domain, domain.hydrokit.aufloesung)
    if not masse:
        return
    dx = masse[3]
    teile = []
    mn, gr = geometrie.domain_bereich(domain)
    teile.append(('DOMAIN', mn, mn + gr, dx))
    objekte = geometrie.sammle_objekte(scene)
    for rolle in ('MIST_DOMAIN', 'SPRAY_DOMAIN'):
        for obj in objekte.get(rolle, []):
            o_mn, o_gr = geometrie.domain_bereich(obj)
            teile.append((rolle, o_mn, o_mn + o_gr, dx))

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.line_width_set(2.0)
    for rolle, a, b, d in teile:
        umriss_farbe, gitter_farbe = FARBEN[rolle]
        if scene.hydrokit.domains_anzeigen:
            batch = batch_for_shader(shader, 'LINES', {"pos": _umriss(a, b)})
            shader.uniform_float("color", umriss_farbe)
            batch.draw(shader)
        if not mit_gitter or rolle == 'SPRAY_DOMAIN':
            continue
        rv3d = bpy.context.region_data
        if rv3d is None:
            continue
        gpu.state.line_width_set(1.0)
        shader.uniform_float("color", gitter_farbe)
        for achse, seite in _flaechen_waehlen(a, b, rv3d, scene.hydrokit.gitter_seiten):
            schluessel = (rolle, tuple(round(v, 4) for v in (*a, *b)), round(d, 6), achse, seite)
            batch = _zwischenspeicher.get(schluessel)
            if batch is None:
                if len(_zwischenspeicher) > 64:
                    _zwischenspeicher.clear()
                batch = batch_for_shader(shader, 'LINES',
                                         {"pos": _linien_auf_flaeche(a, b, d, achse, seite)})
                _zwischenspeicher[schluessel] = batch
            batch.draw(shader)
        gpu.state.line_width_set(2.0)
    gpu.state.line_width_set(1.0)
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('NONE')


def _neu_zeichnen(self, context):
    for fenster in context.window_manager.windows:
        for bereich in fenster.screen.areas:
            if bereich.type == 'VIEW_3D':
                bereich.tag_redraw()


def _kopfleiste(self, context):
    """Two buttons in the viewport header — only if there is a domain."""
    scene = context.scene
    if scene is None or geometrie.finde_domain(scene) is None:
        return
    zeile = self.layout.row(align=True)
    zeile.prop(scene.hydrokit, "domains_anzeigen", text="Domains", icon='MESH_CUBE')
    zeile.prop(scene.hydrokit, "gitter_anzeigen", text="Grid", icon='MESH_GRID')


# The draw handler is recorded outside the module. Reloading the add-on
# creates a new module object; a handler remembered only in the module would
# then be left behind and keep drawing without a way to switch it off
# (happened on 28.10.2026: "Domains" button had no effect).
_SCHLUESSEL = "hydrokit_gitter_handler"


def _alte_entfernen():
    alt = bpy.app.driver_namespace.pop(_SCHLUESSEL, None)
    if alt is not None:
        try:
            bpy.types.SpaceView3D.draw_handler_remove(alt, 'WINDOW')
        except (ValueError, RuntimeError):
            pass
    try:
        for fn in list(bpy.types.VIEW3D_HT_header._dyn_ui_initialize()):
            if getattr(fn, "__name__", "") == "_kopfleiste" and "hydrokit" in getattr(fn, "__module__", ""):
                bpy.types.VIEW3D_HT_header.remove(fn)
    except AttributeError:
        pass


def register():
    global _handler
    _alte_entfernen()
    _handler = bpy.types.SpaceView3D.draw_handler_add(_zeichnen, (), 'WINDOW', 'POST_VIEW')
    bpy.app.driver_namespace[_SCHLUESSEL] = _handler
    bpy.types.VIEW3D_HT_header.append(_kopfleiste)


def unregister():
    global _handler
    _alte_entfernen()          # removes the recorded handler and the buttons
    _handler = None
    _zwischenspeicher.clear()
