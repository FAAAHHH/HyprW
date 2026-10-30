# HyprW — mist from whitewater
# SPDX-License-Identifier: GPL-3.0-or-later
#
# At a waterfall water atomises into mist. FLIP Fluids provides the spray
# particles for it, but displays them as tiny bodies — a cloud of little
# balls, not mist.
#
# This module turns the particles into a real volume (Points to Volume)
# and gives it a volume shader. It costs NO new bake: it works on the
# particles that are already baked.
#
# Peculiarity: the particles CANNOT be reached through an "Object Info" node
# — FLIP's whitewater modifier output 0 vertices throughout the test.
# The raw data is in the mesh data block of the spray object, though
# (measured: 7775 vertices). So the mist object SHARES that data block and
# gets the points directly as modifier input. Checked: the shared data block
# follows the cache (frame 60 -> 6212, frame 180 -> 1736 vertices).

import bpy
from ..utils import flip_bridge

GRUPPE = "HK Nebel"
OBJEKT = "HK_Nebel"
MATERIAL = "HK Nebel"


def baue_material(dichte=0.6):
    """Volume shader: mist scatters light, it does not glow."""
    mat = bpy.data.materials.get(MATERIAL)
    if mat is None:
        mat = bpy.data.materials.new(MATERIAL)
    mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial"); out.location = (260, 0)
    vol = nt.nodes.new("ShaderNodeVolumePrincipled"); vol.location = (-60, 0)
    vol.inputs["Color"].default_value = (0.92, 0.95, 1.0, 1.0)
    vol.inputs["Density"].default_value = dichte
    if "Anisotropy" in vol.inputs:
        # slightly forward scattering — that gives mist its sheen against the light
        vol.inputs["Anisotropy"].default_value = 0.3
    nt.links.new(vol.outputs["Volume"], out.inputs["Volume"])
    return mat


def baue_gruppe(material, radius, dichte, hoehe, hoehe_an):
    """Node group: particles -> points -> volume -> material.

    The input is the shared mesh of the spray object, not an Object Info node.
    """
    ng = bpy.data.node_groups.get(GRUPPE)
    if ng is None:
        ng = bpy.data.node_groups.new(GRUPPE, "GeometryNodeTree")
    ng.nodes.clear()
    ng.links.clear()
    vorhanden = {(i.name, i.in_out) for i in ng.interface.items_tree
                 if i.item_type == 'SOCKET'}
    if ("Geometry", 'INPUT') not in vorhanden:
        ng.interface.new_socket("Geometry", in_out='INPUT',
                                socket_type='NodeSocketGeometry')
    if ("Geometry", 'OUTPUT') not in vorhanden:
        ng.interface.new_socket("Geometry", in_out='OUTPUT',
                                socket_type='NodeSocketGeometry')

    n = ng.nodes
    gi = n.new("NodeGroupInput"); gi.location = (-620, 0)
    m2p = n.new("GeometryNodeMeshToPoints"); m2p.location = (-400, 0)
    ng.links.new(gi.outputs[0], m2p.inputs["Mesh"])
    letzte = m2p.outputs["Points"]

    if hoehe_an:
        # Only particles above a height: at a waterfall the mist is at the top,
        # none should lie over calm water.
        sep = n.new("GeometryNodeSeparateGeometry"); sep.location = (-120, 0)
        sep.domain = 'POINT'
        pos = n.new("GeometryNodeInputPosition"); pos.location = (-480, -280)
        sxyz = n.new("ShaderNodeSeparateXYZ"); sxyz.location = (-330, -280)
        gr = n.new("ShaderNodeMath"); gr.location = (-180, -280)
        gr.operation = 'GREATER_THAN'
        gr.inputs[1].default_value = hoehe
        ng.links.new(pos.outputs["Position"], sxyz.inputs["Vector"])
        ng.links.new(sxyz.outputs["Z"], gr.inputs[0])
        ng.links.new(gr.outputs["Value"], sep.inputs["Selection"])
        ng.links.new(letzte, sep.inputs["Geometry"])
        letzte = sep.outputs["Selection"]

    p2v = n.new("GeometryNodePointsToVolume"); p2v.location = (120, 0)
    p2v.inputs["Density"].default_value = dichte
    p2v.inputs["Radius"].default_value = radius
    ng.links.new(letzte, p2v.inputs["Points"])

    setm = n.new("GeometryNodeSetMaterial"); setm.location = (320, 0)
    setm.inputs["Material"].default_value = material
    ng.links.new(p2v.outputs["Volume"], setm.inputs["Geometry"])

    out = n.new("NodeGroupOutput"); out.location = (520, 0)
    ng.links.new(setm.outputs["Geometry"], out.inputs[0])
    return ng


