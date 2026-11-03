# HyprW — the water material
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The shaders are built in code instead of shipped as a .blend:
# - everything is in the source, traceable and versionable
# - no binary files in the add-on, clean for a GPL release
# - adapts to Blender versions (Transmission is named differently in 4.x than in 3.x)

import bpy

PREFIX = "HK "

# WATER FROM MEASURED OPTICS, not from calibrated colours.
#
# Two things decide how clear water looks, and both are measured in nature:
#
#   VISIBILITY   how far a black object can be seen under water. It gives
#                the beam attenuation c = absorption + scattering:
#                visibility = 4.8 / c (Davies-Colley 1988, Limnol. Oceanogr.
#                33:616; Zaneveld & Pegau 2003, Opt. Express 11:2997: within
#                10 % over coastal and inland waters). 96 rivers at low flow
#                ranged from 0.25 to 10.75 m (Davies-Colley & Close 1990,
#                NZ J. Mar. Freshw. Res. 24:357).
#   DISSOLVED    yellow-brown organic matter (CDOM). Absorption at 440 nm in
#   MATTER       mountain waters 0.063-0.35 /m (EGU 2020-10101, CDOM along
#                extreme environmental gradients); towards red it falls as
#                exp(-S (lambda - 440)), S = 0.017 /nm (0.016-0.018 measured
#                at a river inflow, Lake Balaton).
#
# The water itself absorbs as in Pope & Fry 1997 (sim/aussehen.py,
# WASSER_ABSORPTION; channels at 650 / 550 / 450 nm). Whatever of c is not
# absorbed at 550 nm is scattering by suspended particles. Those are much
# larger than the wavelength, so they scatter all colours alike (van de
# Hulst 1957), strongly forwards: g = 0.924, the average particle phase
# function of natural waters (Petzold 1972, after Mobley, Ocean Optics
# Web Book).
#
# No factor for the scene size: thin water stays clear and deep water gets
# its colour by itself, as in nature — the path length does it.
WELLENLAENGEN = (650.0, 550.0, 450.0)       # nm, the R G B channels
CDOM_STEIGUNG = 0.017                        # /nm
TEILCHEN_G = 0.924

# Variant -> (material name, visibility m, dissolved matter a440 /m). Points
# inside the measured ranges above; OZEAN lies outside the river data
# (APPROXIMATION), and algae pigments are not modelled (ALGEN is only the
# dissolved matter and the murkiness of such water).
WASSER_VARIANTEN = {
    'KLAR':        ("Wasser Klar",        10.75, 0.063),
    'SAUBER_BLAU': ("Wasser Sauber Blau",  6.0,  0.0),
    'GEBIRGSBACH': ("Wasser Gebirgsbach",  3.0,  0.15),
    'ALGEN':       ("Wasser Algen",        1.6,  0.35),
    'OZEAN':       ("Wasser Ozean",       20.0,  0.02),
    'TRUEBE':      ("Wasser Truebe",       0.25, 0.35),
}


def _transmission_key(inputs):
    """The input is named differently depending on the Blender version."""
    for key in ("Transmission Weight", "Transmission"):
        if key in inputs:
            return key
    return None


def _neues_material(name):
    from ..sim.aussehen import MATERIAL_STAND, STAND_KEY
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    # drivers of the old nodes (animated ripples) would otherwise remain
    mat.node_tree.animation_data_clear()
    mat[STAND_KEY] = MATERIAL_STAND
    mat.pop(WASSER_STAND_KEY, None)     # set again only by wasser_aus_einstellungen
    return mat


