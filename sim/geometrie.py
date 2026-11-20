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


# The engine multiplies grid sizes in 32-bit integers (Array3d: i*j*k in an
# int), so a larger grid overflows instead of failing cleanly. Its largest
# grids have one point more per side than the cells.
GITTER_MAX_ZELLEN = 2**31 - 1


def gitter_zu_gross(masse):
    return (masse[0] + 1) * (masse[1] + 1) * (masse[2] + 1) > GITTER_MAX_ZELLEN


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


def als_trianglemesh(obj, versatz, depsgraph=None, mindestdicke=0.0, bereich=None):
    """Blender object -> TriangleMesh in engine coordinates.

    versatz is the lower corner of the domain; it is subtracted so the grid
    starts at (0,0,0). Modifiers are included.

    mindestdicke > 0 (metres): an OPEN mesh — a plane, a terrain sheet, any
    surface with boundary edges — becomes a closed body; the engine needs
    one: measured, water fell straight through a single-face floor, while a
    0.3 m slab held it. A sheet that faces up and ends outside the grid or
    below its floor (a terrain) is closed downwards (_schliessen_nach_unten);
    any other is given that thickness behind its faces (opposite to the
    normals). Either way its surface stays where it was placed.
    tm.hk_verdickt says whether one of them happened.

    bereich = (size of the grid in metres, margin): an open mesh keeps only
    the faces that come within the margin of the grid (_zuschneiden).
    """
    import numpy as np

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
    # Split quads and n-gons into triangles
    bmesh.ops.triangulate(bm, faces=bm.faces[:])
    bm.to_mesh(me)
    bm.free()

    # In float32 like v.co - versatz before, so closed meshes stay bit-identical
    ecken = np.empty(len(me.vertices) * 3, dtype=np.float32)
    me.vertices.foreach_get("co", ecken)
    ecken = ecken.reshape(-1, 3) - np.array(versatz[:], dtype=np.float32)
    dreiecke = np.empty(len(me.loops), dtype=np.int32)
    me.loops.foreach_get("vertex_index", dreiecke)
    dreiecke = dreiecke.reshape(-1, 3)
    ausgewertet.to_mesh_clear()

    verdickt = False
    if mindestdicke > 0.0 and len(dreiecke) and _ist_offen(dreiecke):
        # Faces that only share positions (split edges, flat-shaded or glTF
        # imports) belong together. Such a body stays welded even when it
        # turns out closed: the engine splits a mesh into islands by shared
        # vertices and tests each island leaving the grid on its own, so the
        # six loose faces of a split 6 cm slab were six open sheets to it and
        # all the water fell through (measured).
        ecken, dreiecke = _verschweissen(ecken, dreiecke)
        if len(dreiecke) == 0:
            return None                 # only faces without area (scaled to zero)
        if _ist_offen(dreiecke):
            zu = None
            if bereich is not None:
                ecken, dreiecke = _zuschneiden(ecken, dreiecke, *bereich)
                if len(dreiecke) == 0:
                    return None             # nowhere near the domain
            dreiecke = _wicklung_angleichen(ecken, dreiecke)
            if bereich is not None:
                zu = _schliessen_nach_unten(ecken, dreiecke, *bereich)
            ecken, dreiecke = zu if zu is not None else _verdicken(ecken, dreiecke, mindestdicke)
            verdickt = True

    tm = TriangleMesh()
    tm.vertices = array.array('f', ecken.astype(np.float32).tobytes())
    tm.triangles = array.array('i', dreiecke.astype(np.int32).tobytes())
    tm.hk_verdickt = verdickt
    return tm


def _kanten(dreiecke):
    """All edges of the triangles in their direction (a -> b), and per edge
    how many triangles share it."""
    import numpy as np
    kanten = np.concatenate((dreiecke[:, [0, 1]], dreiecke[:, [1, 2]], dreiecke[:, [2, 0]]))
    k = kanten.astype(np.int64)
    schluessel = np.minimum(k[:, 0], k[:, 1]) * (int(k.max()) + 1) + np.maximum(k[:, 0], k[:, 1])
    _, wo, zahl = np.unique(schluessel, return_inverse=True, return_counts=True)
    return kanten, zahl[wo]


def _ist_offen(dreiecke):
    """An edge with an odd number of faces: a plane, a sheet, a hole, a fin
    on a floor. The engine counts ray hits per column and skips a column
    with an odd count, so only those edges leave a body open to it; four
    faces on an edge (two closed bodies touching) are closed."""
    return bool((_kanten(dreiecke)[1] % 2 == 1).any())


