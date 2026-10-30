# HyprW — Cache
# SPDX-License-Identifier: GPL-3.0-or-later
#
# One .bobj file per frame with the surface mesh, plus a small
# info.json with the key data. Deliberately the same format as FLIP Fluids:
# TriangleMesh.to_bobj()/from_bobj() already exist, and anyone with existing
# caches can keep using them.
#
# Layout:
#   <cache>/info.json          grid, offset, frames
#   <cache>/frames/000001.bobj surface
#   <cache>/frames/foam000001.bobj   (if whitewater)

import json
import os

from ..ffengine.trianglemesh import TriangleMesh

INFO = "info.json"
FRAMES = "frames"
GISCHT_ARTEN = ("foam", "bubble", "spray", "dust")


def pfad_aufloesen(roh):
    """Blender path (//...) to a real path."""
    import bpy
    return bpy.path.abspath(roh) if roh else ""


def vorbereiten(cache):
    os.makedirs(os.path.join(cache, FRAMES), exist_ok=True)


def info_schreiben(cache, daten):
    vorbereiten(cache)
    with open(os.path.join(cache, INFO), "w") as f:
        json.dump(daten, f, indent=2)


def info_lesen(cache):
    pfad = os.path.join(cache, INFO)
    if not os.path.isfile(pfad):
        return None
    try:
        with open(pfad) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _datei(cache, frame, art=None):
    name = f"{art or ''}{frame:06d}.bobj"
    return os.path.join(cache, FRAMES, name)


def mesh_schreiben(cache, frame, rohdaten, art=None):
    vorbereiten(cache)
    with open(_datei(cache, frame, art), "wb") as f:
        f.write(rohdaten)


def mesh_lesen(cache, frame, art=None):
    """TriangleMesh or None."""
    pfad = _datei(cache, frame, art)
    if not os.path.isfile(pfad):
        return None
    try:
        with open(pfad, "rb") as f:
            daten = f.read()
        if len(daten) < 8:
            return None
        return TriangleMesh.from_bobj(daten)
    except (OSError, ValueError, Exception):
        return None


# --- Whitewater particles ------------------------------------------------
# A format of its own instead of the mesh files per type, because the ID is
# stored here as well. It stays the same across frames and allows dosing
# the amount afterwards without the selection flickering.
#
#   int32   count
#   float32 x,y,z   per particle  position
#   float32 x,y,z   per particle  velocity
#   float32         per particle  remaining lifetime (counts down!)
#   uint8   ID      per particle  (0..255)
#   uint8   type    per particle  (character from the engine)
#
# The VELOCITY is written as well, because it tells mist from spray: a drop
# slowed down to air speed floats — it IS mist. That is in the data and
# does not have to be forced in the simulation.

PARTIKEL_ENDUNG = ".hkp"
KOPF = b"HKP3"


def _partikeldatei(cache, frame):
    return os.path.join(cache, FRAMES, f"{frame:06d}{PARTIKEL_ENDUNG}")


def partikel_schreiben(cache, frame, positionen, geschwindigkeiten,
                       lebensdauern, kennungen, arten):
    import struct
    vorbereiten(cache)
    n = len(kennungen)
    with open(_partikeldatei(cache, frame), "wb") as f:
        f.write(KOPF)
        f.write(struct.pack("<i", n))
        f.write(positionen)
        f.write(geschwindigkeiten)
        f.write(lebensdauern)
        f.write(kennungen)
        f.write(arten)


def partikel_lesen(cache, frame):
    """(positions, velocity, lifetime, IDs, types) as bytes — or None."""
    import struct
    pfad = _partikeldatei(cache, frame)
    if not os.path.isfile(pfad):
        return None
    try:
        with open(pfad, "rb") as f:
            daten = f.read()
        if len(daten) < 8 or daten[:4] != KOPF:
            return None            # older cache, please bake again
        n = struct.unpack("<i", daten[4:8])[0]
        if n <= 0:
            return b"", b"", b"", b"", b""
        a = 8
        b = a + n * 12             # positions
        c = b + n * 12             # velocities
        d = c + n * 4              # lifetimes
        e = d + n                  # IDs
        f_ = e + n                 # types
        if len(daten) < f_:
            return None
        return daten[a:b], daten[b:c], daten[c:d], daten[d:e], daten[e:f_]
    except (OSError, ValueError, struct.error):
        return None


def mesh_lesen_np(cache, frame, art=None):
    """(vertices, triangles) as NumPy arrays — or None.

    Fast version of mesh_lesen(). TriangleMesh.from_bobj() unpacks through
    struct into Python lists; at 170,000 vertices that cost 200 ms per frame.
    np.frombuffer reads the same bytes without a copy.
    """
    import numpy as np
    pfad = _datei(cache, frame, art)
    if not os.path.isfile(pfad):
        return None
    try:
        with open(pfad, "rb") as f:
            daten = f.read()
        if len(daten) < 8:
            return None
        nv = int(np.frombuffer(daten, np.int32, 1, 0)[0])
        ecken = np.frombuffer(daten, np.float32, nv * 3, 4)
        off = 4 + nv * 12
        nt = int(np.frombuffer(daten, np.int32, 1, off)[0])
        dreiecke = np.frombuffer(daten, np.int32, nt * 3, off + 4)
        return ecken, dreiecke
    except (OSError, ValueError):
        return None


def gebackene_frames(cache):
    """Sorted list of the frames for which a surface exists."""
    ordner = os.path.join(cache, FRAMES)
    if not os.path.isdir(ordner):
        return []
    frames = []
    for name in os.listdir(ordner):
        stamm, endung = os.path.splitext(name)
        if endung == ".bobj" and stamm.isdigit():
            frames.append(int(stamm))
    return sorted(frames)


def leeren(cache):
    """Remove all baked data, keep the folder itself."""
    import shutil
    ordner = os.path.join(cache, FRAMES)
    if os.path.isdir(ordner):
        shutil.rmtree(ordner, ignore_errors=True)
    pfad = os.path.join(cache, INFO)
    if os.path.isfile(pfad):
        os.remove(pfad)
    vorbereiten(cache)
