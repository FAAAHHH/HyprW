# HyprW — wet obstacles
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Where the water has touched an obstacle, its surface turns darker and gets
# a thin water film on top. Lekner & Dorf 1988 (Appl. Opt. 27:1278, "Why some
# things are darker when wet"): light under the film reaches the rough
# surface, and what the surface reflects meets the film's underside, which
# sends it back down with probability p (total internal reflection plus
# Fresnel). For a Lambertian surface p = 1 - (1 - Rbar) / n^2 = 0.475, with
# Rbar = 0.0664 the Fresnel reflectance of water (n = 1.333) for diffuse
# light, integrated here. A diffuse albedo c under the film becomes
# c (1 - p) / (1 - p c): 0.1 -> 0.054, 0.3 -> 0.18, 0.5 -> 0.34. The film's
# own reflection is the Principled coat with water's refractive index.
# Valid for rough, absorbing surfaces (stone, soil, wood), not for paper or
# cloth, which turn translucent when wet (Zhang & Voss 2006).
#
# Wet from the first frame the water mesh comes within half a simulation
# cell's neighbourhood (APPROXIMATION: up to 1.7 cells), and wet from then
# on: drying takes minutes, a bake seconds. Spray and mist do not wet.
# Measured on points spread over the obstacles' surfaces half a cell apart;
# a geometry nodes modifier subdivides coarse obstacles (a box of 8
# corners) for display and render and takes the wetness from them.
import bpy
import numpy as np

from . import cache, geometrie

ATTRIBUT = "hk_nass_ab"      # first wet frame per point, 0 = never
PUNKTE = "HK_Nass_Punkte"    # the wet sample points of all obstacles
GN_GRUPPE = "HK Wet Detail"
GN_MOD = "HK Wet"
GRUPPE = "HK Wet Surface"
KNOTEN = "HK Wet"
FILM_P = 0.475
WASSER_IOR = 1.333
FILM_RAUHEIT = 0.035         # the film as calm water (the Ripples default)
MAX_STUFE = 6                # subdivision levels for coarse obstacles


def _schluessel(zellen):
    off = np.int64(1 << 20)
    z = zellen.astype(np.int64) + off
    return (z[:, 0] << np.int64(42)) | (z[:, 1] << np.int64(21)) | z[:, 2]


def _oberflaeche(obj, depsgraph, abstand, lo, hi):
    """Points spread evenly over the obstacle's surface within lo..hi (world),
    about `abstand` apart. Wetness is measured on them, not on the vertices:
    a box of 8 corners would otherwise stay dry between them."""
    eval_obj = obj.evaluated_get(depsgraph)
    me = eval_obj.to_mesh()
    try:
        me.calc_loop_triangles()
        co = np.empty(len(me.vertices) * 3, np.float32)
        me.vertices.foreach_get("co", co)
        tri = np.empty(len(me.loop_triangles) * 3, np.int32)
        me.loop_triangles.foreach_get("vertices", tri)
    finally:
        eval_obj.to_mesh_clear()
    m = np.array(obj.matrix_world, np.float64)
    co = co.reshape(-1, 3).astype(np.float64) @ m[:3, :3].T + m[:3, 3]
    t = tri.reshape(-1, 3)
    if len(t) == 0:
        return np.zeros((0, 3))
    a, b, c = co[t[:, 0]], co[t[:, 1]], co[t[:, 2]]
    innen = (np.all(np.maximum(np.maximum(a, b), c) >= lo, axis=1)
             & np.all(np.minimum(np.minimum(a, b), c) <= hi, axis=1))
    a, b, c = a[innen], b[innen], c[innen]
    flaeche = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
    anzahl = np.maximum(np.ceil(flaeche / (abstand * abstand)), 1).astype(np.int64)
    welche = np.repeat(np.arange(len(a)), anzahl)
    zufall = np.random.default_rng(0)
    r1 = np.sqrt(zufall.random(len(welche)))
    r2 = zufall.random(len(welche))
    return ((1.0 - r1)[:, None] * a[welche] + (r1 * (1.0 - r2))[:, None] * b[welche]
            + (r1 * r2)[:, None] * c[welche])


