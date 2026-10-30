# MIT License
#
# Copyright (C) 2026 FAAAHHH (HyprW)
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.

# HyprW GPU support (engine side: hkgpu.h). Module level, not per
# simulation: there is one GPU per process.

import ctypes
from ctypes import c_int, c_double, c_void_p

from .ffengine import ffengine as lib
from . import pybindings as pb


def _call(name, argtypes, restype, params):
    libfunc = getattr(lib, name)
    pb.init_lib_func(libfunc, argtypes + [c_void_p], restype)
    return pb.execute_lib_func(libfunc, params)


def _text(name):
    buffer = ctypes.create_string_buffer(1024)
    _call(name, [ctypes.POINTER(ctypes.c_char), c_int], None, [buffer, len(buffer)])
    return buffer.value.decode("utf-8", "replace")


def is_supported():
    """The loaded engine has GPU support built in."""
    try:
        lib.HKGPU_is_available
        return True
    except AttributeError:
        return False


def is_available():
    """A CUDA driver and a usable NVIDIA GPU. Probed once, no GPU memory used."""
    return bool(_call("HKGPU_is_available", [], c_int, []))


def device_name():
    return _text("HKGPU_get_device_name")


def device_memory():
    """Total memory of the GPU in bytes."""
    return _call("HKGPU_get_device_memory", [], c_double, [])


def status_message():
    """Why the GPU is not available, or the last error that fell back to the CPU."""
    return _text("HKGPU_get_status_message")


def release():
    """Free the GPU memory and context (they are created again when needed)."""
    _call("HKGPU_release", [], None, [])


def reset_counters():
    _call("HKGPU_reset_counters", [], None, [])


# The GPU steps (hkgpu.h HKGPU::Step)
SCHRITTE = {"mesh": 0, "curvature": 1, "water": 2, "advection": 3, "pressure": 4}


def counts(step):
    """(calls computed on the GPU, calls that fell back to the CPU) of a step:
    "mesh" (surface mesh field), "curvature" (whitewater: surface curvature) or
    "water" (water particles: liquid level set), "advection" (velocity
    transfer) or "pressure" (pressure solve)."""
    nummer = SCHRITTE[step]
    return (_call("HKGPU_get_gpu_count", [c_int], c_int, [nummer]),
            _call("HKGPU_get_fallback_count", [c_int], c_int, [nummer]))


def mesher_counts():
    return counts("mesh")


def set_verify(enabled):
    """Tests: compute every GPU field on the CPU too and count differing nodes."""
    _call("HKGPU_set_verify_enabled", [c_int], None, [1 if enabled else 0])


def verify_counts(step="mesh"):
    """(values that differ in any bit, values compared) of a step"""
    nummer = SCHRITTE[step]
    return (int(_call("HKGPU_get_verify_mismatches", [c_int], c_double, [nummer])),
            int(_call("HKGPU_get_verify_compared", [c_int], c_double, [nummer])))


def verify_max_diff(step):
    """Largest difference seen for a step that agrees to float rounding only."""
    return _call("HKGPU_get_verify_max_diff", [c_int], c_double, [SCHRITTE[step]])


def verify_times(step):
    """(seconds on the GPU, seconds on the CPU) of a step in verify mode, where
    every call runs both ways on the same input — for picking the faster one."""
    nummer = SCHRITTE[step]
    return (_call("HKGPU_get_verify_gpu_time", [c_int], c_double, [nummer]),
            _call("HKGPU_get_verify_cpu_time", [c_int], c_double, [nummer]))
