# HyprW — geometry from Blender into the engine
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The engine computes on a grid whose origin is at (0,0,0) and which extends
# to (isize*dx, jsize*dx, ksize*dx). So Blender coordinates have to be
# shifted by the lower corner of the domain.
#
# Triangles instead of quads: the engine only knows TriangleMesh.

import array
import bpy
import bmesh
from mathutils import Vector

from ..ffengine.trianglemesh import TriangleMesh


def domain_bereich(domain):
    """Lower corner and size of the domain in world coordinates."""
    ecken = [domain.matrix_world @ Vector(c) for c in domain.bound_box]
    mn = Vector((min(c.x for c in ecken),
                 min(c.y for c in ecken),
                 min(c.z for c in ecken)))
    mx = Vector((max(c.x for c in ecken),
                 max(c.y for c in ecken),
                 max(c.z for c in ecken)))
    return mn, mx - mn


def gitter_masse(domain, aufloesung):
    """(isize, jsize, ksize, dx) from domain and resolution.

    The resolution refers to the LONGEST side — so the voxel size stays
    cube-shaped no matter how the domain is shaped.
    """
    _, groesse = domain_bereich(domain)
    laengste = max(groesse)
    if laengste <= 0:
        return None
    dx = laengste / aufloesung
    i = max(int(round(groesse.x / dx)), 1)
    j = max(int(round(groesse.y / dx)), 1)
    k = max(int(round(groesse.z / dx)), 1)
    return i, j, k, dx


def als_trianglemesh(obj, versatz, depsgraph=None, mindestdicke=0.0):
    """Blender object -> TriangleMesh in engine coordinates.

    versatz is the lower corner of the domain; it is subtracted so the grid
    starts at (0,0,0). Modifiers are included.

    mindestdicke > 0 (metres): an OPEN mesh — a plane, a terrain sheet, any
    surface with boundary edges — is given that thickness behind its faces
    (opposite to the normals), so its surface stays where it was placed. The
    engine needs closed bodies: measured, water fell straight through a
    single-face floor, while a 0.3 m slab held it. tm.hk_verdickt says
    whether that happened.
    """
    if obj.type != 'MESH':
        return None
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()

    ausgewertet = obj.evaluated_get(depsgraph)
    me = ausgewertet.to_mesh()
    if me is None or len(me.vertices) == 0:
        ausgewertet.to_mesh_clear()
        return None

    # Into world space first, so a thickness is in metres whatever the scale
    mw = obj.matrix_world
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.transform(mw)
    if mw.determinant() < 0.0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])   # mirrored: keep normals outward
    verdickt = False
    if mindestdicke > 0.0 and any(not e.is_manifold for e in bm.edges):
        bmesh.ops.solidify(bm, geom=bm.faces[:], thickness=mindestdicke)
        verdickt = True
    # Split quads and n-gons into triangles
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()

    ecken = []
    for v in me.vertices:
        p = v.co - versatz
        ecken.extend((p.x, p.y, p.z))
    indizes = []
    for p in me.polygons:
        indizes.extend(p.vertices[:3])

    ausgewertet.to_mesh_clear()

    tm = TriangleMesh()
    tm.vertices = array.array('f', ecken)
    tm.triangles = array.array('i', indizes)
    tm.hk_verdickt = verdickt
    return tm


def sammle_objekte(scene):
    """All tagged objects sorted by role."""
    gefunden = {'DOMAIN': [], 'FLUID': [], 'OBSTACLE': [],
                'INFLOW': [], 'OUTFLOW': [], 'MIST_DOMAIN': [],
                'SPRAY_DOMAIN': []}
    for obj in scene.objects:
        props = getattr(obj, "hydrokit", None)
        if props is None or props.typ == 'NONE':
            continue
        if props.typ in gefunden:
            gefunden[props.typ].append(obj)
    return gefunden


# Roles that only mark a region or a shape for the simulation. As in FLIP
# Fluids (api_workaround_utils: domain, fluid, inflow, outflow and force
# fields get all Cycles rays switched off): hide_render alone keeps them
# out of F12, but the Rendered viewport follows viewport visibility, so the
# closed domain box was rendered — from inside it everything was black.
HILFSROLLEN = ('DOMAIN', 'FLUID', 'INFLOW', 'OUTFLOW', 'MIST_DOMAIN', 'SPRAY_DOMAIN')
_STRAHLEN = ("visible_camera", "visible_diffuse", "visible_glossy",
             "visible_transmission", "visible_volume_scatter", "visible_shadow")


def strahlen_setzen(obj, sichtbar):
    """Switch all Cycles ray visibilities of the object. A running Rendered
    viewport only picks this up after a visibility change, so the object is
    hidden and shown once — FLIP Fluids' workaround as well."""
    geaendert = False
    for name in _STRAHLEN:
        try:
            if getattr(obj, name) != sichtbar:
                setattr(obj, name, sichtbar)
                geaendert = True
        except AttributeError:
            pass
    if geaendert:
        try:
            gewaehlt = obj.select_get()
            versteckt = obj.hide_get()
            obj.hide_set(not versteckt)
            obj.hide_set(versteckt)
            if gewaehlt:
                obj.select_set(True)
        except RuntimeError:
            pass            # not in the current view layer
    return geaendert


def hilfsobjekte_unsichtbar(scene):
    """Rays off for every helper object, e.g. in files from before."""
    for obj in scene.objects:
        props = getattr(obj, "hydrokit", None)
        if props is not None and props.typ in HILFSROLLEN:
            strahlen_setzen(obj, False)


def finde_domain(scene):
    objekte = sammle_objekte(scene)
    return objekte['DOMAIN'][0] if objekte['DOMAIN'] else None


def finde_nebel_domain(scene):
    objekte = sammle_objekte(scene)
    return objekte['MIST_DOMAIN'][0] if objekte['MIST_DOMAIN'] else None


def nebel_bereich(scene, domain, rolle='MIST_DOMAIN'):
    """(min, max) in world coordinates within which mist (or, with rolle=
    'SPRAY_DOMAIN', spray) may fly: domain and extra domain together.
    Without an extra domain, just the domain."""
    mn, gr = domain_bereich(domain)
    mx = mn + gr
    zusatz = sammle_objekte(scene)[rolle]
    nd = zusatz[0] if zusatz else None
    if nd is not None:
        n_mn, n_gr = domain_bereich(nd)
        n_mx = n_mn + n_gr
        mn = Vector((min(mn.x, n_mn.x), min(mn.y, n_mn.y), min(mn.z, n_mn.z)))
        mx = Vector((max(mx.x, n_mx.x), max(mx.y, n_mx.y), max(mx.z, n_mx.z)))
    return mn, mx


def windvektor(scene, faktor=1.0):
    """Summed wind from Blender's own force field objects, in m/s.

    Blender's WIND blows along the object's local +Z axis. In the engine the
    wind acts through the air drag of the whitewater — not on the water
    itself. That is not a shortcoming: wind acts on flying drops, not on a
    cubic metre of water in a pool.
    """
    gesamt = Vector((0.0, 0.0, 0.0))
    for obj in scene.objects:
        feld = getattr(obj, "field", None)
        if feld is None or feld.type not in {'WIND', 'FORCE'}:
            continue
        richtung = (obj.matrix_world.to_quaternion()
                    @ Vector((0.0, 0.0, 1.0))).normalized()
        gesamt += richtung * feld.strength
    return gesamt * faktor