def vorschlag(context):
    """Derive radius and density from the voxel size.

    Spray particles lie about one voxel width apart. A radius of about three
    voxels just lets the spheres merge without becoming a block. The density
    has to scale INVERSELY: with a large radius many spheres overlap, and the
    optical depth grows with the radius. So fixed numbers only work for one
    scene size.

    The constant comes from a series of measurements on a waterfall scene
    (2.7 cm voxels, six combinations rendered, 5_Nebel_Messreihe.png):

        radius  8 cm, density 0.5   barely visible
        radius  8 cm, density 2.0   just right     <-- product 0.16
        radius  8 cm, density 6.0   dark lump
        radius 22 cm, density 0.5   soft veil, still usable
        radius 22 cm, density 2.0   already too dense

    BY THE WAY, high density makes the mist DARKER, not brighter: the volume
    then absorbs more than it scatters.
    """
    voxel = flip_bridge.get_voxel_size(context)
    if not voxel:
        return None
    radius = voxel * 3.0
    # keep optical depth ~ density * radius constant
    dichte = 0.16 / max(radius, 1e-4)
    return radius, min(dichte, 8.0)


class HYDROKIT_OT_auto_mist(bpy.types.Operator):
    bl_idname = "hydrokit.auto_mist"
    bl_label = "Values from Scene"
    bl_description = "Computes radius and density from the voxel size of the domain"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return flip_bridge.get_domain(context) is not None

    def execute(self, context):
        v = vorschlag(context)
        if v is None:
            self.report({'WARNING'}, "No domain found")
            return {'CANCELLED'}
        props = context.scene.hydrokit
        props.nebel_radius, props.nebel_dichte = v
        self.report({'INFO'},
                    f"Radius {v[0]*100:.1f} cm, density {v[1]:.2f}")
        return {'FINISHED'}


class HYDROKIT_OT_create_mist(bpy.types.Operator):
    bl_idname = "hydrokit.create_mist"
    bl_label = "Create Mist"
    bl_description = (
        "Turns the spray particles into a real mist volume. "
        "Does not need a new bake"
    )
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return "spray" in flip_bridge.get_whitewater_objects(context)

    def execute(self, context):
        props = context.scene.hydrokit
        quelle = flip_bridge.get_whitewater_objects(context).get("spray")
        if quelle is None:
            self.report({'WARNING'}, "No spray object — bake with whitewater")
            return {'CANCELLED'}

        mat = baue_material(props.nebel_dichte)
        ng = baue_gruppe(mat, props.nebel_radius, props.nebel_dichte,
                         props.nebel_hoehe, props.nebel_hoehe_an)

        obj = bpy.data.objects.get(OBJEKT)
        if obj is None:
            # Shares the mesh data block with the spray object: that way the
            # particles arrive directly, and the object follows the cache by itself.
            obj = bpy.data.objects.new(OBJEKT, quelle.data)
            context.scene.collection.objects.link(obj)
        elif obj.data is not quelle.data:
            obj.data = quelle.data
        obj.matrix_world = quelle.matrix_world.copy()
        if obj.name not in context.scene.collection.all_objects:
            context.scene.collection.objects.link(obj)

        mod = next((m for m in obj.modifiers if m.type == 'NODES'), None)
        if mod is None:
            mod = obj.modifiers.new("HK Nebel", 'NODES')
        mod.node_group = ng

        self.report({'INFO'}, f"Mist from {OBJEKT} — radius "
                              f"{props.nebel_radius * 100:.0f} cm")
        return {'FINISHED'}


class HYDROKIT_OT_remove_mist(bpy.types.Operator):
    bl_idname = "hydrokit.remove_mist"
    bl_label = "Remove Mist"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = bpy.data.objects.get(OBJEKT)
        if obj is not None:
            bpy.data.objects.remove(obj, do_unlink=True)
        ng = bpy.data.node_groups.get(GRUPPE)
        if ng is not None:
            bpy.data.node_groups.remove(ng)
        self.report({'INFO'}, "Mist removed")
        return {'FINISHED'}


classes = (HYDROKIT_OT_auto_mist, HYDROKIT_OT_create_mist,
           HYDROKIT_OT_remove_mist)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