def _verschweissen(ecken, dreiecke):
    """Vertices at exactly the same position joined; faces that collapse by
    that (zero area anyway) dropped, and copies of a face. A floor
    duplicated in place (Shift+D cancelled, an import with doubled faces)
    was otherwise welded into a double sheet whose edges all have 2 or 4
    faces, 'closed' but without volume: the engine's ray parity found no
    solid cell in it and the water fell through (measured, 2 m domain at
    Resolution 32, 24 frames: 11,366 of 29,146 surface vertices under the
    floor; with the copy dropped, none). A copy the same way round always
    goes. One the other way round (a double-sided sheet) only where the pair
    alone holds an edge, a fold at the sheet's rim; without a fold the pair
    is the wall between two closed bodies touching, and both stay. A copy
    turned round is only found on the same triangles: a flat floor of square
    quads splits its copy along the other diagonal and still falls through
    (measured: 11,302 under the floor; as triangles, none)."""
    import numpy as np
    ecken_v, neu = np.unique(ecken, axis=0, return_inverse=True)
    d = neu.reshape(-1)[dreiecke]
    a, b, c = d[:, 0], d[:, 1], d[:, 2]
    d = d[(a != b) & (b != c) & (c != a)]
    if len(d) == 0:
        return ecken_v, d.astype(np.int32)
    # Per face its corners sorted and which way round it runs them: equal
    # rows are copies the same way round, of which the first stays
    a, b, c = d[:, 0], d[:, 1], d[:, 2]
    rund = ((a < b) & (b < c)) | ((b < c) & (c < a)) | ((c < a) & (a < b))
    s = np.sort(d, axis=1).astype(np.int64)
    m = len(ecken_v)
    if m < 1 << 20:
        zeilen, erste = np.unique(((s[:, 0] * m + s[:, 1]) * m + s[:, 2]) * 2 + rund,
                                  return_index=True)
        paar = (zeilen[1:] >> 1) == (zeilen[:-1] >> 1)
    else:
        zeilen, erste = np.unique(np.column_stack((s, rund)), axis=0, return_index=True)
        paar = (zeilen[1:, :3] == zeilen[:-1, :3]).all(axis=1)
    o = np.argsort(erste)                       # the faces kept, in their order
    d = d[erste[o]]
    if paar.any():
        # Neighbouring rows with the same corners: a pair the other way round
        im_paar = np.zeros(len(erste), bool)
        im_paar[1:] |= paar
        im_paar[:-1] |= paar
        spaeter = np.zeros(len(erste), bool)
        spaeter[1:] |= paar & (erste[1:] > erste[:-1])
        spaeter[:-1] |= paar & (erste[:-1] > erste[1:])
        im_paar, spaeter = im_paar[o], spaeter[o]
        if (im_paar & (_kanten(d)[1].reshape(3, -1) == 2).any(axis=0)).any():
            d = d[~spaeter]
    return ecken_v, d.astype(np.int32)


def _zuschneiden(ecken, dreiecke, groesse, rand):
    """Only the faces of an open sheet that come within `rand` of the grid
    (0..groesse). A terrain often reaches far beyond the domain; the engine
    meshes those parts at full size for nothing (measured: 788k vertices of
    which the domain holds about a third). Kept are whole faces whose box
    touches the widened grid. A face that reaches more than `rand` beyond it
    is cut at its planes (_schneiden): kept whole, a 2 km floor quad over a
    2 m domain made the engine grid that island at its full box (1.6 billion
    xy cells, about 77 GB) and abort with std::bad_alloc, as the thrown
    solidify vertices did."""
    import numpy as np
    lo = np.full(3, -rand, dtype=np.float32)
    hi = np.array(groesse, dtype=np.float32) + np.float32(rand)
    eck = ecken[dreiecke]
    mn, mx = eck.min(axis=1), eck.max(axis=1)
    behalten = np.all(mx >= lo, axis=1) & np.all(mn <= hi, axis=1)
    weit = behalten & (np.any(mn < lo - rand, axis=1) | np.any(mx > hi + rand, axis=1))
    if weit.any():
        punkte, stuecke = _schneiden(ecken, dreiecke[weit], lo, hi)
        ecken = np.concatenate((ecken, punkte))
        dreiecke = np.concatenate((dreiecke[behalten & ~weit], stuecke))
    else:
        dreiecke = dreiecke[behalten]
    benutzt = np.unique(dreiecke)
    neu = np.full(len(ecken), -1, dtype=np.int64)
    neu[benutzt] = np.arange(len(benutzt))
    return ecken[benutzt], neu[dreiecke].astype(np.int32)


