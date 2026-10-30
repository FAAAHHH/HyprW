# HyprW — water drops on the camera lens (simple shader version)
# SPDX-License-Identifier: GPL-3.0-or-later
#
# A plane right in front of the camera, parented to it, carries a
# procedural drop shader. The strength is NOT animated by hand but derived
# from the simulation: one pass counts the spray particles around the camera
# per frame and writes keyframes from that.
#
# So drops appear exactly when the camera really stands in the spray
# — and dry off again afterwards.
#
# Purely optical, no simulation: no new bake needed.

import bpy
from mathutils import Vector
from ..utils import flip_bridge

OBJEKT = "HK_Linsentropfen"
MATERIAL = "HK Linsentropfen"
TREIBER = "hydrokit_naesse"      # custom property that gets animated


# Below this particle count the measurement is noise. Normalising to its own
# maximum would otherwise turn it into full wetness — measured: just
# 2 particles gave 1.00.
MINDEST_PARTIKEL = 25


def _material_bauen():
    """Drops refract the light, the rest of the pane is invisible.

    Voronoi provides the cells; a threshold decides which of them become
    drops. The threshold is the animated parameter.
    """
    mat = bpy.data.materials.get(MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL)
    mat.use_nodes = True
    # blend_method no longer exists in Blender 5.x; EEVEE handles it through
    # the shader itself. So only set it if present.
    if hasattr(mat, "blend_method"):
        mat.blend_method = 'BLEND'
    nt = mat.node_tree
    nt.nodes.clear()

    out = nt.nodes.new("ShaderNodeOutputMaterial");  out.location = (760, 0)
    mix = nt.nodes.new("ShaderNodeMixShader");       mix.location = (560, 0)
    klar = nt.nodes.new("ShaderNodeBsdfTransparent"); klar.location = (360, 140)
    tropfen = nt.nodes.new("ShaderNodeBsdfRefraction"); tropfen.location = (360, -120)
    tropfen.inputs["IOR"].default_value = 1.333
    tropfen.inputs["Roughness"].default_value = 0.05

    # Two Voronoi layers: large drops and fine spray mist
    koord = nt.nodes.new("ShaderNodeTexCoord");  koord.location = (-620, 0)
    gross = nt.nodes.new("ShaderNodeTexVoronoi"); gross.location = (-420, 120)
    gross.feature = 'F1'
    gross.inputs["Scale"].default_value = 18.0
    fein = nt.nodes.new("ShaderNodeTexVoronoi");  fein.location = (-420, -160)
    fein.feature = 'F1'
    fein.inputs["Scale"].default_value = 55.0

    nt.links.new(koord.outputs["Window"], gross.inputs["Vector"])
    nt.links.new(koord.outputs["Window"], fein.inputs["Vector"])

    # The smaller the distance to the cell centre, the more likely a drop
    kombi = nt.nodes.new("ShaderNodeMath"); kombi.location = (-220, 0)
    kombi.operation = 'MINIMUM'
    nt.links.new(gross.outputs["Distance"], kombi.inputs[0])
    nt.links.new(fein.outputs["Distance"], kombi.inputs[1])

    # Threshold: the animated control. 0 = dry, 1 = fully fogged
    schwelle = nt.nodes.new("ShaderNodeValue"); schwelle.location = (-220, -240)
    schwelle.name = "Naesse"; schwelle.label = "Wetness"
    schwelle.outputs[0].default_value = 0.0

    skaliert = nt.nodes.new("ShaderNodeMath"); skaliert.location = (-40, -240)
    skaliert.operation = 'MULTIPLY'
    skaliert.inputs[1].default_value = 0.35      # largest drop size
    nt.links.new(schwelle.outputs[0], skaliert.inputs[0])

    maske = nt.nodes.new("ShaderNodeMath"); maske.location = (160, 0)
    maske.operation = 'LESS_THAN'
    nt.links.new(kombi.outputs["Value"], maske.inputs[0])
    nt.links.new(skaliert.outputs["Value"], maske.inputs[1])

    weich = nt.nodes.new("ShaderNodeMath"); weich.location = (360, 60)
    weich.operation = 'MULTIPLY'
    weich.inputs[1].default_value = 0.92          # edges slightly translucent
    nt.links.new(maske.outputs["Value"], weich.inputs[0])

    # Curvature of the drops
    bump = nt.nodes.new("ShaderNodeBump"); bump.location = (160, -300)
    bump.inputs["Strength"].default_value = 0.8
    bump.inputs["Distance"].default_value = 0.04
    nt.links.new(kombi.outputs["Value"], bump.inputs["Height"])
    nt.links.new(bump.outputs["Normal"], tropfen.inputs["Normal"])

    nt.links.new(weich.outputs["Value"], mix.inputs["Fac"])
    nt.links.new(klar.outputs["BSDF"], mix.inputs[1])
    nt.links.new(tropfen.outputs["BSDF"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    return mat


def _scheibe_bauen(context, kamera, abstand):
    """Plane close in front of the camera, filling the frame."""
    obj = bpy.data.objects.get(OBJEKT)
    if obj is None:
        me = bpy.data.meshes.new(OBJEKT)
        me.from_pydata([(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)], [],
                       [(0, 1, 2, 3)])
        me.update()
        obj = bpy.data.objects.new(OBJEKT, me)
        context.scene.collection.objects.link(obj)
    if obj.name not in context.scene.collection.all_objects:
        context.scene.collection.objects.link(obj)

    # Size so that the plane fills the image
    import math
    cam = kamera.data
    winkel = cam.angle
    halbe_breite = math.tan(winkel / 2.0) * abstand
    render = context.scene.render
    seite = render.resolution_y / max(render.resolution_x, 1)
    obj.scale = (halbe_breite * 1.1, halbe_breite * seite * 1.1, 1.0)

    obj.parent = kamera
    obj.matrix_parent_inverse = kamera.matrix_world.inverted()
    obj.location = (0.0, 0.0, -abstand)       # camera looks along -Z
    obj.rotation_euler = (0.0, 0.0, 0.0)
    obj.hide_select = True
    return obj


def naesse_messen(context, radius):
    """Per frame: share of spray particles around the camera.

    Returns {frame: value}, value between 0 and 1.
    """
    kamera = context.scene.camera
    spray = flip_bridge.get_whitewater_objects(context).get("spray")
    if kamera is None or spray is None:
        return {}

    sc = context.scene
    merker = sc.frame_current
    werte = {}
    r2 = radius * radius
    for f in range(sc.frame_start, sc.frame_end + 1):
        sc.frame_set(f)
        pos = kamera.matrix_world.translation
        mw = spray.matrix_world
        treffer = 0
        for v in spray.data.vertices:
            if ((mw @ v.co) - pos).length_squared < r2:
                treffer += 1
        werte[f] = treffer
    sc.frame_set(merker)

    hoechst = max(werte.values()) if werte else 0
    if hoechst == 0:
        return {f: 0.0 for f in werte}, 0
    # Normalised to its own maximum — so the strength is relative to the
    # wettest moment of the shot. That is why the absolute peak value is
    # passed along: with few particles the normalisation is misleading
    # (measured: 2 particles gave wetness 1.00).
    return {f: n / hoechst for f, n in werte.items()}, hoechst


class HYDROKIT_OT_create_lens_drops(bpy.types.Operator):
    bl_idname = "hydrokit.create_lens_drops"
    bl_label = "Set Up Lens Drops"
    bl_description = (
        "Places a pane in front of the camera and lets drops appear on it "
        "when the camera stands in the spray. Purely optical, no new bake"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.scene.camera is not None

    def execute(self, context):
        props = context.scene.hydrokit
        kamera = context.scene.camera
        mat = _material_bauen()
        obj = _scheibe_bauen(context, kamera, props.tropfen_abstand)
        obj.data.materials.clear()
        obj.data.materials.append(mat)
        if TREIBER not in obj:
            obj[TREIBER] = 0.0

        self.report({'INFO'}, "Pane set up — now 'Compute Wetness'")
        return {'FINISHED'}


class HYDROKIT_OT_bake_lens_drops(bpy.types.Operator):
    bl_idname = "hydrokit.bake_lens_drops"
    bl_label = "Compute Wetness"
    bl_description = (
        "Goes through the baked frames, counts the spray particles near the "
        "camera and writes keyframes from that"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (bpy.data.objects.get(OBJEKT) is not None
                and "spray" in flip_bridge.get_whitewater_objects(context))

    def execute(self, context):
        props = context.scene.hydrokit
        mat = bpy.data.materials.get(MATERIAL)
        if mat is None:
            self.report({'ERROR'}, "Material missing — set up first")
            return {'CANCELLED'}
        knoten = mat.node_tree.nodes.get("Naesse")
        if knoten is None:
            self.report({'ERROR'}, "Wetness node missing — set up first")
            return {'CANCELLED'}

        werte, spitze = naesse_messen(context, props.tropfen_radius)
        if not werte:
            self.report({'WARNING'}, "No spray data found")
            return {'CANCELLED'}
        if spitze < MINDEST_PARTIKEL:
            self.report(
                {'WARNING'},
                f"At most {spitze} spray particles within "
                f"{props.tropfen_radius:.2f} m — the camera does not get close "
                "enough to the whitewater. Move the camera closer or increase "
                "the radius")
            return {'CANCELLED'}

        # replace existing animation
        sockel = knoten.outputs[0]
        if mat.node_tree.animation_data is not None:
            mat.node_tree.animation_data_clear()

        nass = 0.0
        traegheit = props.tropfen_traegheit
        for f in sorted(werte):
            ziel = werte[f] * props.tropfen_staerke
            # drops collect quickly and dry off slowly
            nass = max(ziel, nass * traegheit)
            sockel.default_value = min(nass, 1.0)
            sockel.keyframe_insert("default_value", frame=f)

        hoch = sum(1 for v in werte.values() if v > 0.1)
        self.report({'INFO'},
                    f"{len(werte)} keyframes, the camera is in the spray in "
                    f"{hoch} frames (peak {spitze} particles)")
        return {'FINISHED'}


class HYDROKIT_OT_remove_lens_drops(bpy.types.Operator):
    bl_idname = "hydrokit.remove_lens_drops"
    bl_label = "Remove Lens Drops"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = bpy.data.objects.get(OBJEKT)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        mat = bpy.data.materials.get(MATERIAL)
        if mat is not None:
            bpy.data.materials.remove(mat)
        self.report({'INFO'}, "Lens drops removed")
        return {'FINISHED'}


classes = (HYDROKIT_OT_create_lens_drops, HYDROKIT_OT_bake_lens_drops,
           HYDROKIT_OT_remove_lens_drops)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
