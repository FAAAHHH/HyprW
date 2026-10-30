# HyprW — connection to FLIP Fluids
# Copyright (C) 2026  FAAAHHH
# SPDX-License-Identifier: GPL-3.0-or-later
#
# This is the ONLY place that talks to FLIP Fluids directly.
# If FLIP Fluids changes its API, only this file needs to be adapted.

import bpy

# Names of the cache objects FLIP Fluids creates
# (see src/addon/objects/flip_fluid_cache.py)
SURFACE_OBJECT = "fluid_surface"
WHITEWATER_OBJECTS = {
    "foam":   "whitewater_foam",
    "bubble": "whitewater_bubble",
    "spray":  "whitewater_spray",
    "dust":   "whitewater_dust",
}

# Sockets of the FF_GeometryNodesWhitewater* groups are looked up by their
# DISPLAY NAME, not by identifier ("Input_5"): identifiers can change when the
# node group is rebuilt, the names are more stable and self-explanatory.
SOCKET_MATERIAL = "Material"
SOCKET_DOMAIN = "FLIP Domain"
SOCKET_SURFACE = "FLIP Surface Mesh"
SOCKET_PARTICLE_SCALE = "Particle Scale"
SOCKET_PARTICLE_SCALE_MULT = "Particle Scale Multiplier"


# ---------------------------------------------------------------- Basics

def is_available():
    """Is FLIP Fluids installed and enabled?"""
    return hasattr(bpy.types.Object, "flip_fluid")


def get_domain(context=None):
    """The domain object of the scene, or None."""
    if not is_available():
        return None
    scene = (context or bpy.context).scene
    ff = getattr(scene, "flip_fluid", None)
    if ff is not None and hasattr(ff, "get_domain_object"):
        try:
            return ff.get_domain_object()
        except Exception:
            pass
    for obj in scene.objects:
        props = getattr(obj, "flip_fluid", None)
        if props is not None and getattr(props, "object_type", "") == "TYPE_DOMAIN":
            return obj
    return None


def get_domain_props(context=None):
    dom = get_domain(context)
    return dom.flip_fluid.domain if dom else None


def get_cache_object(name, context=None):
    scene = (context or bpy.context).scene
    obj = scene.objects.get(name)
    return obj if obj is not None else bpy.data.objects.get(name)


def get_whitewater_objects(context=None):
    """{'foam': obj, ...} — only those that actually exist."""
    found = {}
    for key, name in WHITEWATER_OBJECTS.items():
        obj = get_cache_object(name, context)
        if obj is not None:
            found[key] = obj
    return found


def get_surface_object(context=None):
    return get_cache_object(SURFACE_OBJECT, context)


# ---------------------------------------------------------------- Grid

def get_voxel_size(context=None):
    """Edge length of a grid cell in Blender units.

    In FLIP Fluids the resolution applies to the LONGEST domain side.
    """
    dom = get_domain(context)
    if dom is None:
        return None
    resolution = getattr(dom.flip_fluid.domain.simulation, "resolution", 0)
    if not resolution:
        return None
    longest = max(dom.dimensions)
    return longest / resolution if longest > 0 else None


def get_grid_dimensions(context=None):
    """(x, y, z, total) cell count."""
    dom = get_domain(context)
    voxel = get_voxel_size(context)
    if dom is None or not voxel:
        return None
    gx, gy, gz = (max(1, round(d / voxel)) for d in dom.dimensions)
    return gx, gy, gz, gx * gy * gz


# ---------------------------------------------------- Geometry Nodes access

def get_whitewater_modifier(obj):
    """The FF_GeometryNodesWhitewater* modifier of a cache object."""
    for mod in obj.modifiers:
        if mod.type == 'NODES' and mod.node_group is not None:
            if mod.node_group.name.startswith("FF_GeometryNodesWhitewater"):
                return mod
    return None


def _socket_identifier(modifier, socket_name):
    """Find the identifier ("Input_5") for a display name ("Material")."""
    group = getattr(modifier, "node_group", None)
    if group is None:
        return None
    for item in group.interface.items_tree:
        if getattr(item, "item_type", "") != 'SOCKET':
            continue
        if item.in_out == 'INPUT' and item.name == socket_name:
            return item.identifier
    return None