def _eingang_setzen(mod, kennung, wert):
    """A geometry nodes modifier input: Blender 5.3 reaches it through
    properties.inputs, older versions as an ID property."""
    eingaenge = getattr(getattr(mod, "properties", None), "inputs", None)
    if eingaenge is not None and hasattr(eingaenge, kennung):
        getattr(eingaenge, kennung).value = wert
    else:
        mod[kennung] = wert


def _stufe(obj, dx):
    """Subdivision level that brings the obstacle's median edge to about a cell."""
    me = obj.data
    if not len(me.edges):
        return 0
    co = np.empty(len(me.vertices) * 3, np.float32)
    me.vertices.foreach_get("co", co)
    kanten = np.empty(len(me.edges) * 2, np.int32)
    me.edges.foreach_get("vertices", kanten)
    co = co.reshape(-1, 3) * np.asarray(obj.matrix_world.to_scale(), np.float32)
    k = kanten.reshape(-1, 2)
    kante = float(np.median(np.linalg.norm(co[k[:, 0]] - co[k[:, 1]], axis=1)))
    if kante <= dx:
        return 0
    return int(min(MAX_STUFE, np.ceil(np.log2(kante / dx))))


def berechnen(scene):
    """Measures where the water touched the obstacles and from which frame,
    keeps the wet points in PUNKTE and gives every obstacle the modifier that
    hands them to its surface. {object name: wet points near it}."""
    domain = geometrie.finde_domain(scene)
    if domain is None:
        return {}
    pfad = cache.pfad_aufloesen(domain.hydrokit.cache_pfad)
    info = cache.info_lesen(pfad) or {}
    dx = info.get("dx")
    if not dx:
        return {}
    versatz = np.asarray(info.get("versatz", (0.0, 0.0, 0.0)), np.float64)
    groesse = np.asarray(info.get("groesse", (0.0, 0.0, 0.0)), np.float64)
    zelle = 0.5 * dx
    frames = sorted(cache.gebackene_frames(pfad))
    hindernisse = [o for o in (bpy.data.objects.get(n) for n in info.get("hindernisse", []))
                   if o is not None and o.type == 'MESH']
    depsgraph = bpy.context.evaluated_depsgraph_get()
    lo, hi = versatz - 2.0 * dx, versatz + groesse + 2.0 * dx
    proben = {o.name: _oberflaeche(o, depsgraph, zelle, lo, hi) for o in hindernisse}
    ab = {n: np.zeros(len(p), np.float32) for n, p in proben.items()}
    nachbarn = np.array([(a, b, c) for a in (-1, 0, 1) for b in (-1, 0, 1) for c in (-1, 0, 1)])

    for frame in frames:
        netz = cache.mesh_lesen_np(pfad, frame)
        if not netz or len(netz[0]) == 0:
            continue
        wasser = np.asarray(netz[0], np.float64).reshape(-1, 3) + versatz
        rand = 2.0 * zelle
        w_lo, w_hi = wasser.min(0) - rand, wasser.max(0) + rand
        belegt = np.unique(_schluessel(np.floor(wasser / zelle)))
        for name, pos in proben.items():
            if len(pos) == 0:
                continue
            offen = (ab[name] == 0) & np.all(pos >= w_lo, axis=1) & np.all(pos <= w_hi, axis=1)
            idx = np.flatnonzero(offen)
            if len(idx) == 0:
                continue
            basis = np.floor(pos[idx] / zelle).astype(np.int64)
            nass = np.zeros(len(idx), bool)
            for d in nachbarn:
                k = _schluessel(basis + d)
                stelle = np.searchsorted(belegt, k)
                stelle[stelle >= len(belegt)] = 0
                nass |= belegt[stelle] == k
            ab[name][idx[nass]] = float(frame)

    # the wet points of all obstacles in one point object
    alle = [(proben[n][ab[n] > 0], ab[n][ab[n] > 0]) for n in proben]
    pos = np.concatenate([p for p, _ in alle]) if alle else np.zeros((0, 3))
    werte = np.concatenate([w for _, w in alle]) if alle else np.zeros(0, np.float32)
    punkte = bpy.data.objects.get(PUNKTE)
    if punkte is None:
        punkte = bpy.data.objects.new(PUNKTE, bpy.data.meshes.new(PUNKTE))
        scene.collection.objects.link(punkte)
        punkte.hide_render = True
        punkte.hide_select = True
    me = punkte.data
    me.clear_geometry()
    me.vertices.add(len(pos))
    me.vertices.foreach_set("co", pos.astype(np.float32).ravel())
    attr = me.attributes.get("ab") or me.attributes.new("ab", 'FLOAT', 'POINT')
    attr.data.foreach_set("value", werte.astype(np.float32))
    me.update()
    punkte.matrix_world = __import__("mathutils").Matrix.Identity(4)

    ergebnis = {}
    for obj in hindernisse:
        mod = obj.modifiers.get(GN_MOD) or obj.modifiers.new(GN_MOD, 'NODES')
        mod.node_group = _gn_gruppe()
        skala = max(float(np.mean(np.abs(np.asarray(obj.matrix_world.to_scale())))), 1e-6)
        for item in mod.node_group.interface.items_tree:
            if getattr(item, "in_out", None) != 'INPUT':
                continue
            if item.name == "Level":
                _eingang_setzen(mod, item.identifier, _stufe(obj, dx))
            elif item.name == "Points":
                _eingang_setzen(mod, item.identifier, punkte)
            elif item.name == "Distance":
                _eingang_setzen(mod, item.identifier, 1.5 * zelle / skala)
        obj.update_tag()
        ergebnis[obj.name] = int((ab[obj.name] > 0).sum())
    return ergebnis