# --- Studio look: the principle of studio water materials (light through the
# surface, fine ripples, optional dispersion), with water's measured values.
#
# Fine ripples below the simulation resolution are capillary-gravity waves.
# At lambda = 2 pi sqrt(sigma / (rho g)) = 1.71 cm both restoring forces are
# equal and the phase speed has its minimum (4 g sigma / rho)^(1/4) = 23 cm/s
# (sigma = 72.75 mN/m at 20 °C, Vargaftik, Volkov & Voljak 1983, J. Phys. Chem.
# Ref. Data 12:817; dispersion relation of capillary-gravity waves, Lamb 1932,
# Hydrodynamics).
KAPILLAR_WELLENLAENGE = 0.0171
KAPILLAR_TEMPO = 0.231
# Ripple height at slider 1 — APPROXIMATION (wave steepness k a = 0.18)
KAPILLAR_HOEHE = 0.0005
# Refractive index of water at 20 °C for red, green and blue (640, 550 and
# 465 nm), from the Sellmeier fit of Daimon & Masumura 2007, Appl. Opt.
# 46:3811 (Abbe number 55.8). Blender 5.2 has no dispersion input yet.
WASSER_IOR_RGB = (1.33192, 1.33468, 1.33867)
# Caustic line pattern (1 - |2 noise - 1| / 0.05)^2 of a noise with detail
# 2: thin bright lines on a dark ground. Its mean over a rendered plane is
# 0.0938 (measured 02.11.2026, 2 m plane, 15 cm cells), so this factor keeps
# the mean brightness on the ground. The pattern itself is an APPROXIMATION.
KAUSTIK_LINIE = 0.05
KAUSTIK_NORMIERUNG = 1.0 / 0.0938


# The scene's water settings a water material is built from. The material
# keeps them (WASSER_STAND_KEY), so the panel can say when a slider has moved
# since Apply Water.
WASSER_EINSTELLUNGEN = ("wasser_variante", "wasser_sichtweite", "wasser_geloest",
                        "wasser_kraeuselung", "wasser_schatten", "wasser_kaustik",
                        "wasser_kaustik_groesse", "wasser_wellen", "wasser_dispersion")
WASSER_STAND_KEY = "hk_wasser"


def wasser_aus_einstellungen(scene, variante=None):
    """The water material from the scene's water sliders."""
    p = scene.hydrokit
    fps = scene.render.fps / max(scene.render.fps_base, 1e-6)
    mat = baue_wasser(variante or p.wasser_variante, p.wasser_kraeuselung,
                      p.wasser_sichtweite, p.wasser_geloest,
                      schatten=p.wasser_schatten, kaustik=p.wasser_kaustik,
                      kaustik_groesse=p.wasser_kaustik_groesse, wellen=p.wasser_wellen,
                      dispersion=p.wasser_dispersion, fps=fps)
    stand = {k: getattr(p, k) for k in WASSER_EINSTELLUNGEN}
    stand["wasser_variante"] = variante or p.wasser_variante
    stand["wasser_dispersion"] = int(p.wasser_dispersion)
    mat[WASSER_STAND_KEY] = stand
    return mat


def wasser_stand(scene, flaeche):
    """(name of the applied water, names of the settings changed since) for
    the panel; (None, []) without a water material. Materials from before
    02.11.2026 carry no settings: only their water is known then."""
    import math
    mat = next((m for m in flaeche.data.materials if m is not None), None) if flaeche else None
    if mat is None:
        return None, []
    p = scene.hydrokit
    varianten = p.bl_rna.properties["wasser_variante"].enum_items
    gespeichert = mat.get(WASSER_STAND_KEY)
    if gespeichert is None:
        variante = next((k for k, (kurz, *_) in WASSER_VARIANTEN.items()
                         if mat.name == PREFIX + kurz), None)
        return (varianten[variante].name if variante else mat.name), []
    gespeichert = gespeichert.to_dict()
    variante = gespeichert.get("wasser_variante")
    offen = []
    for k in WASSER_EINSTELLUNGEN:
        if k not in gespeichert:
            continue
        alt, jetzt = gespeichert[k], getattr(p, k)
        if isinstance(jetzt, bool):
            gleich = bool(alt) == jetzt
        elif isinstance(jetzt, float):
            gleich = math.isclose(float(alt), jetzt, rel_tol=1e-4, abs_tol=1e-9)
        else:
            gleich = alt == jetzt
        if not gleich:
            offen.append(p.bl_rna.properties[k].name)
    name = varianten[variante].name if variante in varianten.keys() else mat.name
    return name, offen


