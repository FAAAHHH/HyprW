# HyprW — look of water and whitewater
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The simulation only delivers points and a surface mesh. To turn that into
# something visible it takes three things:
#
#   1. GEOMETRY on the points. Bare vertices do not render.
#   2. MATERIALS per type.
#   3. A fine enough SURFACE, otherwise the water looks faceted.
#
# How large the particles are drawn:
# Physically, spray drops are below a millimetre — in a 2 m scene that would
# be 0.04 % of the image width, i.e. invisible. So they are drawn by the
# VOXEL SIZE, not by the real drop size. The factors come from a series of
# measurements: foam clumps and looks larger, spray are single drops, mist
# is the finest.
#
# Why whitewater as particles and not as a volume: a volume shader makes
# whitewater look like smoke. Small bodies with a highlight hit it better —
# whitewater is nothing but very many tiny water drops. Only the
# MIST is a real volume.

import bpy

from . import anzeige, cache, geometrie

PREFIX = "HK "

# Version of the generated look. Raise it whenever a generated material
# changes in a way an older file must not keep. 2 (30.10.2026): foam without
# subsurface — Cycles renders subsurface on points black on the GPU, and old
# files kept that foam until Apply Look. 3 (02.11.2026): studio water (light
# through the surface, caustics, capillary ripples, dispersion). Files whose
# water or whitewater materials are older get them rebuilt when opened
# (anzeige._nach_laden).
MATERIAL_STAND = 3
STAND_KEY = "hk_material_stand"

# Density x radius — calibrated on a waterfall scene:
#   radius  8 cm, density 0.5   barely visible
#   radius  8 cm, density 2.0   right            <-- product 0.16
#   radius  8 cm, density 6.0   dark lump
# BY THE WAY, high density makes mist DARKER, not brighter: the volume
# then absorbs more than it scatters.
NEBEL_OPTISCHE_TIEFE = 0.16

# Mist: sphere radius in voxels and how many cells far it is blended.
# 1.5 voxels = 8 cm at 5.3 cm cells — exactly the calibrated point above
# (radius 8 cm, density 2.0). Blurring over 3 cells (~16 cm) lets
# neighbours flow into each other.
NEBEL_RADIUS_VOXEL = 1.5
WEICH_SCHRITTE = 3

# Type -> (display name, factor on the voxel size)
ZEICHENGROESSE = {
    "foam":   ("Foam",    0.24),
    "bubble": ("Bubbles", 0.20),
    "spray":  ("Spray",   0.14),
    "nebel":  ("Mist",    0.10),
}

# OPTICS PER TYPE — each by its own physics, not one glossy material for all:
#
#   FOAM    is water full of air bubbles. Light is scattered many times
#           inside -> milky, partly see-through (schaum_optik).
#   BUBBLES are AIR IN WATER. Light passes from a denser into a thinner medium,
#           the refractive index ratio is 1/1.333 = 0.75. With IOR 1.333 (like
#           water in air) they would be invisible or wrong; with 0.75 they show
#           the typical silvery edges from total internal reflection.
#   SPRAY   are water drops in the air: clear, IOR 1.333. Single drops
#           sparkle (smooth), clusters of drops look milky (rougher) — the
#           per-particle variation reproduces that.
#   MIST    is a volume of drops a few hundredths of a millimetre across.
#           Such drops scatter by MIE: strongly forwards, which is why spray
#           mist glows against the light. Blender computes Mie directly from
#           the drop diameter — which comes from the mist drop size.
#
# Variation per particle via the attribute "hk_zufall": the ID from the
# engine, which stays with a particle its whole life. A random value per
# point INDEX would change every frame and flicker.
ZUFALL = "hk_zufall"


def _transmission(inputs):
    for key in ("Transmission Weight", "Transmission"):
        if key in inputs:
            return key
    return None