def _schneiden(ecken, dreiecke, lo, hi):
    """Triangles cut to the box lo..hi (Sutherland-Hodgman per plane, fanned
    back into triangles, winding kept). Corners inside keep their index; a
    cut point is computed from the edge in a fixed order and shared by its
    float32 position, so both faces of an edge get the same point and the
    sheet stays connected. Only the few faces that reach far out come here."""
    import numpy as np
    n = len(ecken)
    neue = {}
    stuecke = []
    for dreieck in dreiecke:
        vieleck = [(int(e), tuple(float(x) for x in ecken[e])) for e in dreieck]
        for achse in range(3):
            for grenze, seite in ((float(lo[achse]), -1.0), (float(hi[achse]), 1.0)):
                drin = []
                for m in range(len(vieleck)):
                    ia, a = vieleck[m]
                    ib, b = vieleck[(m + 1) % len(vieleck)]
                    a_drin = seite * (a[achse] - grenze) <= 0.0
                    if a_drin:
                        drin.append((ia, a))
                    if a_drin != (seite * (b[achse] - grenze) <= 0.0):
                        p, q = (a, b) if a < b else (b, a)
                        t = (grenze - p[achse]) / (q[achse] - p[achse])
                        x = [p[c] + t * (q[c] - p[c]) for c in range(3)]
                        x[achse] = grenze
                        x = tuple(float(c) for c in np.array(x, dtype=np.float32))
                        drin.append((neue.setdefault(x, n + len(neue)), x))
                vieleck = drin
        for m in range(1, len(vieleck) - 1):
            stuecke.append((vieleck[0][0], vieleck[m][0], vieleck[m + 1][0]))
    return (np.array(list(neue), dtype=np.float32).reshape(-1, 3),
            np.array(stuecke, dtype=np.int32).reshape(-1, 3))


def _wicklung_angleichen(ecken, dreiecke):
    """Faces wound against their neighbours turned round, per connected
    piece towards the orientation that holds most of its area. The
    thickness goes behind the faces: with half a floor wound the other way
    it went up there, and the back surface crossed the front along the seam
    (measured, 2 m domain at resolution 40: 5940 of 40368 particles fell
    through within 3 frames)."""
    import numpy as np
    from collections import deque
    t = len(dreiecke)
    kanten = np.concatenate((dreiecke[:, [0, 1]], dreiecke[:, [1, 2]], dreiecke[:, [2, 0]])).astype(np.int64)
    m = int(kanten.max()) + 1
    schluessel = np.minimum(kanten[:, 0], kanten[:, 1]) * m + np.maximum(kanten[:, 0], kanten[:, 1])
    o = np.argsort(schluessel, kind='stable')
    _, anfang, zahl = np.unique(schluessel[o], return_index=True, return_counts=True)
    a, b = o[anfang[zahl == 2]], o[anfang[zahl == 2] + 1]
    vorwaerts = kanten[:, 0] < kanten[:, 1]
    gegen = vorwaerts[a] == vorwaerts[b]        # both faces run the edge the same way
    if not gegen.any():
        return dreiecke                         # the usual case: nothing to do
    von = np.concatenate((a % t, b % t))
    nach = np.concatenate((b % t, a % t))
    wechsel = np.concatenate((gegen, gegen)).astype(np.int8)
    o = np.argsort(von, kind='stable')
    nach, wechsel = nach[o].tolist(), wechsel[o].tolist()
    grenzen = np.searchsorted(von[o], np.arange(t + 1)).tolist()
    farbe = [-1] * t
    stueck = [0] * t
    for s in range(t):
        if farbe[s] >= 0:
            continue
        farbe[s] = 0
        stueck[s] = s
        schlange = deque((s,))
        while schlange:
            f = schlange.popleft()
            for e in range(grenzen[f], grenzen[f + 1]):
                g = nach[e]
                if farbe[g] < 0:
                    farbe[g] = farbe[f] ^ wechsel[e]
                    stueck[g] = s
                    schlange.append(g)
    farbe, stueck = np.array(farbe), np.array(stueck)
    p = ecken.astype(np.float64)
    flaeche = np.linalg.norm(np.cross(p[dreiecke[:, 1]] - p[dreiecke[:, 0]],
                                      p[dreiecke[:, 2]] - p[dreiecke[:, 0]]), axis=1)
    eins = np.bincount(stueck, weights=flaeche * (farbe == 1), minlength=t)
    null = np.bincount(stueck, weights=flaeche * (farbe == 0), minlength=t)
    drehen = np.where(eins[stueck] <= null[stueck], farbe == 1, farbe == 0)
    dreiecke = dreiecke.copy()
    dreiecke[drehen] = dreiecke[drehen][:, ::-1]
    return dreiecke


