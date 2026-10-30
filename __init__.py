# HyprW — toolkit for realistic water
# Copyright (C) 2026  FAAAHHH
# SPDX-License-Identifier: GPL-3.0-or-later

from .properties import scene_properties, object_properties
from .operators import (doctor_ops, materials_ops, whitewater_ops, sim_ops,
                        wind_ops, boundary_ops, mist_ops,
                        camera_ops, preset_ops,
                        optimize_ops)
from .ui import main_panel, sim_panel, gitter, scrollen

# Order matters: properties first, panels last
_modules = (scene_properties, object_properties, doctor_ops, materials_ops,
            whitewater_ops, wind_ops, boundary_ops, mist_ops, camera_ops,
            preset_ops, optimize_ops, sim_ops,
            main_panel, sim_panel, gitter, scrollen)


def register():
    for mod in _modules:
        mod.register()


def unregister():
    for mod in reversed(_modules):
        mod.unregister()