def set_socket(modifier, socket_name, value):
    """Set a Geometry Nodes input — Blender 4.x and 5.x.

    Blender 5.x removed the ID properties on the modifier; inputs now live
    under modifier.properties.inputs.<identifier>.value. We try both ways
    so the add-on runs on both versions.
    """
    ident = _socket_identifier(modifier, socket_name)
    if ident is None:
        return False

    # Blender 5.x
    props = getattr(modifier, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        try:
            sock = getattr(props.inputs, ident)
            sock.value = value
            modifier.id_data.update_tag()
            return True
        except (AttributeError, TypeError):
            pass

    # Blender 4.x
    try:
        modifier[ident] = value
        modifier.id_data.update_tag()
        return True
    except (KeyError, TypeError):
        return False


def get_socket(modifier, socket_name, default=None):
    ident = _socket_identifier(modifier, socket_name)
    if ident is None:
        return default
    props = getattr(modifier, "properties", None)
    if props is not None and hasattr(props, "inputs"):
        try:
            return getattr(props.inputs, ident).value
        except (AttributeError, TypeError):
            pass
    try:
        return modifier[ident]
    except (KeyError, TypeError):
        return default


def check_sockets(obj):
    """Missing sockets of a cache object — an empty list means all good."""
    erwartet = (SOCKET_MATERIAL, SOCKET_DOMAIN, SOCKET_SURFACE,
                SOCKET_PARTICLE_SCALE, SOCKET_PARTICLE_SCALE_MULT)
    mod = get_whitewater_modifier(obj)
    if mod is None:
        return list(erwartet)
    return [name for name in erwartet if _socket_identifier(mod, name) is None]


# ---------------------------------------------------------------- Apply

def set_whitewater_material(obj, material):
    """Set the material — on the mesh AND in the Geometry Nodes modifier.

    FLIP Fluids does both; setting only one is not enough, because the
    point cloud is drawn through the modifier.
    """
    if obj.type == 'MESH':
        obj.data.materials.clear()
        obj.data.materials.append(material)
    mod = get_whitewater_modifier(obj)
    if mod is None:
        return False
    set_socket(mod, "Apply Material", True)
    return set_socket(mod, SOCKET_MATERIAL, material)


def set_whitewater_particle_scale(obj, scale):
    """Set the display size of the whitewater particles."""
    mod = get_whitewater_modifier(obj)
    if mod is None:
        return False
    return set_socket(mod, SOCKET_PARTICLE_SCALE, float(scale))


def get_whitewater_particle_scale(obj):
    mod = get_whitewater_modifier(obj)
    return get_socket(mod, SOCKET_PARTICLE_SCALE) if mod else None


def get_cache_directory(context=None):
    props = get_domain_props(context)
    if props is None:
        return None
    raw = getattr(props.cache, "cache_directory", "")
    return bpy.path.abspath(raw) if raw else None


# --------------------------------------------------------------- Whitewater look
# FLIP Fluids' whitewater node groups have about 40 inputs, of which the
# add-on only sets a handful. Several of them decide whether the whitewater
# looks like real whitewater or like equally sized spheres.
SOCKET_DISPLAY_MODE   = "Particle Display Mode"
SOCKET_INSTANCING     = "Instancing Mode"
SOCKET_SCALE_RANDOM   = "Particle Scale Random"
SOCKET_RANDOM_BIAS    = "Random Bias"
SOCKET_ROT_RANDOM     = "Randomize Instance Rotation"
SOCKET_ALIGN_VELOCITY = "Align Instance to Velocity"
SOCKET_DUPLICATE      = "Duplicate and Randomize Particles"
SOCKET_NUM_DUPLICATES = "Num Duplicates"
SOCKET_DIST_RADIUS    = "Distribution Radius"
SOCKET_LIFETIME_SCALE = "Lifetime Based Particle Scaling"
SOCKET_FINAL_SCALE    = "Final Scale Factor"
SOCKET_SCALE_DURATION = "Scaling Duration"
SOCKET_SHADE_SMOOTH   = "Shade Smooth Instances"


def set_sockets(obj, werte):
    """Set several sockets of a whitewater object.

    werte: {display name: value}. Returns how many were set.
    """
    modifier = get_whitewater_modifier(obj)
    if modifier is None:
        return 0
    gesetzt = 0
    for name, wert in werte.items():
        if set_socket(modifier, name, wert):
            gesetzt += 1
    return gesetzt