def _schliessen_nach_unten(ecken, dreiecke, groesse, rand):
    """Closed body from a terrain: walls straight down from the boundary of
    the sheet to below the grid, and a bottom (a fan; the engine's ray
    parity only counts how often a column crosses it). Everything under the
    surface is solid then, and under an overhang the column crosses the
    sheet three times plus the bottom: air, as it should be. No offset
    surface, so nothing folds through the front. At the cliff top of
    wasserfull.blend at Resolution 1000 the thickened sheet let 2158 of a
    dropped 32368 particles through the rock within 1.2 s (its back surface
    lay in front of 399 faces); closed downwards, none.

    Only for a sheet that faces up overall and whose boundary edges all lie
    outside the grid's footprint or below its floor (a terrain or a floor
    reaching past the domain) — a plate, a wall or a ceiling returns None and
    is thickened instead."""
    import numpy as np
    kanten, zahl = _kanten(dreiecke)
    if (zahl >= 3).any():
        return None
    rand_k = kanten[zahl == 1]
    if len(rand_k) == 0:
        return None
    a, b = ecken[rand_k[:, 0]], ecken[rand_k[:, 1]]
    mn, mx = np.minimum(a, b), np.maximum(a, b)
    ueber = ((mx[:, 0] >= 0) & (mn[:, 0] <= groesse[0]) & (mx[:, 1] >= 0)
             & (mn[:, 1] <= groesse[1]) & (mx[:, 2] >= 0))
    if ueber.any():
        return None
    p = ecken.astype(np.float64)
    hoch = np.cross(p[dreiecke[:, 1]] - p[dreiecke[:, 0]], p[dreiecke[:, 2]] - p[dreiecke[:, 0]])[:, 2].sum()
    if hoch <= 0.0:
        return None                             # faces down: a ceiling
    n = len(ecken)
    rv = np.unique(rand_k)
    z_unten = min(float(ecken[rv, 2].min()), -rand) - 1.0
    # A copy below for the boundary vertices only. Copying every vertex left
    # one unused per surface vertex (585k on the user's terrain), which the
    # engine removes one by one (vector::erase).
    unten = ecken[rv].copy()
    unten[:, 2] = z_unten
    platz = np.full(n, -1, dtype=np.int64)
    platz[rv] = n + np.arange(len(rv))
    mitte = np.array([[ecken[rv, 0].mean(), ecken[rv, 1].mean(), z_unten]], dtype=np.float32)
    ra, rb = rand_k[:, 0], rand_k[:, 1]
    ua, ub = platz[ra], platz[rb]
    wand = np.concatenate((np.stack((rb, ra, ua), axis=1),
                           np.stack((rb, ua, ub), axis=1)))
    boden = np.stack((ua, ub, np.full(len(ra), n + len(rv))), axis=1)
    return (np.concatenate((ecken, unten, mitte)).astype(np.float32),
            np.concatenate((dreiecke, wand, boden)).astype(np.int32))


def _verdicken(ecken, dreiecke, dicke):
    """Closed body from an open sheet: a copy `dicke` behind the faces, the
    copy turned inside out, and a rim of two triangles along each boundary
    edge.

    Every vertex moves exactly `dicke`, along its area-weighted normal.
    bmesh.ops.solidify divides by the cosine between face and vertex normal
    to keep the wall even; on a displaced terrain sheet that threw single
    vertices tens of kilometres away (measured: 38 of 788k beyond 500 m, one
    49 km), and the engine then gridded that whole box and aborted Blender
    with std::bad_alloc.
    """
    import numpy as np
    n = len(ecken)
    p = ecken.astype(np.float64)
    a, b, c = p[dreiecke[:, 0]], p[dreiecke[:, 1]], p[dreiecke[:, 2]]
    flaeche = np.cross(b - a, c - a)            # normal times twice the area
    normale = np.zeros((n, 3))
    for ecke in range(3):
        for achse in range(3):
            normale[:, achse] += np.bincount(dreiecke[:, ecke], weights=flaeche[:, achse],
                                             minlength=n)
    laenge = np.linalg.norm(normale, axis=1)
    # A vertex whose faces cancel out (folded, zero area) goes with the sheet
    gesamt = flaeche.sum(axis=0)
    g = np.linalg.norm(gesamt)
    ersatz = gesamt / g if g > 0.0 else np.array((0.0, 0.0, 1.0))
    ok = laenge > 1e-12
    normale[ok] /= laenge[ok, None]
    normale[~ok] = ersatz
    hinten = (p - dicke * normale).astype(np.float32)

    kanten, zahl = _kanten(dreiecke)
    # A rim along every edge with an odd number of faces: a boundary, and a
    # non-manifold edge such as a fin standing on a floor (three faces). The
    # engine skips a column with an odd number of ray hits entirely; with
    # rims only at boundaries, 6806 of 40368 particles fell through along
    # such a fin within 3 frames (measured, 2 m domain at resolution 40).
    rand = kanten[zahl % 2 == 1]                # (a, b) in the direction of its face
    ra, rb = rand[:, 0], rand[:, 1]
    saum = np.concatenate((np.stack((rb, ra, ra + n), axis=1),
                           np.stack((rb, ra + n, rb + n), axis=1)))
    return (np.concatenate((ecken, hinten)),
            np.concatenate((dreiecke, dreiecke[:, ::-1] + n, saum)).astype(np.int32))


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