def _gn_gruppe():
    """Geometry nodes: subdivide (coarse obstacles only), take the first wet
    frame of the nearest wet point within Distance, store it as ATTRIBUT."""
    ng = bpy.data.node_groups.get(GN_GRUPPE)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(GN_GRUPPE, 'GeometryNodeTree')
    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Level", in_out='INPUT', socket_type='NodeSocketInt')
    ng.interface.new_socket("Points", in_out='INPUT', socket_type='NodeSocketObject')
    ng.interface.new_socket("Distance", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')
    n, l = ng.nodes, ng.links
    ein = n.new("NodeGroupInput"); ein.location = (-1000, 0)
    aus = n.new("NodeGroupOutput"); aus.location = (600, 0)
    teil = n.new("GeometryNodeSubdivideMesh"); teil.location = (-760, 120)
    l.new(ein.outputs["Geometry"], teil.inputs["Mesh"])
    l.new(ein.outputs["Level"], teil.inputs["Level"])
    objekt = n.new("GeometryNodeObjectInfo"); objekt.location = (-760, -200)
    objekt.transform_space = 'RELATIVE'
    l.new(ein.outputs["Points"], objekt.inputs["Object"])
    ort = n.new("GeometryNodeInputPosition"); ort.location = (-760, -420)
    nah = n.new("GeometryNodeSampleNearest"); nah.location = (-520, -260)
    l.new(objekt.outputs["Geometry"], nah.inputs["Geometry"])
    l.new(ort.outputs["Position"], nah.inputs["Sample Position"])
    name = n.new("GeometryNodeInputNamedAttribute"); name.location = (-520, -460)
    name.data_type = 'FLOAT'
    name.inputs["Name"].default_value = "ab"
    wert = n.new("GeometryNodeSampleIndex"); wert.location = (-300, -200)
    wert.data_type = 'FLOAT'; wert.domain = 'POINT'
    l.new(objekt.outputs["Geometry"], wert.inputs["Geometry"])
    l.new(name.outputs["Attribute"], wert.inputs["Value"])
    l.new(nah.outputs["Index"], wert.inputs["Index"])
    dort = n.new("GeometryNodeSampleIndex"); dort.location = (-300, -420)
    dort.data_type = 'FLOAT_VECTOR'; dort.domain = 'POINT'
    l.new(objekt.outputs["Geometry"], dort.inputs["Geometry"])
    l.new(ort.outputs["Position"], dort.inputs["Value"])
    l.new(nah.outputs["Index"], dort.inputs["Index"])
    weit = n.new("ShaderNodeVectorMath"); weit.location = (-100, -420)
    weit.operation = 'DISTANCE'
    l.new(dort.outputs["Value"], weit.inputs[0])
    l.new(ort.outputs["Position"], weit.inputs[1])
    nahe = n.new("ShaderNodeMath"); nahe.location = (80, -420)
    nahe.operation = 'LESS_THAN'
    l.new(weit.outputs["Value"], nahe.inputs[0])
    l.new(ein.outputs["Distance"], nahe.inputs[1])
    mal = n.new("ShaderNodeMath"); mal.location = (240, -260)
    mal.operation = 'MULTIPLY'
    l.new(wert.outputs["Value"], mal.inputs[0])
    l.new(nahe.outputs["Value"], mal.inputs[1])
    speichern = n.new("GeometryNodeStoreNamedAttribute"); speichern.location = (380, 60)
    speichern.data_type = 'FLOAT'; speichern.domain = 'POINT'
    speichern.inputs["Name"].default_value = ATTRIBUT
    l.new(teil.outputs["Mesh"], speichern.inputs["Geometry"])
    l.new(mal.outputs["Value"], speichern.inputs["Value"])
    l.new(speichern.outputs["Geometry"], aus.inputs["Geometry"])
    return ng


def _gruppe():
    """The shader group: Color, Coat in -> darkened Color, Coat out."""
    ng = bpy.data.node_groups.get(GRUPPE)
    if ng is not None:
        return ng
    ng = bpy.data.node_groups.new(GRUPPE, 'ShaderNodeTree')
    ng.interface.new_socket("Color", in_out='INPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Coat", in_out='INPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Color", in_out='OUTPUT', socket_type='NodeSocketColor')
    ng.interface.new_socket("Coat", in_out='OUTPUT', socket_type='NodeSocketFloat')
    ng.interface.new_socket("Wet", in_out='OUTPUT', socket_type='NodeSocketFloat')
    n, l = ng.nodes, ng.links
    ein = n.new("NodeGroupInput"); ein.location = (-900, 0)
    aus = n.new("NodeGroupOutput"); aus.location = (700, 0)

    # wet = (first wet frame > 0) and (frame >= first wet frame)
    attr = n.new("ShaderNodeAttribute"); attr.location = (-900, 300)
    attr.attribute_type = 'GEOMETRY'
    attr.attribute_name = ATTRIBUT
    bild = n.new("ShaderNodeValue"); bild.location = (-900, 450); bild.label = "Frame"
    treiber = bild.outputs[0].driver_add("default_value").driver
    treiber.expression = "frame"
    je = n.new("ShaderNodeMath"); je.location = (-680, 360); je.operation = 'GREATER_THAN'
    je.inputs[1].default_value = 0.5
    l.new(attr.outputs["Fac"], je.inputs[0])
    seit = n.new("ShaderNodeMath"); seit.location = (-680, 500); seit.operation = 'SUBTRACT'
    l.new(bild.outputs[0], seit.inputs[0]); l.new(attr.outputs["Fac"], seit.inputs[1])
    schon = n.new("ShaderNodeMath"); schon.location = (-500, 500); schon.operation = 'GREATER_THAN'
    schon.inputs[1].default_value = -0.5
    l.new(seit.outputs[0], schon.inputs[0])
    nass = n.new("ShaderNodeMath"); nass.location = (-320, 420); nass.operation = 'MULTIPLY'
    l.new(je.outputs[0], nass.inputs[0]); l.new(schon.outputs[0], nass.inputs[1])

    # Lekner & Dorf: c (1 - p) / (1 - p c), per channel
    zaehler = n.new("ShaderNodeVectorMath"); zaehler.location = (-680, 0); zaehler.operation = 'SCALE'
    zaehler.inputs["Scale"].default_value = 1.0 - FILM_P
    l.new(ein.outputs["Color"], zaehler.inputs[0])
    nenner = n.new("ShaderNodeVectorMath"); nenner.location = (-680, -200)
    nenner.operation = 'MULTIPLY_ADD'
    nenner.inputs[1].default_value = (-FILM_P, -FILM_P, -FILM_P)
    nenner.inputs[2].default_value = (1.0, 1.0, 1.0)
    l.new(ein.outputs["Color"], nenner.inputs[0])
    teilen = n.new("ShaderNodeVectorMath"); teilen.location = (-460, -80); teilen.operation = 'DIVIDE'
    l.new(zaehler.outputs["Vector"], teilen.inputs[0]); l.new(nenner.outputs["Vector"], teilen.inputs[1])
    mischen = n.new("ShaderNodeMix"); mischen.location = (200, 0)
    mischen.data_type = 'RGBA'
    l.new(nass.outputs[0], mischen.inputs["Factor"])
    l.new(ein.outputs["Color"], mischen.inputs["A"])
    l.new(teilen.outputs["Vector"], mischen.inputs["B"])
    l.new(mischen.outputs["Result"], aus.inputs["Color"])
    film = n.new("ShaderNodeMath"); film.location = (200, -220); film.operation = 'MAXIMUM'
    l.new(ein.outputs["Coat"], film.inputs[0]); l.new(nass.outputs[0], film.inputs[1])
    l.new(film.outputs[0], aus.inputs["Coat"])
    l.new(nass.outputs[0], aus.inputs["Wet"])
    return ng


def _materialien(scene):
    domain = geometrie.finde_domain(scene)
    if domain is None:
        return []
    mats = []
    for obj in geometrie.sammle_objekte(scene)['OBSTACLE']:
        for slot in getattr(obj, "material_slots", []):
            if slot.material is not None and slot.material.use_nodes and slot.material not in mats:
                mats.append(slot.material)
    return mats


def einbauen(scene):
    """Puts the group in front of every Principled BSDF of the obstacles'
    materials. Number of BSDFs changed."""
    gruppe = _gruppe()
    anzahl = 0
    for mat in _materialien(scene):
        nt = mat.node_tree
        for bsdf in [k for k in nt.nodes if k.type == 'BSDF_PRINCIPLED']:
            farbe = bsdf.inputs["Base Color"]
            if any(l.from_node.name.startswith(KNOTEN) for l in farbe.links):
                continue
            g = nt.nodes.new("ShaderNodeGroup"); g.node_tree = gruppe
            g.name = g.label = KNOTEN
            g.location = (bsdf.location.x - 220, bsdf.location.y + 120)
            if farbe.links:
                nt.links.new(farbe.links[0].from_socket, g.inputs["Color"])
            else:
                g.inputs["Color"].default_value = farbe.default_value
            nt.links.new(g.outputs["Color"], farbe)
            coat = bsdf.inputs.get("Coat Weight")
            if coat is not None and not coat.links:
                g["hk_alt_coat"] = coat.default_value
                g["hk_alt_ior"] = bsdf.inputs["Coat IOR"].default_value
                g["hk_alt_rauheit"] = bsdf.inputs["Coat Roughness"].default_value
                g.inputs["Coat"].default_value = coat.default_value
                nt.links.new(g.outputs["Coat"], coat)
                bsdf.inputs["Coat IOR"].default_value = WASSER_IOR
                bsdf.inputs["Coat Roughness"].default_value = FILM_RAUHEIT
            anzahl += 1
    return anzahl


def ausbauen(scene):
    """Takes the group out again and restores the coat."""
    anzahl = 0
    for mat in _materialien(scene):
        nt = mat.node_tree
        for g in [k for k in nt.nodes if k.type == 'GROUP' and k.name.startswith(KNOTEN)]:
            quelle = g.inputs["Color"].links[0].from_socket if g.inputs["Color"].links else None
            for link in list(g.outputs["Color"].links):
                ziel = link.to_socket
                if quelle is not None:
                    nt.links.new(quelle, ziel)
                else:
                    ziel.default_value = g.inputs["Color"].default_value
            for link in list(g.outputs["Coat"].links):
                bsdf = link.to_node
                link.to_socket.default_value = g.get("hk_alt_coat", 0.0)
                if "hk_alt_ior" in g:
                    bsdf.inputs["Coat IOR"].default_value = g["hk_alt_ior"]
                    bsdf.inputs["Coat Roughness"].default_value = g["hk_alt_rauheit"]
            nt.nodes.remove(g)
            anzahl += 1
    return anzahl


def _aufraeumen(scene):
    """Removes the wet modifiers and the point object."""
    for obj in scene.objects:
        mod = obj.modifiers.get(GN_MOD) if hasattr(obj, "modifiers") else None
        if mod is not None:
            obj.modifiers.remove(mod)
    punkte = bpy.data.objects.get(PUNKTE)
    if punkte is not None:
        me = punkte.data
        bpy.data.objects.remove(punkte)
        if me.users == 0:
            bpy.data.meshes.remove(me)


def schalten(scene, an):
    """Wet Obstacles on: compute the wet frames and wet the materials;
    off: dry materials again (the attribute stays, it costs nothing)."""
    if not an:
        _aufraeumen(scene)
        return ausbauen(scene)
    berechnen(scene)
    return einbauen(scene)