def _zeit_treiber(eingang, schwingungen_pro_sekunde, fps):
    """Animates a 4D noise: W advances with the scene time."""
    fc = eingang.driver_add("default_value")
    fc.driver.type = 'SCRIPTED'
    fc.driver.expression = f"frame*{schwingungen_pro_sekunde / max(fps, 1e-6):.6f}"


def _rauschen_4d(nt, koord, groesse, ort, detail=3.0):
    noise = nt.nodes.new("ShaderNodeTexNoise"); noise.location = ort
    noise.noise_dimensions = '4D'
    noise.inputs["Scale"].default_value = 1.0 / max(groesse, 1e-4)
    noise.inputs["Detail"].default_value = detail
    noise.inputs["Roughness"].default_value = 0.5
    nt.links.new(koord.outputs["Object"], noise.inputs["Vector"])
    return noise


def wasser_optik(sichtweite, geloest):
    """(absorption R G B, scattering) in 1/m for this visibility in m and
    dissolved matter a440 in 1/m."""
    import math
    from ..sim.aussehen import WASSER_ABSORPTION
    absorption = tuple(w + geloest * math.exp(-CDOM_STEIGUNG * (l - 440.0))
                       for w, l in zip(WASSER_ABSORPTION, WELLENLAENGEN))
    c = 4.8 / max(sichtweite, 0.01)
    return absorption, max(c - absorption[1], 0.0)


# Light in murky water is scattered many times before it leaves. Cycles'
# default of 0 volume bounces keeps only the first scattering: measured on
# the waterfall pool (visibility 3 m), 0 bounces came out 23 % too dark,
# 4 bounces 3 %, 16 and 64 identical.
VOLUMEN_STRAHLEN = 16


def volumen_strahlen(scene):
    """Raises (never lowers) Cycles' volume and total bounces so the water's
    scattering is complete."""
    c = getattr(scene, "cycles", None)
    if c is None:
        return
    if c.volume_bounces < VOLUMEN_STRAHLEN:
        c.volume_bounces = VOLUMEN_STRAHLEN
    if c.max_bounces < VOLUMEN_STRAHLEN:
        c.max_bounces = VOLUMEN_STRAHLEN