def _material(name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    mat[STAND_KEY] = MATERIAL_STAND
    return mat


def wasser_veraltet():
    """True if the water body carries a water built by an older HyprW. A
    material of the user's own (no stand, no "HK " name) never counts: it
    was replaced by HyprW water on every open."""
    flaeche = bpy.data.objects.get(anzeige.OBERFLAECHE)
    if flaeche is None or not getattr(flaeche.data, "materials", None):
        return False
    return any(m is not None and (STAND_KEY in m or m.name.startswith(PREFIX))
               and m.get(STAND_KEY, 0) < MATERIAL_STAND for m in flaeche.data.materials)


def materialien_veraltet():
    """True if the water or whitewater materials in this file were built by
    an older HyprW (MATERIAL_STAND)."""
    if wasser_veraltet():
        return True
    mats = []
    for name, _ in ZEICHENGROESSE.values():
        m = bpy.data.materials.get(PREFIX + name)
        if m is not None and m.users > 0:
            mats.append(m)
    nebel = bpy.data.materials.get(PREFIX + "Nebel")
    if nebel is not None and nebel.users > 0:
        mats.append(nebel)
    return any(m.get(STAND_KEY, 0) < MATERIAL_STAND for m in mats)


def _zufall(nt, x=-520, y=0):
    attr = nt.nodes.new("ShaderNodeAttribute"); attr.location = (x, y)
    attr.attribute_type = 'GEOMETRY'
    attr.attribute_name = ZUFALL
    return attr.outputs["Fac"]


def _bereich(nt, wert, lo, hi, x=-320, y=0):
    """Random 0..1 -> lo..hi"""
    mr = nt.nodes.new("ShaderNodeMapRange"); mr.location = (x, y)
    mr.inputs["To Min"].default_value = lo
    mr.inputs["To Max"].default_value = hi
    nt.links.new(wert, mr.inputs["Value"])
    return mr.outputs["Result"]


MITTE = "hk_mitte"       # particle centre, stored in _gruppe_punkte


def _blasen_textur(nt, basis, z, radius, blasen):
    """Foam as a heap of bubbles: Voronoi cells around each particle centre,
    thin bright walls between them and a dome per cell (bump) that catches
    a highlight. The coordinates are relative to the particle's own centre
    plus an offset from its ID, so the pattern travels with the particle
    instead of the particles swimming through a fixed texture.
    basis: colour socket. Returns (colour, roughness, normal) sockets."""
    tk = nt.nodes.new("ShaderNodeTexCoord"); tk.location = (-1300, -300)
    mitte = nt.nodes.new("ShaderNodeAttribute"); mitte.location = (-1300, -520)
    mitte.attribute_type = 'GEOMETRY'; mitte.attribute_name = MITTE
    rel = nt.nodes.new("ShaderNodeVectorMath"); rel.location = (-1100, -360)
    rel.operation = 'SUBTRACT'
    nt.links.new(tk.outputs["Object"], rel.inputs[0])
    nt.links.new(mitte.outputs["Vector"], rel.inputs[1])
    skal = nt.nodes.new("ShaderNodeVectorMath"); skal.location = (-920, -360)
    skal.operation = 'SCALE'
    skal.inputs["Scale"].default_value = blasen / max(2.0 * radius, 1e-6)
    nt.links.new(rel.outputs["Vector"], skal.inputs[0])
    # different pattern per particle: shift by its random value
    vers = nt.nodes.new("ShaderNodeVectorMath"); vers.location = (-920, -560)
    vers.operation = 'SCALE'
    vers.inputs[0].default_value = (37.1, 17.3, 53.7)
    nt.links.new(z, vers.inputs["Scale"])
    summe = nt.nodes.new("ShaderNodeVectorMath"); summe.location = (-740, -420)
    summe.operation = 'ADD'
    nt.links.new(skal.outputs["Vector"], summe.inputs[0])
    nt.links.new(vers.outputs["Vector"], summe.inputs[1])

    zellen = nt.nodes.new("ShaderNodeTexVoronoi"); zellen.location = (-540, -300)
    zellen.feature = 'F1'
    zellen.inputs["Scale"].default_value = 1.0
    rand = nt.nodes.new("ShaderNodeTexVoronoi"); rand.location = (-540, -560)
    rand.feature = 'DISTANCE_TO_EDGE'
    rand.inputs["Scale"].default_value = 1.0
    # Foam is never one bubble size: a second, finer network of small
    # bubbles over the first (one size everywhere looked like honeycomb)
    fein = nt.nodes.new("ShaderNodeTexVoronoi"); fein.location = (-540, -800)
    fein.feature = 'DISTANCE_TO_EDGE'
    fein.inputs["Scale"].default_value = 2.3
    for v in (zellen, rand, fein):
        nt.links.new(summe.outputs["Vector"], v.inputs["Vector"])
    feinw = nt.nodes.new("ShaderNodeMath"); feinw.location = (-360, -800)
    feinw.operation = 'MULTIPLY'; feinw.inputs[1].default_value = 1.6   # finer walls thinner
    nt.links.new(fein.outputs["Distance"], feinw.inputs[0])
    naechste = nt.nodes.new("ShaderNodeMath"); naechste.location = (-360, -620)
    naechste.operation = 'MINIMUM'
    nt.links.new(rand.outputs["Distance"], naechste.inputs[0])
    nt.links.new(feinw.outputs["Value"], naechste.inputs[1])

    # Walls: 1 at the cell edge, 0 inside the bubble — thin liquid films
    wand = nt.nodes.new("ShaderNodeMapRange"); wand.location = (-200, -620)
    wand.inputs["From Min"].default_value = 0.0
    wand.inputs["From Max"].default_value = 0.055
    wand.inputs["To Min"].default_value = 1.0
    wand.inputs["To Max"].default_value = 0.0
    nt.links.new(naechste.outputs["Value"], wand.inputs["Value"])

    # Colour: bright walls, the bubble insides a little darker
    farbe = nt.nodes.new("ShaderNodeMix"); farbe.location = (-160, -120)
    farbe.data_type = 'RGBA'
    # The mix node has A/B for float, vector and colour under the same
    # names — the colour pair is 6/7, its output 2
    # walls at the particle's own foam brightness (not pure white: that
    # alone was brighter than any measured foam)
    nt.links.new(wand.outputs["Result"], farbe.inputs[0])
    innen = nt.nodes.new("ShaderNodeMix"); innen.location = (-340, -120)
    innen.data_type = 'RGBA'; innen.blend_type = 'MULTIPLY'
    innen.inputs[0].default_value = 1.0
    innen.inputs[7].default_value = (0.92, 0.92, 0.92, 1.0)   # neutral, slightly darker
    nt.links.new(basis, innen.inputs[6])
    nt.links.new(basis, farbe.inputs[7])
    nt.links.new(innen.outputs[2], farbe.inputs[6])

    # Glossy bubble films inside, matte walls
    rau = nt.nodes.new("ShaderNodeMapRange"); rau.location = (-160, -360)
    rau.inputs["To Min"].default_value = 0.08
    rau.inputs["To Max"].default_value = 0.5
    nt.links.new(wand.outputs["Result"], rau.inputs["Value"])

    # Dome per bubble: 1 - d^2 is round at the top and steep at the wall, so
    # every bubble catches its own small highlight
    quadrat = nt.nodes.new("ShaderNodeMath"); quadrat.location = (-360, -300)
    quadrat.operation = 'POWER'; quadrat.inputs[1].default_value = 2.0
    nt.links.new(zellen.outputs["Distance"], quadrat.inputs[0])
    kuppel = nt.nodes.new("ShaderNodeMath"); kuppel.location = (-200, -300)
    kuppel.operation = 'SUBTRACT'
    kuppel.inputs[0].default_value = 1.0
    nt.links.new(quadrat.outputs["Value"], kuppel.inputs[1])
    bump = nt.nodes.new("ShaderNodeBump"); bump.location = (-160, -580)
    bump.inputs["Strength"].default_value = 1.0
    bump.inputs["Distance"].default_value = 2.0 * radius / max(blasen, 1)
    nt.links.new(kuppel.outputs["Value"], bump.inputs["Height"])
    return farbe.outputs[2], rau.outputs["Result"], bump.outputs["Normal"]


# FOAM AS AERATED WATER: clear water full of air bubbles. Light enters and is
# scattered at the bubbles — translucent and milky, not an opaque white ball.
#   bubble radius   ~1 mm, the Hinze scale under breaking waves
#                   (Deane & Stokes 2002, Nature 418:839)
#   air fraction    up to 24 % in breaking waves (Lamarre & Melville 1992,
#                   IEEE J. Oceanic Eng. 17:204); default 20 %, adjustable
#   scattering      large bubbles scatter by geometric optics, extinction
#                   efficiency Q = 2 (van de Hulst 1957): sigma = 1.5 phi / a
#                   = 300 /m at 20 % — light travels ~3.3 mm between bubbles
#   anisotropy      g = 0.85 for air bubbles in water (Zhang et al. 2002,
#                   "Remote sensing of bubble clouds in seawater")
#   absorption      pure water, Pope & Fry 1997 (Appl. Opt. 36:8710):
#                   0.34 /m at 650 nm, 0.0565 at 550, 0.0092 at 450 — only
#                   the water share (1 - air fraction) absorbs
#
# NOT AS SUBSURFACE: Cycles cannot do subsurface scattering on point clouds
# on the GPU. The random walk has to find where it leaves the particle, and
# on the GPU those rays only hit triangles: the walk never gets out, the
# light is lost and the foam renders black (measured: the same material was
# milky on the CPU, black with OptiX, and milky on mesh spheres with OptiX).
# So the way through one particle is solved here in closed form and the
# shader evaluates it — the same on CPU and GPU, and cheaper than a walk.
#
# Cycles draws a point as a sphere with front faces only, so one hit stands
# for the whole particle. A ray crosses it along the chord d = 2 r cos(theta)
# (geometry: r from Point Info, theta between ray and normal), and the
# particle counts as a layer that thick, facing the ray:
#   straight through  exp(-(sigma_a + sigma_s / 2) d). Of the extinction of
#                     large particles (Q = 2) exactly half is diffraction,
#                     into a cone of ~lambda / 2a = 0.02 degrees for 1 mm
#                     bubbles (van de Hulst 1957) — far below a pixel, so it
#                     stays in the image. Only the other half (reflection and
#                     refraction at the bubbles) blurs it. That half has
#                     g = 2 * 0.85 - 1 = 0.7, so the total keeps g = 0.85.
#   scattered         Kubelka-Munk layer (Kubelka 1948, JOSA 38:448):
#                     R = sinh(bSd) / (a sinh(bSd) + b cosh(bSd)),
#                     T = b / (same), a = 1 + K/S, b = sqrt(a^2 - 1), with
#                     K = 2 sigma_a, S = 3/4 sigma_s (1 - g) - 1/4 sigma_a
#                     (van Gemert & Star 1987, Lasers Life Sci. 1:287);
#                     reflected and diffusely transmitted light leave the
#                     layer as from a matte sheet facing the ray
#   APPROXIMATION     the diffuse share of the transmission is T minus the
#                     straight share (exact for a beam: the four-flux model,
#                     Maheu et al. 1984, Appl. Opt. 23:3353), and a flat
#                     layer stands in for the curved particle
# Checked against Cycles path-tracing the same medium as a volume in mesh
# spheres (sun and sky, checker backdrop): mean brightness of a particle
# within 1-4 % at 6 mm radius, up to 9 % at 2 mm; at 20 mm 4-11 % darker,
# see _schaum_shader. At 20 % air a particle 13 mm across, through its
# centre: 15 % passes straight, 30 % is reflected, 55 % leaves diffusely at
# the back. Towards the rim the chord shrinks and nearly everything passes —
# milky, see-through edges. Red is absorbed most: a faint cyan where foam
# piles up.
SCHAUM_BLASENRADIUS = 1.0e-3
SCHAUM_G = 0.85
WASSER_ABSORPTION = (0.34, 0.0565, 0.0092)       # /m, R G B


def schaum_optik(luftanteil):
    """Coefficients of aerated water with this air fraction (0..1), per
    channel R G B in 1/m: "gerade" (extinction of the straight beam) and the
    Kubelka-Munk "K", "S", "a", "b"."""
    import math
    phi = min(max(luftanteil, 0.01), 0.6)        # the slider's range
    sigma_s = 1.5 * phi / SCHAUM_BLASENRADIUS
    g = SCHAUM_G
    werte = {"gerade": [], "K": [], "S": [], "a": [], "b": []}
    for wasser in WASSER_ABSORPTION:
        sigma_a = (1.0 - phi) * wasser
        K = 2.0 * sigma_a
        S = 0.75 * sigma_s * (1.0 - g) - 0.25 * sigma_a
        a = 1.0 + K / S
        werte["gerade"].append(sigma_a + 0.5 * sigma_s)      # diffraction stays straight
        werte["K"].append(K)
        werte["S"].append(S)
        werte["a"].append(a)
        werte["b"].append(math.sqrt(a * a - 1.0))
    return {k: tuple(v) for k, v in werte.items()}


def schaum_schicht(optik, d):
    """(reflected, diffusely through, straight through) per channel for a
    chord of d metres — the same formulas the shader evaluates."""
    import math
    zurueck, diffus, gerade = [], [], []
    for ext, S, a, b in zip(optik["gerade"], optik["S"], optik["a"], optik["b"]):
        x = b * S * d
        nenner = a * math.sinh(x) + b * math.cosh(x)
        durch = math.exp(-ext * d)
        zurueck.append(math.sinh(x) / nenner)
        diffus.append(max(b / nenner - durch, 0.0))
        gerade.append(durch)
    return tuple(zurueck), tuple(diffus), tuple(gerade)


def _knoten_rechnen(nt, art, op, werte, ort):
    """Math or Vector Math node; werte are sockets or constants, in order."""
    n = nt.nodes.new(art); n.location = ort
    n.operation = op
    for k, w in enumerate(werte):
        if isinstance(w, bpy.types.NodeSocket):
            nt.links.new(w, n.inputs[k])
        else:
            n.inputs[k].default_value = w
    return n.outputs[1 if op == 'DOT_PRODUCT' else 0]


def _zahl(nt, op, *werte, ort=(0, 0)):
    return _knoten_rechnen(nt, "ShaderNodeMath", op, werte, ort)


def _vektor(nt, op, *werte, ort=(0, 0)):
    return _knoten_rechnen(nt, "ShaderNodeVectorMath", op, werte, ort)


def _schaum_streuung(nt, optik, radius):
    """Nodes for the way of light through one foam particle (see above).
    Returns sockets: "basis" reflection tint, "durchschein" colour of the
    diffuse transmission, "klar" colour of the straight share, "anteil" of
    the diffuse transmission in the scattered part, "koerper" = 1 - straight
    share, "blick" = view direction, "rueckseite" = backfacing."""
    import math
    # the least absorbed channel carries the most light; the mix factors are
    # set on it so no colour exceeds 1 (energy is conserved)
    ref = min(range(3), key=lambda k: WASSER_ABSORPTION[k])
    eins = (1.0, 1.0, 1.0)
    x = -2500
    info = nt.nodes.new("ShaderNodePointInfo"); info.location = (x, 900)
    # a mesh has no point radius: then the drawn radius
    ist_punkt = _zahl(nt, 'GREATER_THAN', info.outputs["Radius"], 0.0, ort=(x + 200, 1000))
    abw = _zahl(nt, 'SUBTRACT', info.outputs["Radius"], radius, ort=(x + 200, 840))
    r = _zahl(nt, 'MULTIPLY_ADD', ist_punkt, abw, radius, ort=(x + 400, 920))
    geo = nt.nodes.new("ShaderNodeNewGeometry"); geo.location = (x, 640)
    cos = _vektor(nt, 'DOT_PRODUCT', geo.outputs["True Normal"], geo.outputs["Incoming"],
                  ort=(x + 200, 640))
    cos = _zahl(nt, 'MAXIMUM', cos, 0.0, ort=(x + 400, 700))
    # half chord r cos(theta); the factor 2 of the chord is in the constants
    h = _zahl(nt, 'MULTIPLY', r, cos, ort=(x + 600, 800))

    e = (math.e,) * 3
    a, b, S = optik["a"], optik["b"], optik["S"]
    bsd = _vektor(nt, 'MULTIPLY', h, tuple(2.0 * bk * sk for bk, sk in zip(b, S)),
                  ort=(x + 800, 1000))
    ex = _vektor(nt, 'POWER', e, bsd, ort=(x + 1000, 1000))
    ex_inv = _vektor(nt, 'DIVIDE', eins, ex, ort=(x + 1200, 1100))
    sinh2 = _vektor(nt, 'SUBTRACT', ex, ex_inv, ort=(x + 1400, 1100))       # 2 sinh
    cosh2 = _vektor(nt, 'ADD', ex, ex_inv, ort=(x + 1400, 920))             # 2 cosh
    nenner = _vektor(nt, 'MULTIPLY_ADD', sinh2, a,
                     _vektor(nt, 'MULTIPLY', cosh2, b, ort=(x + 1600, 920)),
                     ort=(x + 1800, 1000))
    zurueck = _vektor(nt, 'DIVIDE', sinh2, nenner, ort=(x + 2000, 1100))   # R
    durch = _vektor(nt, 'DIVIDE', tuple(2.0 * bk for bk in b), nenner,
                    ort=(x + 2000, 920))                                    # T
    gerade = _vektor(nt, 'POWER', e,
                     _vektor(nt, 'MULTIPLY', h, tuple(-2.0 * k for k in optik["gerade"]),
                             ort=(x + 800, 700)),
                     ort=(x + 1000, 700))
    diffus = _vektor(nt, 'MAXIMUM', _vektor(nt, 'SUBTRACT', durch, gerade, ort=(x + 2200, 820)),
                     (0.0, 0.0, 0.0), ort=(x + 2400, 820))

    def kanal(v, ort):
        teile = nt.nodes.new("ShaderNodeSeparateXYZ"); teile.location = ort
        nt.links.new(v, teile.inputs[0])
        return teile.outputs[ref]
    gerade_ref = kanal(gerade, (x + 1200, 640))
    zurueck_ref = kanal(zurueck, (x + 2200, 1180))
    koerper = _zahl(nt, 'SUBTRACT', 1.0, gerade_ref, ort=(x + 1400, 640))
    anteil = _zahl(nt, 'SUBTRACT', 1.0,
                   _zahl(nt, 'DIVIDE', zurueck_ref,
                         _zahl(nt, 'MAXIMUM', koerper, 1e-6, ort=(x + 1600, 640)),
                         ort=(x + 2400, 1180)),
                   ort=(x + 2600, 1180))
    anteil.node.use_clamp = True
    basis = _vektor(nt, 'MINIMUM',
                    _vektor(nt, 'DIVIDE', zurueck,
                            _zahl(nt, 'MAXIMUM', zurueck_ref, 1e-6, ort=(x + 2400, 1340)),
                            ort=(x + 2600, 1340)),
                    eins, ort=(x + 2800, 1340))
    rest = _zahl(nt, 'MAXIMUM', _zahl(nt, 'SUBTRACT', koerper, zurueck_ref, ort=(x + 2400, 640)),
                 1e-6, ort=(x + 2600, 640))
    durchschein = _vektor(nt, 'MINIMUM', _vektor(nt, 'DIVIDE', diffus, rest, ort=(x + 2600, 820)),
                          eins, ort=(x + 2800, 820))
    klar = _vektor(nt, 'MINIMUM',
                   _vektor(nt, 'DIVIDE', gerade,
                           _zahl(nt, 'MAXIMUM', gerade_ref, 1e-6, ort=(x + 1400, 480)),
                           ort=(x + 1600, 480)),
                   eins, ort=(x + 1800, 480))
    return {"basis": basis, "durchschein": durchschein, "klar": klar, "anteil": anteil,
            "koerper": koerper, "blick": geo.outputs["Incoming"],
            "rueckseite": geo.outputs["Backfacing"]}


def _schaum_shader(nt, z, radius, blasen, luftanteil):
    """A foam particle: the straight, reflected and diffusely transmitted
    light of schaum_optik, under the highlight of the bubble films.
    Returns the shader socket."""
    weg = _schaum_streuung(nt, schaum_optik(luftanteil), radius)
    # Per particle -10 %..0 on the reflection, an APPROXIMATION for the
    # varying air content
    streu = _bereich(nt, z, 0.9, 1.0)
    farbe = _vektor(nt, 'MULTIPLY', weg["basis"], streu, ort=(-320, 160))
    rauheit, normal = 0.15, None                    # a water surface
    if blasen > 0:
        farbe, rauheit, normal = _blasen_textur(nt, farbe, z, radius, blasen)

    def bsdf(typ, ort, farbe=None, normal=None):
        n = nt.nodes.new(typ); n.location = ort
        if farbe is not None:
            nt.links.new(farbe, n.inputs["Color"])
        if normal is not None:
            nt.links.new(normal, n.inputs["Normal"])
        return n

    def mischen(anteil, a, b, ort):
        m = nt.nodes.new("ShaderNodeMixShader"); m.location = ort
        nt.links.new(anteil, m.inputs[0])
        nt.links.new(a, m.inputs[1])
        nt.links.new(b, m.inputs[2])
        return m.outputs["Shader"]

    # The layer faces the ray, so its matte lobes take the view direction as
    # their normal: a particle glows evenly like the path-traced volume,
    # instead of being shaded like a hard ball (on the sphere normal it came
    # out 3-17 % too bright). Limit: Cycles drops light from below the
    # sphere's own surface for a lobe whose normal differs from it
    # (bump_shadowing_term, kernel/closure/bsdf.h) — a large particle lit
    # from the side gets a darker crescent there.
    zurueck = bsdf("ShaderNodeBsdfDiffuse", (200, 660), farbe, weg["blick"])
    durch = bsdf("ShaderNodeBsdfTranslucent", (200, 480), weg["durchschein"], weg["blick"])
    gestreut = mischen(weg["anteil"], zurueck.outputs["BSDF"], durch.outputs["BSDF"], (400, 580))
    klar = bsdf("ShaderNodeBsdfTransparent", (400, 780), weg["klar"])
    teilchen = mischen(weg["koerper"], klar.outputs["BSDF"], gestreut, (600, 620))

    # The bubble films on top are water surfaces and reflect by Fresnel
    # (IOR 1.333). Mixed by the Fresnel factor, what they reflect is missing
    # below — energy is conserved.
    fresnel = nt.nodes.new("ShaderNodeFresnel"); fresnel.location = (600, 320)
    fresnel.inputs["IOR"].default_value = 1.333
    glanz = bsdf("ShaderNodeBsdfGlossy", (600, 120), normal=normal)
    film = bsdf("ShaderNodeBsdfGlossy", (800, 120), normal=normal) if blasen > 0 else None
    for g in (glanz, film):
        if g is not None:
            try:
                g.distribution = 'MULTI_GGX'        # keeps its energy when rough
            except TypeError:
                pass
    if normal is not None:
        nt.links.new(normal, fresnel.inputs["Normal"])
        nt.links.new(rauheit, glanz.inputs["Roughness"])
    else:
        glanz.inputs["Roughness"].default_value = rauheit
    oben = mischen(fresnel.outputs["Fac"], teilchen, glanz.outputs["BSDF"], (800, 420))
    if film is not None:
        # the liquid film of every bubble: a sharp highlight per bubble
        film.inputs["Roughness"].default_value = 0.03
        oben = mischen(fresnel.outputs["Fac"], oben, film.outputs["BSDF"], (1000, 420))
    # The back faces of a mesh (Cycles points have none) let everything
    # through, or a particle would count twice
    hinten = bsdf("ShaderNodeBsdfTransparent", (1000, 660))
    return mischen(weg["rueckseite"], oben, hinten.outputs["BSDF"], (1200, 420))


def baue_gischt_material(art, radius=0.01, blasen=0, spray_farbe=(1.0, 1.0, 1.0),
                         luftanteil=0.2):
    """blasen: bubbles across a foam particle (0 = no texture);
    spray_farbe: tint of the spray drops; luftanteil: air fraction of foam."""
    mat = _material(PREFIX + ZEICHENGROESSE[art][0])
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (340, 0)
    z = _zufall(nt)
    if art == "foam":
        # Aerated water, see schaum_optik
        nt.links.new(_schaum_shader(nt, z, radius, blasen, luftanteil), out.inputs["Surface"])
        out.location = (1400, 420)
        if hasattr(mat, "use_transparent_shadow"):
            mat.use_transparent_shadow = True           # EEVEE: shadows see the straight part
        return mat
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (0, 0)
    i = bsdf.inputs
    tr = _transmission(i)
    if art == "bubble":
        i["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        i["Roughness"].default_value = 0.0
        i["IOR"].default_value = 1.0 / 1.333        # air in water
        if tr:
            i[tr].default_value = 1.0
    else:   # spray
        i["Base Color"].default_value = (*spray_farbe, 1.0)
        i["IOR"].default_value = 1.333
        # A drop is clear water with a smooth surface: fully transmissive.
        # The milky look of dense spray comes from light scattered between
        # many drops, which the renderer does by itself — a white diffuse
        # share per drop (0.15 before) made every drop a white ball.
        nt.links.new(_bereich(nt, z, 0.0, 0.08), i["Roughness"])
        if tr:
            i[tr].default_value = 1.0
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def baue_nebel_material(dichte=2.0, durchmesser_mm=0.03, radius=0.2):
    """Mist is the only thing that really is a volume — Mie scattering."""
    mat = _material(PREFIX + "Nebel")
    nt = mat.node_tree
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (260, 0)
    vol = nt.nodes.new("ShaderNodeVolumeScatter"); vol.location = (-60, 0)
    vol.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    vol.phase = 'MIE'
    # Blender's Mie approximation holds for 5 to 50 micrometres
    vol.inputs["Diameter"].default_value = min(max(durchmesser_mm * 1000.0, 5.0), 50.0)
    # Density as before with Principled Volume: grid value "density" x slider
    attr = nt.nodes.new("ShaderNodeAttribute"); attr.location = (-660, 0)
    attr.attribute_type = 'GEOMETRY'; attr.attribute_name = "density"
    mul = nt.nodes.new("ShaderNodeMath"); mul.location = (-460, 0)
    mul.operation = 'MULTIPLY'; mul.inputs[1].default_value = dichte
    nt.links.new(attr.outputs["Fac"], mul.inputs[0])
    # BREAKING UP: otherwise every mist particle becomes a sharply bounded
    # sphere of equal density — single cotton balls in the render. Noise in
    # space (not per particle, or it would jump while scrubbing) turns that
    # into wisps. Mapped to 0..2, so ~1 on average: the calibrated optical
    # depth is preserved.
    rausch = nt.nodes.new("ShaderNodeTexNoise"); rausch.location = (-660, -220)
    # Wisps about twice the size of a mist particle — the engine resolution
    # does not break it up any finer anyway
    rausch.inputs["Scale"].default_value = 1.0 / max(2.0 * radius, 1e-3)
    rausch.inputs["Detail"].default_value = 6.0
    rausch.inputs["Roughness"].default_value = 0.6
    form = nt.nodes.new("ShaderNodeMapRange"); form.location = (-460, -220)
    form.inputs["From Min"].default_value = 0.35
    form.inputs["From Max"].default_value = 0.65
    form.inputs["To Min"].default_value = 0.0
    form.inputs["To Max"].default_value = 2.0
    nt.links.new(rausch.outputs["Fac"], form.inputs["Value"])
    mul2 = nt.nodes.new("ShaderNodeMath"); mul2.location = (-260, 0)
    mul2.operation = 'MULTIPLY'
    nt.links.new(mul.outputs["Value"], mul2.inputs[0])
    nt.links.new(form.outputs["Result"], mul2.inputs[1])
    nt.links.new(mul2.outputs["Value"], vol.inputs["Density"])
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def _gruppe_punkte(name, radius, material):
    """Points -> small bodies. For foam, bubbles and spray."""
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.nodes.clear(); ng.links.clear()
    vorhanden = {(i.name, i.in_out) for i in ng.interface.items_tree
                 if i.item_type == 'SOCKET'}
    if ("Geometry", 'INPUT') not in vorhanden:
        ng.interface.new_socket("Geometry", in_out='INPUT',
                                socket_type='NodeSocketGeometry')
    if ("Geometry", 'OUTPUT') not in vorhanden:
        ng.interface.new_socket("Geometry", in_out='OUTPUT',
                                socket_type='NodeSocketGeometry')
    n = ng.nodes
    gi = n.new("NodeGroupInput");  gi.location = (-560, 0)
    # POINT CLOUD instead of instanced spheres. Cycles and EEVEE draw points
    # natively as spheres and accept materials. The first version put an
    # icosphere with 42 vertices on every particle — with 25,000 foam
    # particles over a million vertices, and Blender stuttered badly.
    # Each point's own centre for textures that travel with it (foam bubbles)
    pos = n.new("GeometryNodeInputPosition"); pos.location = (-560, -160)
    mitte = n.new("GeometryNodeStoreNamedAttribute"); mitte.location = (-460, 0)
    mitte.data_type = 'FLOAT_VECTOR'; mitte.domain = 'POINT'
    mitte.inputs["Name"].default_value = MITTE
    ng.links.new(gi.outputs[0], mitte.inputs["Geometry"])
    ng.links.new(pos.outputs["Position"], mitte.inputs["Value"])
    m2p = n.new("GeometryNodeMeshToPoints"); m2p.location = (-300, 0)
    ng.links.new(mitte.outputs["Geometry"], m2p.inputs["Mesh"])
    # Radius per point: drawn size x the spread per type (anzeige.py,
    # "hk_groesse"); without the attribute (old caches) factor 1
    groesse = n.new("GeometryNodeInputNamedAttribute"); groesse.location = (-760, -320)
    groesse.data_type = 'FLOAT'
    groesse.inputs["Name"].default_value = "hk_groesse"
    faktor = n.new("GeometryNodeSwitch"); faktor.location = (-580, -320)
    faktor.input_type = 'FLOAT'
    faktor.inputs["False"].default_value = 1.0
    ng.links.new(groesse.outputs["Exists"], faktor.inputs["Switch"])
    ng.links.new(groesse.outputs["Attribute"], faktor.inputs["True"])
    r_mal = n.new("ShaderNodeMath"); r_mal.location = (-440, -320)
    r_mal.operation = 'MULTIPLY'; r_mal.inputs[1].default_value = radius
    ng.links.new(faktor.outputs["Output"], r_mal.inputs[0])
    ng.links.new(r_mal.outputs["Value"], m2p.inputs["Radius"])
    setm = n.new("GeometryNodeSetMaterial"); setm.location = (-120, 0)
    setm.inputs["Material"].default_value = material
    ng.links.new(m2p.outputs["Points"], setm.inputs["Geometry"])
    out = n.new("NodeGroupOutput"); out.location = (300, 0)
    ng.links.new(setm.outputs["Geometry"], out.inputs[0])
    return ng


def _gruppe_nebel(name, radius, dichte, material, voxel):
    """Points -> volume. Only for mist."""
    ng = bpy.data.node_groups.get(name)
    if ng is None:
        ng = bpy.data.node_groups.new(name, "GeometryNodeTree")
    ng.nodes.clear(); ng.links.clear()
    vorhanden = {(i.name, i.in_out) for i in ng.interface.items_tree
                 if i.item_type == 'SOCKET'}
    if ("Geometry", 'INPUT') not in vorhanden:
        ng.interface.new_socket("Geometry", in_out='INPUT',
                                socket_type='NodeSocketGeometry')
    if ("Geometry", 'OUTPUT') not in vorhanden:
        ng.interface.new_socket("Geometry", in_out='OUTPUT',
                                socket_type='NodeSocketGeometry')
    n = ng.nodes
    gi = n.new("NodeGroupInput"); gi.location = (-460, 0)
    m2p = n.new("GeometryNodeMeshToPoints"); m2p.location = (-280, 0)
    p2v = n.new("GeometryNodePointsToVolume"); p2v.location = (-80, 0)
    p2v.inputs["Density"].default_value = dichte
    p2v.inputs["Radius"].default_value = radius
    # FIXED cell size = water resolution. The default "64 cells across the
    # whole cloud" was coarse and changed with the cloud size every frame.
    p2v.inputs["Resolution Mode"].default_value = 'Size'
    p2v.inputs["Voxel Size"].default_value = voxel
    # JOIN LIKE WATER: small spheres alone give single balls. So the density
    # field is first dilated by a few cells (otherwise the blur cannot reach
    # beyond the spheres) and then blurred. Nearby spheres flow into each
    # other, the edge fades out softly. A mean filter only redistributes —
    # the total amount stays the same.
    holen = n.new("GeometryNodeGetNamedGrid"); holen.location = (120, 0)
    holen.inputs["Name"].default_value = "density"
    holen.inputs["Remove"].default_value = True
    weiten = n.new("GeometryNodeGridDilateAndErode"); weiten.location = (320, -120)
    weiten.inputs["Steps"].default_value = WEICH_SCHRITTE
    glatt = n.new("GeometryNodeGridMean"); glatt.location = (520, -120)
    glatt.inputs["Width"].default_value = 1
    glatt.inputs["Iterations"].default_value = WEICH_SCHRITTE
    ablegen = n.new("GeometryNodeStoreNamedGrid"); ablegen.location = (720, 0)
    ablegen.inputs["Name"].default_value = "density"
    setm = n.new("GeometryNodeSetMaterial"); setm.location = (920, 0)
    setm.inputs["Material"].default_value = material
    out = n.new("NodeGroupOutput"); out.location = (1120, 0)
    ng.links.new(gi.outputs[0], m2p.inputs["Mesh"])
    ng.links.new(m2p.outputs["Points"], p2v.inputs["Points"])
    ng.links.new(p2v.outputs["Volume"], holen.inputs["Volume"])
    ng.links.new(holen.outputs["Grid"], weiten.inputs["Grid"])
    ng.links.new(weiten.outputs["Grid"], glatt.inputs["Grid"])
    ng.links.new(holen.outputs["Volume"], ablegen.inputs["Volume"])
    ng.links.new(glatt.outputs["Grid"], ablegen.inputs["Grid"])
    ng.links.new(ablegen.outputs["Volume"], setm.inputs["Geometry"])
    ng.links.new(setm.outputs["Geometry"], out.inputs[0])
    return ng


def _eevee_volumen(scene, domain):
    """EEVEE draws volumes in tiles and depth slices. The defaults
    (8-pixel tiles, slices up to 100 m) make mist coarse in the viewport
    when the scene is only a few metres deep. Only COARSER values are
    adjusted — finer settings by the user are kept.

    Not finer than 4-pixel tiles: the volume buffers grow with the square of
    1/tile. Measured on a 1574 x 1034 viewport with 128 slices (RTX A2000,
    4 GB): 2-pixel tiles took 1.7 GB of GPU memory, 4-pixel 0.6 GB. With
    2-pixel tiles the Material Preview left Cycles too little memory, and
    every F12 render came out black ("out of GPU memory", 29.10.2026)."""
    e = scene.eevee
    try:
        if int(e.volumetric_tile_size) > 4:
            e.volumetric_tile_size = '4'
    except (AttributeError, TypeError, ValueError):
        pass
    cam = scene.camera
    if cam is not None and hasattr(e, "volumetric_end"):
        from mathutils import Vector
        mn, mx = geometrie.nebel_bereich(scene, domain)
        ecken = [Vector((x, y, z)) for x in (mn.x, mx.x)
                 for y in (mn.y, mx.y) for z in (mn.z, mx.z)]
        auge = cam.matrix_world.translation
        noetig = max((c - auge).length for c in ecken) + 0.5
        # too short cuts off mist, far too long wastes depth slices
        if e.volumetric_end < noetig or e.volumetric_end > 2.0 * noetig:
            e.volumetric_end = noetig
    if getattr(e, "volumetric_samples", 128) < 128:
        e.volumetric_samples = 128


def _modifier_setzen(obj, ng):
    mod = next((m for m in obj.modifiers if m.type == 'NODES'), None)
    if mod is None:
        mod = obj.modifiers.new("HK Aussehen", 'NODES')
    mod.node_group = ng
    return mod


def voxelgroesse(context):
    domain = geometrie.finde_domain(context.scene)
    if domain is None:
        return None
    masse = geometrie.gitter_masse(domain, domain.hydrokit.aufloesung)
    return masse[3] if masse else None


# On the domain: the cell size the whitewater look was sized for
LOOK_DX = "hk_look_dx"
GISCHT_OBJEKTE = tuple(a[1] for a in anzeige.ARTEN.values())


def zellgroesse(context, info=None):
    """Cell size of the baked frames, else of the current settings. The drawn
    whitewater has to fit the bake on display: with a changed Resolution
    and no new bake yet, the cached particles still come from the old
    cells. info: the cache's info.json if already read."""
    domain = geometrie.finde_domain(context.scene)
    if domain is None:
        return None
    if info is None:
        info = cache.info_lesen(cache.pfad_aufloesen(domain.hydrokit.cache_pfad)) or {}
    return info.get("dx") or voxelgroesse(context)


def look_stand(context, info=None):
    """(state, text) of the look, for the panel and after a bake. State None:
    nothing baked; 'fehlt': whitewater without its geometry (renders black)
    or water without a material; 'veraltet': whitewater sized for other
    cells; 'ok'. info: the cache's info.json if already read ({} = nothing
    baked, e.g. after Clear Cache: the emptied objects stay)."""
    flaeche = bpy.data.objects.get(anzeige.OBERFLAECHE)
    domain = geometrie.finde_domain(context.scene)
    if info is None and domain is not None:
        info = cache.info_lesen(cache.pfad_aufloesen(domain.hydrokit.cache_pfad)) or {}
    if flaeche is None or not info:
        return None, "Nothing baked yet"
    # Whitewater only if the bake on display has some: leftovers of an
    # earlier bake are emptied, not styled (anzeige._gischt_leeren)
    gischt = ([o for o in map(bpy.data.objects.get, GISCHT_OBJEKTE) if o is not None]
              if info.get("gischt") else [])
    if any(not any(m.type == 'NODES' for m in o.modifiers) for o in gischt):
        return 'fehlt', "Whitewater has no look yet — renders black"
    if not any(m is not None for m in flaeche.data.materials):
        return 'fehlt', "The water has no material yet"
    alt = domain.get(LOOK_DX) if domain is not None else None
    jetzt = zellgroesse(context, info)
    if gischt and alt and jetzt and abs(alt - jetzt) > 0.01 * jetzt:
        return 'veraltet', (f"Whitewater sized for {alt * 100:.2f} cm cells, "
                            f"now {jetzt * 100:.2f} cm")
    return 'ok', "Look applied"


def nach_bake(context):
    """After a bake: new whitewater gets its look, whitewater from other
    cells a new size. A water chosen with Apply Water stays as it is; only a
    surface without any material gets one. True if something was applied."""
    scene = context.scene
    domain = geometrie.finde_domain(scene)
    if domain is None:
        return False
    info = cache.info_lesen(cache.pfad_aufloesen(domain.hydrokit.cache_pfad)) or {}
    # gischt_laden only creates the types present on the loaded frame; the
    # others appeared later without a look and rendered black
    if info.get("gischt"):
        for _, name, _ in anzeige.ARTEN.values():
            if name != "HK_Nebel" or info.get("nebel"):
                anzeige._objekt_holen(name, scene)
    stand, _ = look_stand(context, info)
    # A look from before LOOK_DX was sized for unknown cells: size it once
    if stand == 'ok' and info.get("gischt") and domain.get(LOOK_DX) is None:
        stand = 'veraltet'
    if stand in (None, 'ok'):
        return False
    flaeche = bpy.data.objects.get(anzeige.OBERFLAECHE)
    ohne = not any(m is not None for m in flaeche.data.materials)
    return bool(anwenden(context, wasser=ohne))


def wasser_anwenden(context, wasser_variante=None):
    """The water body alone gets its material from the water settings;
    whitewater keeps its look. Name of the material, None without water."""
    flaeche = bpy.data.objects.get(anzeige.OBERFLAECHE)
    if flaeche is None:
        return None
    from ..operators import materials_ops as mo
    scene = context.scene
    mat = mo.wasser_aus_einstellungen(scene, wasser_variante)
    mo.volumen_strahlen(scene)
    flaeche.data.materials.clear()
    flaeche.data.materials.append(mat)
    # smooth faceted water
    for p in flaeche.data.polygons:
        p.use_smooth = True
    return mat.name


def anwenden(context, wasser_variante=None, wasser=True):
    """Put materials and geometry on everything that is baked. wasser=False
    leaves the water body as it is (whitewater sliders)."""
    scene = context.scene
    domain = geometrie.finde_domain(scene)
    if domain is None:
        return {}
    voxel = zellgroesse(context)
    if voxel:
        domain[LOOK_DX] = voxel         # look_stand: sized for these cells
    voxel = voxel or 0.03
    props = domain.hydrokit
    erledigt = {}

    # --- Water ----------------------------------------------------------
    if wasser:
        name = wasser_anwenden(context, wasser_variante)
        if name is not None:
            erledigt["Water"] = name

    # --- Whitewater -----------------------------------------------------
    for art, (_, faktor) in ZEICHENGROESSE.items():
        objname = next((o for a, (o, _) in _ARTEN_NAMEN.items() if a == art), None)
        obj = bpy.data.objects.get(objname) if objname else None
        if obj is None:
            continue
        radius = voxel * faktor * scene.hydrokit.whitewater_scale_multiplier
        if art == "nebel":
            # Radius large enough for the drops to merge into a cloud,
            # density INVERSELY to it. Calibrated: the product of both should
            # come to about 0.16. Fixed values look right at one scene size
            # and wrong at any other — too dense looks like cotton wool,
            # too thin like nothing at all.
            # Small spheres, joined by blurring in _gruppe_nebel.
            # Before: 8 voxels looked coarse, 4 voxels like single balls.
            radius_nebel = voxel * NEBEL_RADIUS_VOXEL
            dichte = NEBEL_OPTISCHE_TIEFE / max(radius_nebel, 1e-4)
            dichte *= props.nebel_dichte_anzeige / 2.0   # slider as a factor
            mat = baue_nebel_material(dichte, props.nebel_groesse, radius_nebel)
            ng = _gruppe_nebel("HK Nebel", radius_nebel, dichte, mat, voxel)
            _eevee_volumen(scene, domain)
            radius = radius_nebel       # the report shows the real mist sphere
        else:
            mat = baue_gischt_material(art, radius, props.schaum_blasen,
                                       tuple(props.spray_farbe),
                                       props.schaum_luftanteil / 100.0)
            ng = _gruppe_punkte("HK " + ZEICHENGROESSE[art][0], radius, mat)
        _modifier_setzen(obj, ng)
        erledigt[ZEICHENGROESSE[art][0]] = f"{radius*100:.2f} cm"
    return erledigt


# Type -> object name, derived from anzeige.ARTEN
_ARTEN_NAMEN = {art: (name, menge) for _, (art, name, menge) in
                anzeige.ARTEN.items()}
