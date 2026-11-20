# HyprW — probe bake that finds the faster device for every step
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Runs in a separate Blender process (background mode) on a COPY of the
# user's file, started by HYDROKIT_OT_rechengeraet_messen:
#
#   blender -b --factory-startup <copy.blend> --python geraete_probe.py --
#           <add-on module> <result.json> <cache folder> <frames>
#
# The copy gets a block of water in the lower quarter of the domain, so the
# probe works on a realistic amount of water from the first frame (a
# waterfall's inflow would only have started). Every step is set to the GPU
# with verification on: the engine then computes every call both ways on the
# same input and times both (hkgpu.h, addVerifyTiming). The user's file and
# cache are never touched.

import json
import os
import sys
import time
import traceback

import bpy
import addon_utils

argv = sys.argv[sys.argv.index("--") + 1:]
modul, ziel, cache_ordner, frames = argv[0], argv[1], argv[2], int(argv[3])
ergebnis = {"error": None}


def melden(text):
    """Progress line for the operator that started this process."""
    print("HKPROBE " + text, flush=True)


try:
    fehler = []
    addon_utils.enable(modul, default_set=True, handle_error=lambda ex: fehler.append(str(ex)))
    if fehler:
        raise RuntimeError("HyprW could not be enabled: " + fehler[0])

    from importlib import import_module
    geometrie = import_module(modul + ".sim.geometrie")
    backen = import_module(modul + ".sim.backen")
    gpu = import_module(modul + ".ffengine.gpu")

    scene = bpy.context.scene
    domain = geometrie.finde_domain(scene)
    if domain is None:
        raise RuntimeError("No domain in the scene")
    if not gpu.is_supported() or not gpu.is_available():
        raise RuntimeError("No usable GPU: " + (gpu.status_message() if gpu.is_supported() else "engine without GPU support"))

    # Water block: the domain's lower quarter, a cell's margin to the walls
    mn, groesse = geometrie.domain_bereich(domain)
    masse = geometrie.gitter_masse(domain, domain.hydrokit.aufloesung)
    rand = masse[3] * 2.0 if masse else 0.0
    mesh = bpy.data.meshes.new("HK_Probe_Wasser")
    x0, y0, z0 = mn.x + rand, mn.y + rand, mn.z + rand
    x1, y1, z1 = mn.x + groesse.x - rand, mn.y + groesse.y - rand, mn.z + 0.25 * groesse.z
    ecken = [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
             (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)]
    flaechen = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    mesh.from_pydata(ecken, [], flaechen)
    block = bpy.data.objects.new("HK_Probe_Wasser", mesh)
    scene.collection.objects.link(block)
    block.hydrokit.typ = 'FLUID'

    props = domain.hydrokit
    props.cache_pfad = cache_ordner
    props.rechengeraet = 'GPU'
    props.linse_an = False
    # The probe never resumes. Its cache lies in /tmp, on Fedora RAM (tmpfs):
    # the final state of a quarter domain of water at Resolution 1000 would
    # put up to 7 GB there (274 million particles before merging, 25 bytes each)
    props.zwischenstand_an = False
    scene.frame_end = scene.frame_start + frames - 1

    b = backen.Backer(scene)
    if not b.vorbereiten():
        raise RuntimeError(b.fehler or "Preparation failed")
    gpu.set_verify(True)
    start = time.time()
    weiter = True
    while weiter:
        weiter = b.schritt()
        melden(f"frame {b.fertig}/{frames}")
    b.aufraeumen()

    ergebnis["device"] = gpu.device_name()
    ergebnis["frames"] = frames
    ergebnis["seconds"] = round(time.time() - start, 2)
    ergebnis["steps"] = {}
    for key, name, eigenschaft, erweiterung, schritt in backen.STUFEN:
        if schritt is None:
            continue
        auf_gpu, auf_cpu = gpu.counts(schritt)
        t_gpu, t_cpu = gpu.verify_times(schritt)
        falsch, verglichen = gpu.verify_counts(schritt)
        ergebnis["steps"][key] = {"gpu_seconds": t_gpu, "cpu_seconds": t_cpu,
                                  "gpu_calls": auf_gpu, "cpu_fallbacks": auf_cpu,
                                  "differing": falsch, "compared": verglichen}
except Exception as e:
    ergebnis["error"] = f"{type(e).__name__}: {e}"
    traceback.print_exc()

with open(ziel, "w") as f:
    json.dump(ergebnis, f, indent=1)
melden("done")
