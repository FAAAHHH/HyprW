# HyprW — toolkit for realistic water
# Copyright (C) 2026  FAAAHHH
# SPDX-License-Identifier: GPL-3.0-or-later

from .properties import scene_properties, object_properties
from .operators import materials_ops, sim_ops
from .ui import sim_panel, gitter, scrollen

# Order matters: properties first, panels last
_modules = (scene_properties, object_properties, materials_ops, sim_ops,
            sim_panel, gitter, scrollen)


def register():
    for mod in _modules:
        mod.register()


def unregister():
    for mod in reversed(_modules):
        mod.unregister()