def baue_wasser(variante='GEBIRGSBACH', kraeuselung=0.035, sichtweite=None, geloest=None,
                schatten=1.0, kaustik=0.5, kaustik_groesse=0.15, wellen=1.0,
                dispersion=False, fps=24.0):
    """Water material: a clear surface (IOR 1.333) over a volume that
    absorbs and scatters as measured (see wasser_optik). sichtweite and
    geloest default to the variant's values.

    schatten: share of sunlight that passes the surface to the ground
    (shadow rays; the volume still absorbs them); kaustik: strength of the
    caustic pattern on it, kaustik_groesse its cell size in m; wellen:
    height of the capillary ripples (1 = KAPILLAR_HOEHE); dispersion: one
    refraction per colour channel (WASSER_IOR_RGB)."""
    kurzname, sicht0, geloest0 = WASSER_VARIANTEN[variante]
    absorption, streuung = wasser_optik(sicht0 if sichtweite is None else sichtweite,
                                        geloest0 if geloest is None else geloest)
    mat = _neues_material(PREFIX + kurzname)
    nt = mat.node_tree

    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (1220, 0)
    koord = nt.nodes.new("ShaderNodeTexCoord"); koord.location = (-780, 0)

    # Capillary ripples: a 4D noise at their wavelength, moving on at their
    # frequency c / lambda, as a bump of their height
    wellen_rauschen = _rauschen_4d(nt, koord, KAPILLAR_WELLENLAENGE, (-560, 120))
    _zeit_treiber(wellen_rauschen.inputs["W"], KAPILLAR_TEMPO / KAPILLAR_WELLENLAENGE, fps)
    bump = nt.nodes.new("ShaderNodeBump"); bump.location = (-340, 120)
    bump.inputs["Strength"].default_value = 1.0 if wellen > 0.0 else 0.0
    bump.inputs["Distance"].default_value = KAPILLAR_HOEHE * wellen
    nt.links.new(wellen_rauschen.outputs["Fac"], bump.inputs["Height"])
    normale = bump.outputs["Normal"]

    if dispersion:
        # One glass per colour channel with that channel's refractive index
        oberflaeche = None
        for kanal, ior in enumerate(WASSER_IOR_RGB):
            glas = nt.nodes.new("ShaderNodeBsdfGlass"); glas.location = (180, 360 - 180 * kanal)
            glas.inputs["Color"].default_value = tuple(1.0 if c == kanal else 0.0 for c in range(3)) + (1.0,)
            glas.inputs["Roughness"].default_value = kraeuselung
            glas.inputs["IOR"].default_value = ior
            nt.links.new(normale, glas.inputs["Normal"])
            if oberflaeche is None:
                oberflaeche = glas.outputs["BSDF"]
            else:
                summe = nt.nodes.new("ShaderNodeAddShader"); summe.location = (380, 300 - 120 * kanal)
                nt.links.new(oberflaeche, summe.inputs[0])
                nt.links.new(glas.outputs["BSDF"], summe.inputs[1])
                oberflaeche = summe.outputs["Shader"]
    else:
        bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (180, 260)
        i = bsdf.inputs
        i["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
        # A tiny roughness: at 0 the surface is a perfect mirror and in a
        # plain scene looks like liquid metal
        i["Roughness"].default_value = kraeuselung
        i["IOR"].default_value = 1.333          # water, not glass (1.45)
        key = _transmission_key(i)
        if key:
            i[key].default_value = 1.0
        nt.links.new(normale, i["Normal"])
        oberflaeche = bsdf.outputs["BSDF"]

    if schatten > 0.0:
        # LIGHT THROUGH THE SURFACE. Cycles can hardly find the paths that
        # refract sunlight through a wavy surface onto the ground (caustics):
        # the ground under water stays dark and noisy. Shadow rays therefore
        # pass the surface, weakened by its Fresnel reflection; the volume
        # below absorbs and scatters them as measured. Render approximation.
        weg = nt.nodes.new("ShaderNodeLightPath"); weg.location = (400, 620)
        fresnel = nt.nodes.new("ShaderNodeFresnel"); fresnel.location = (-120, -160)
        fresnel.inputs["IOR"].default_value = 1.333
        nt.links.new(normale, fresnel.inputs["Normal"])
        durch = nt.nodes.new("ShaderNodeMath"); durch.location = (80, -160)
        durch.operation = 'SUBTRACT'
        durch.inputs[0].default_value = 1.0
        nt.links.new(fresnel.outputs["Fac"], durch.inputs[1])
        licht = durch.outputs["Value"]
        if kaustik > 0.0:
            # Caustic network: bright lines where the noise crosses 0.5,
            # normalised to a mean of 1, faded in with the strength. Cells of
            # the given size, moving on at the frequency of gravity waves of
            # that length, sqrt(g / (2 pi lambda)).
            k_rauschen = _rauschen_4d(nt, koord, kaustik_groesse, (-560, -360), detail=2.0)
            _zeit_treiber(k_rauschen.inputs["W"],
                          (9.81 / (2.0 * 3.141592653589793 * max(kaustik_groesse, 1e-3))) ** 0.5, fps)
            mitte = nt.nodes.new("ShaderNodeMath"); mitte.location = (-340, -360)
            mitte.operation = 'MULTIPLY_ADD'
            mitte.inputs[1].default_value = 2.0
            mitte.inputs[2].default_value = -1.0
            nt.links.new(k_rauschen.outputs["Fac"], mitte.inputs[0])
            betrag = nt.nodes.new("ShaderNodeMath"); betrag.location = (-160, -360)
            betrag.operation = 'ABSOLUTE'
            nt.links.new(mitte.outputs["Value"], betrag.inputs[0])
            linie = nt.nodes.new("ShaderNodeMapRange"); linie.location = (20, -360)
            linie.clamp = True
            linie.inputs["From Min"].default_value = 0.0
            linie.inputs["From Max"].default_value = KAUSTIK_LINIE
            linie.inputs["To Min"].default_value = 1.0
            linie.inputs["To Max"].default_value = 0.0
            nt.links.new(betrag.outputs["Value"], linie.inputs["Value"])
            quadrat = nt.nodes.new("ShaderNodeMath"); quadrat.location = (200, -360)
            quadrat.operation = 'POWER'
            quadrat.inputs[1].default_value = 2.0
            nt.links.new(linie.outputs["Result"], quadrat.inputs[0])
            faktor = nt.nodes.new("ShaderNodeMath"); faktor.location = (380, -360)
            faktor.operation = 'MULTIPLY_ADD'
            faktor.inputs[1].default_value = kaustik * KAUSTIK_NORMIERUNG
            faktor.inputs[2].default_value = 1.0 - kaustik
            nt.links.new(quadrat.outputs["Value"], faktor.inputs[0])
            mal = nt.nodes.new("ShaderNodeMath"); mal.location = (560, -200)
            mal.operation = 'MULTIPLY'
            nt.links.new(licht, mal.inputs[0])
            nt.links.new(faktor.outputs["Value"], mal.inputs[1])
            licht = mal.outputs["Value"]
        klar = nt.nodes.new("ShaderNodeBsdfTransparent"); klar.location = (760, -120)
        nt.links.new(licht, klar.inputs["Color"])
        anteil = nt.nodes.new("ShaderNodeMath"); anteil.location = (760, 560)
        anteil.operation = 'MULTIPLY'
        anteil.inputs[1].default_value = schatten
        nt.links.new(weg.outputs["Is Shadow Ray"], anteil.inputs[0])
        mischen = nt.nodes.new("ShaderNodeMixShader"); mischen.location = (980, 200)
        nt.links.new(anteil.outputs["Value"], mischen.inputs["Fac"])
        nt.links.new(oberflaeche, mischen.inputs[1])
        nt.links.new(klar.outputs["BSDF"], mischen.inputs[2])
        oberflaeche = mischen.outputs["Shader"]

    nt.links.new(oberflaeche, out.inputs["Surface"])

    # Volume Absorption takes density x (1 - colour) per channel
    dichte = max(absorption)
    absorb = nt.nodes.new("ShaderNodeVolumeAbsorption"); absorb.location = (420, -240)
    absorb.inputs["Color"].default_value = (*(1.0 - a / dichte for a in absorption), 1.0)
    absorb.inputs["Density"].default_value = dichte
    if streuung <= 0.0:
        nt.links.new(absorb.outputs["Volume"], out.inputs["Volume"])
        return mat
    streu = nt.nodes.new("ShaderNodeVolumeScatter"); streu.location = (420, -420)
    streu.inputs["Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    streu.inputs["Density"].default_value = streuung
    if "Anisotropy" in streu.inputs:
        streu.inputs["Anisotropy"].default_value = TEILCHEN_G
    addiere = nt.nodes.new("ShaderNodeAddShader"); addiere.location = (640, -320)
    nt.links.new(absorb.outputs["Volume"], addiere.inputs[0])
    nt.links.new(streu.outputs["Volume"], addiere.inputs[1])
    nt.links.new(addiere.outputs["Shader"], out.inputs["Volume"])
    return mat


def register():
    # Rebuild an outdated look when a file is opened (aussehen.MATERIAL_STAND)
    from ..sim import anzeige
    anzeige.lade_handler_an()


def unregister():
    from ..sim import anzeige
    anzeige.lade_handler_aus()
