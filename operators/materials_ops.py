# HyprW — water and whitewater materials
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The shaders are built in code instead of shipped as a .blend:
# - everything is in the source, traceable and versionable
# - no binary files in the add-on, clean for a GPL release
# - adapts to Blender versions (Transmission is named differently in 4.x than in 3.x)

import bpy
from ..utils import flip_bridge

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


# Whitewater type -> (material name, base roughness, transmission, bubble size,
#                     bump, translucency)
GISCHT_MATERIALIEN = {
    "foam":   ("Schaum", 0.80, 0.00, 120.0, 0.60, 0.45),
    "bubble": ("Blasen", 0.25, 0.80,  60.0, 0.35, 0.70),
    "spray":  ("Spray",  0.15, 0.55, 240.0, 0.20, 0.55),
    "dust":   ("Staub",  1.00, 0.45, 400.0, 0.05, 0.80),
}


def _transmission_key(inputs):
    """The input is named differently depending on the Blender version."""
    for key in ("Transmission Weight", "Transmission"):
        if key in inputs:
            return key
    return None


def _neues_material(name):
    mat = bpy.data.materials.get(name)
    if mat is None:
        mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    mat.node_tree.nodes.clear()
    return mat


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


def baue_wasser(variante='GEBIRGSBACH', kraeuselung=0.035, sichtweite=None, geloest=None):
    """Water material: a clear surface (IOR 1.333) over a volume that
    absorbs and scatters as measured (see wasser_optik). sichtweite and
    geloest default to the variant's values."""
    kurzname, sicht0, geloest0 = WASSER_VARIANTEN[variante]
    absorption, streuung = wasser_optik(sicht0 if sichtweite is None else sichtweite,
                                        geloest0 if geloest is None else geloest)
    mat = _neues_material(PREFIX + kurzname)
    nt = mat.node_tree

    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (820, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (420, 180)

    i = bsdf.inputs
    i["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    # A tiny roughness: at 0 the surface is a perfect mirror and in a plain
    # scene looks like liquid metal
    i["Roughness"].default_value = kraeuselung
    i["IOR"].default_value = 1.333          # water, not glass (1.45)
    key = _transmission_key(i)
    if key:
        i[key].default_value = 1.0

    # Fine surface disturbance: breaks up the reflection without making the
    # water matte. Scale deliberately kept small.
    koord = nt.nodes.new("ShaderNodeTexCoord"); koord.location = (-380, -40)
    noise = nt.nodes.new("ShaderNodeTexNoise"); noise.location = (-180, -40)
    noise.inputs["Scale"].default_value = 90.0
    noise.inputs["Detail"].default_value = 6.0
    if "Roughness" in noise.inputs:
        noise.inputs["Roughness"].default_value = 0.6
    bump = nt.nodes.new("ShaderNodeBump"); bump.location = (120, -40)
    bump.inputs["Strength"].default_value = 0.12
    bump.inputs["Distance"].default_value = 0.004
    nt.links.new(koord.outputs["Object"], noise.inputs["Vector"])
    nt.links.new(noise.outputs["Fac"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

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


def baue_gischt(art):
    """Whitewater is white, ROUGH and STRUCTURED.

    A single white Principled node looks like styrofoam. Foam is a mass of
    bubbles: Voronoi provides the cell structure, the bump comes from it.
    Plus translucency, because wet foam lets light through — without it
    it looks like chalk.
    """
    kurzname, rauheit, transmission, zellen, woelbung, durchschein = \
        GISCHT_MATERIALIEN[art]
    mat = _neues_material(PREFIX + kurzname)
    nt = mat.node_tree

    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (620, 0)
    bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled"); bsdf.location = (300, 0)

    i = bsdf.inputs
    i["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    i["Roughness"].default_value = rauheit
    i["IOR"].default_value = 1.333
    key = _transmission_key(i)
    if key:
        i[key].default_value = transmission

    # Translucency: the difference between wet foam and chalk. A Translucent
    # BSDF, not subsurface: FLIP draws whitewater as a point cloud, and
    # Cycles on the GPU renders subsurface on points black (see
    # sim/aussehen.py, schaum_optik)
    durch = nt.nodes.new("ShaderNodeBsdfTranslucent"); durch.location = (300, -260)
    durch.inputs["Color"].default_value = (0.82, 0.92, 1.0, 1.0)
    beide = nt.nodes.new("ShaderNodeMixShader"); beide.location = (480, 0)
    beide.inputs[0].default_value = durchschein
    nt.links.new(bsdf.outputs["BSDF"], beide.inputs[1])
    nt.links.new(durch.outputs["BSDF"], beide.inputs[2])

    koord = nt.nodes.new("ShaderNodeTexCoord"); koord.location = (-620, 0)

    # Bubble structure
    vor = nt.nodes.new("ShaderNodeTexVoronoi"); vor.location = (-420, 120)
    vor.feature = 'F1'
    vor.inputs["Scale"].default_value = zellen
    nt.links.new(koord.outputs["Object"], vor.inputs["Vector"])

    # fine unrest on top, so the cells do not look regular
    noise = nt.nodes.new("ShaderNodeTexNoise"); noise.location = (-420, -180)
    noise.inputs["Scale"].default_value = zellen * 2.5
    noise.inputs["Detail"].default_value = 8.0
    nt.links.new(koord.outputs["Object"], noise.inputs["Vector"])

    misch = nt.nodes.new("ShaderNodeMix"); misch.location = (-200, 0)
    misch.data_type = 'FLOAT'
    misch.inputs["Factor"].default_value = 0.35
    nt.links.new(vor.outputs["Distance"], misch.inputs[2])
    nt.links.new(noise.outputs["Fac"], misch.inputs[3])

    bump = nt.nodes.new("ShaderNodeBump"); bump.location = (40, -160)
    bump.inputs["Strength"].default_value = woelbung
    bump.inputs["Distance"].default_value = 0.02
    nt.links.new(misch.outputs[0], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])
    nt.links.new(bump.outputs["Normal"], durch.inputs["Normal"])

    # Vary roughness too: bubble tops shine, the gaps in between are matte
    rough = nt.nodes.new("ShaderNodeMapRange"); rough.location = (40, 160)
    rough.inputs["From Min"].default_value = 0.0
    rough.inputs["From Max"].default_value = 1.0
    rough.inputs["To Min"].default_value = max(rauheit - 0.18, 0.02)
    rough.inputs["To Max"].default_value = min(rauheit + 0.12, 1.0)
    nt.links.new(misch.outputs[0], rough.inputs["Value"])
    nt.links.new(rough.outputs["Result"], bsdf.inputs["Roughness"])

    out.location = (700, 0)
    nt.links.new(beide.outputs["Shader"], out.inputs["Surface"])
    return mat


class HYDROKIT_OT_apply_materials(bpy.types.Operator):
    bl_idname = "hydrokit.apply_materials"
    bl_label = "Apply Materials"
    bl_description = (
        "Creates water and whitewater materials and assigns them to the "
        "FLIP Fluids objects. Does not need a new bake"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain(context) is not None

    def execute(self, context):
        props = context.scene.hydrokit
        erledigt = []

        surface = flip_bridge.get_surface_object(context)
        if surface is not None and surface.type == 'MESH':
            mat = baue_wasser(props.wasser_variante, props.wasser_kraeuselung,
                              props.wasser_sichtweite, props.wasser_geloest)
            volumen_strahlen(context.scene)
            surface.data.materials.clear()
            surface.data.materials.append(mat)
            erledigt.append(mat.name)

        nicht_gesetzt = []
        for art, obj in flip_bridge.get_whitewater_objects(context).items():
            mat = baue_gischt(art)
            if flip_bridge.set_whitewater_material(obj, mat):
                erledigt.append(mat.name)
            else:
                nicht_gesetzt.append(art)

        if not erledigt:
            self.report({'WARNING'}, "No objects found that anything fits")
            return {'CANCELLED'}
        if nicht_gesetzt:
            self.report({'WARNING'}, "Cannot be assigned: " + ", ".join(nicht_gesetzt))
        self.report({'INFO'}, f"{len(erledigt)} materials set")
        return {'FINISHED'}


classes = (HYDROKIT_OT_apply_materials,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
