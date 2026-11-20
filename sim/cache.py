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
    """Blender path (//...) to a real path. A // path in an unsaved file gives
    "": bpy.path.abspath made it relative to Blender's working folder, so every
    new file showed (and cleared) the same bake, and after the first save the
    bake was gone."""
    import bpy
    if not roh or (roh.startswith("//") and not bpy.data.filepath):
        return ""
    return bpy.path.abspath(roh)


def vorbereiten(cache):
    os.makedirs(os.path.join(cache, FRAMES), exist_ok=True)


def info_schreiben(cache, daten):
    vorbereiten(cache)
    with open(os.path.join(cache, INFO), "w") as f:
        json.dump(daten, f, indent=2)


def info_lesen(cache):
    if not cache:
        return None         # "" is Blender's working folder, never a cache
    pfad = os.path.join(cache, INFO)
    if not os.path.isfile(pfad):
        return None
    try:
        with open(pfad) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


# The frame handler reads info.json on every frame; parsed again only when
# the file changes (a new bake writes it anew)
_INFO = {}


def info_gemerkt(cache):
    """info_lesen for every frame: the same dict until the file's stamp
    (mtime, size, inode) changes. Shared: callers must not change it."""
    if not cache:
        return None
    pfad = os.path.join(cache, INFO)
    try:
        st = os.stat(pfad)
    except OSError:
        return None
    stempel = (pfad, st.st_mtime_ns, st.st_size, st.st_ino)
    if _INFO.get("stempel") == stempel:
        return _INFO["info"]
    info = info_lesen(cache)
    _INFO.clear()
    if info is not None:            # a half-written file is read again next time
        _INFO.update(stempel=stempel, info=info)
    return info


def _datei(cache, frame, art=None):
    name = f"{art or ''}{frame:06d}.bobj"
    return os.path.join(cache, FRAMES, name)


def _ganz_schreiben(pfad, teile):
    """The file appears complete or not at all: written beside and renamed
    over. The bake writes from its worker thread while the viewport follows
    the timeline; a frame read half-written showed torn water."""
    neu = pfad + ".tmp"
    with open(neu, "wb") as f:
        for teil in teile:
            f.write(teil)
    os.replace(neu, pfad)


def mesh_schreiben(cache, frame, rohdaten, art=None):
    vorbereiten(cache)
    _ganz_schreiben(_datei(cache, frame, art), (rohdaten,))


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


def partikel_datei(cache, frame):
    return os.path.join(cache, FRAMES, f"{frame:06d}{PARTIKEL_ENDUNG}")


def partikel_schreiben(cache, frame, positionen, geschwindigkeiten,
                       lebensdauern, kennungen, arten):
    import struct
    vorbereiten(cache)
    n = len(kennungen)
    _ganz_schreiben(partikel_datei(cache, frame),
                    (KOPF, struct.pack("<i", n), positionen, geschwindigkeiten,
                     lebensdauern, kennungen, arten))


def partikel_lesen(cache, frame):
    """(positions, velocity, lifetime, IDs, types) as bytes — or None."""
    import struct
    pfad = partikel_datei(cache, frame)
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


def _block(f, dtype, anzahl):
    """The next `anzahl` values of the file straight into an array of their own."""
    import numpy as np
    a = np.empty(anzahl, dtype)
    puffer = memoryview(a).cast("B")
    gelesen = 0
    while gelesen < len(puffer):
        k = f.readinto(puffer[gelesen:])
        if not k:
            raise ValueError("file ends early")
        gelesen += k
    return a


def partikel_lesen_np(cache, frame, tempo=True, leben=True):
    """(positions n x 3, velocity n x 3, lifetime, IDs, types) as NumPy arrays,
    or None: partikel_lesen for the display. tempo/leben False skip those
    blocks (None in their place).

    partikel_lesen reads the whole file into one bytes object and copies
    every block out of it again: 50 ms in Blender at frames 95-101 of the
    user's waterfall (Resolution 300, 2 million particles, 59 MB). Here each
    block wanted goes straight from the file into its array: 14 ms with the
    velocity, 7 ms without (Light Playback); the lifetime only for the mist
    classification."""
    import numpy as np
    import struct
    pfad = partikel_datei(cache, frame)
    try:
        with open(pfad, "rb", buffering=0) as f:
            kopf = f.read(8)
            if len(kopf) < 8 or kopf[:4] != KOPF:
                return None            # older cache, please bake again
            n = struct.unpack("<i", kopf[4:8])[0]
            if n <= 0:
                drei = np.zeros((0, 3), np.float32)
                return (drei, drei.copy() if tempo else None,
                        np.zeros(0, np.float32) if leben else None,
                        np.zeros(0, np.uint8), np.zeros(0, np.uint8))
            if os.fstat(f.fileno()).st_size < 8 + 30 * n:
                return None            # being written, or cut off
            pos = _block(f, np.float32, 3 * n).reshape(-1, 3)
            vel = None
            if tempo:
                vel = _block(f, np.float32, 3 * n).reshape(-1, 3)
            else:
                f.seek(12 * n, os.SEEK_CUR)
            dauer = None
            if leben:
                dauer = _block(f, np.float32, n)
            else:
                f.seek(4 * n, os.SEEK_CUR)
            return pos, vel, dauer, _block(f, np.uint8, n), _block(f, np.uint8, n)
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
    if not cache:
        return []
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
    if not cache:
        return              # "" would empty frames/ in Blender's working folder
    ordner = os.path.join(cache, FRAMES)
    if os.path.isdir(ordner):
        shutil.rmtree(ordner, ignore_errors=True)
    pfad = os.path.join(cache, INFO)
    if os.path.isfile(pfad):
        os.remove(pfad)
    # with the leftovers of a save that crashed: a full state each, about
    # 1 GB for the waterfall at Resolution 1000 after 250 frames
    for rest in ("zwischenstand", "zwischenstand_neu", "zwischenstand_alt"):
        shutil.rmtree(os.path.join(cache, rest), ignore_errors=True)
    vorbereiten(cache)


def frames_nach_loeschen(cache, frame):
    """Remove what was baked after `frame`: a resumed bake computes it anew."""
    ordner = os.path.join(cache, FRAMES)
    if not os.path.isdir(ordner):
        return
    for name in os.listdir(ordner):
        zahl = os.path.splitext(name)[0][-6:]
        if zahl.isdigit() and int(zahl) > frame:
            try:
                os.remove(os.path.join(ordner, name))
            except OSError:
                pass
