# HyprW — saved states, so a bake can resume
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The cache holds what is shown (surface, whitewater), not what the simulation
# needs to go on: the water particles. A saved state holds them, the same data
# FLIP Fluids keeps in its savestates (src/addon/bake.py), plus the adaptive
# level per particle, without which merged particles would lose their volume.
#
# Layout:
#   <cache>/zwischenstand/info.json    frame, grid, counts
#   <cache>/zwischenstand/*.data       one file per value, in particle order
#
# Written next to the old state and swapped in when complete: a crash while
# saving leaves the previous state usable. The data reaches the disk (fsync)
# before the swap: without it btrfs may store the renamed folder before its
# data, and after a power cut the files are shorter than info.json says. Such
# a state was accepted and loaded garbage (review 05.11.2026: half-length
# files gave levels up to 255 and positions of 1e-41, the next frame hung).
# So every file's size is checked against the counts before a resume.

import json
import os
import shutil

ORDNER = "zwischenstand"
INFO = "info.json"
FORMAT = 1
PRO_SCHREIBEN = 2 ** 21     # particles per read or write, as FLIP Fluids

# (file, bytes per particle)
WASSER = (("marker_position", 12), ("marker_velocity", 12))
AFFIN = (("marker_affinex", 12), ("marker_affiney", 12), ("marker_affinez", 12))
STUFE = (("marker_level", 1),)
GISCHT = (("diffuse_position", 12), ("diffuse_velocity", 12), ("diffuse_lifetime", 4),
          ("diffuse_type", 1), ("diffuse_id", 1))


def _ordner(cache):
    return os.path.join(cache, ORDNER)


def _ordner_sichern(pfad):
    """fsync a folder, so a rename in it is on the disk as well."""
    try:
        fd = os.open(pfad, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _schreiben(ordner, anzahl, quellen):
    dateien = [open(os.path.join(ordner, name + ".data"), "wb") for name, _ in quellen]
    try:
        for start in range(0, anzahl, PRO_SCHREIBEN):
            ende = min(start + PRO_SCHREIBEN, anzahl)
            for datei, (_, holen) in zip(dateien, quellen):
                datei.write(holen(start, ende))
        for datei in dateien:
            datei.flush()
            os.fsync(datei.fileno())
    finally:
        for datei in dateien:
            datei.close()


def schreiben(sim, cache, frame, frame_start, gitter, dx, versatz, dauer):
    """Save the state after `frame`. OSError (disk full) leaves the old state."""
    neu = _ordner(cache) + "_neu"
    shutil.rmtree(neu, ignore_errors=True)
    os.makedirs(neu)
    try:
        n = sim.get_num_marker_particles()
        apic = sim.is_velocity_transfer_method_APIC()
        stufen = sim.has_marker_particle_level_data()
        quellen = [("marker_position", sim.get_marker_particle_position_data_range),
                   ("marker_velocity", sim.get_marker_particle_velocity_data_range)]
        if apic:
            quellen += [("marker_affinex", sim.get_marker_particle_affinex_data_range),
                        ("marker_affiney", sim.get_marker_particle_affiney_data_range),
                        ("marker_affinez", sim.get_marker_particle_affinez_data_range)]
        if stufen:
            quellen.append(("marker_level", sim.get_marker_particle_level_data_range))
        _schreiben(neu, n, quellen)

        g = sim.get_num_diffuse_particles()
        _schreiben(neu, g, [("diffuse_position", sim.get_diffuse_particle_position_data_range),
                            ("diffuse_velocity", sim.get_diffuse_particle_velocity_data_range),
                            ("diffuse_lifetime", sim.get_diffuse_particle_lifetime_data_range),
                            ("diffuse_type", sim.get_diffuse_particle_type_data_range),
                            ("diffuse_id", sim.get_diffuse_particle_id_data_range)])
        zeiten = sim.get_adaptive_particle_times() if stufen else None
        info = {"format": FORMAT, "frame": frame, "frame_start": frame_start,
                "engine_frame": sim.get_current_frame(), "gitter": list(gitter), "dx": dx,
                "versatz": list(versatz), "wasser": n, "gischt": g, "apic": apic,
                "stufen": stufen, "hybrid_zeiten": zeiten, "dauer": dauer}
        with open(os.path.join(neu, INFO), "w") as f:
            json.dump(info, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        _ordner_sichern(neu)
    except Exception:
        shutil.rmtree(neu, ignore_errors=True)
        raise

    ziel = _ordner(cache)
    alt = ziel + "_alt"
    shutil.rmtree(alt, ignore_errors=True)
    if os.path.isdir(ziel):
        os.rename(ziel, alt)
    os.rename(neu, ziel)
    _ordner_sichern(cache)
    shutil.rmtree(alt, ignore_errors=True)


def _erwartet(stand):
    """(file, size in bytes) the counts in info.json call for."""
    n, g = stand["wasser"], stand["gischt"]
    dateien = [(name, n * b) for name, b in WASSER]
    if stand["apic"]:
        dateien += [(name, n * b) for name, b in AFFIN]
    if stand["stufen"]:
        dateien += [(name, n * b) for name, b in STUFE]
    return dateien + [(name, g * b) for name, b in GISCHT]


def unvollstaendig(stand):
    """The first data file whose size does not match info.json, or None."""
    for name, soll in _erwartet(stand):
        try:
            ist = os.path.getsize(os.path.join(stand["pfad"], name + ".data"))
        except OSError:
            ist = -1
        if ist != soll:
            return name + ".data"
    return None


def _lesen_aus(ordner):
    pfad = os.path.join(ordner, INFO)
    if not os.path.isfile(pfad):
        return None
    try:
        with open(pfad) as f:
            stand = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(stand, dict) or stand.get("format") != FORMAT:
        return None
    stand["pfad"] = ordner
    return stand


def lesen(cache):
    """The info of the saved state, or None. A crash between the two renames
    of schreiben() leaves only <ordner>_neu and <ordner>_alt: then the newer
    complete one of those."""
    if not cache:
        return None
    ziel = _ordner(cache)
    stand = _lesen_aus(ziel)
    if stand is None and not os.path.isdir(ziel):
        for ordner in (ziel + "_neu", ziel + "_alt"):
            ersatz = _lesen_aus(ordner)
            try:
                if ersatz is not None and unvollstaendig(ersatz) is None:
                    return ersatz
            except (KeyError, TypeError):
                pass        # counts missing: the panel calls this while drawing
    return stand


def groesse(stand):
    """Bytes the saved state takes on disk."""
    summe = 0
    try:
        namen = os.listdir(stand["pfad"])
    except OSError:
        return 0
    for name in namen:
        try:
            summe += os.path.getsize(os.path.join(stand["pfad"], name))
        except OSError:
            pass
    return summe


def warum_nicht(stand, masse, versatz, frame_start, frame_end):
    """Why this state cannot go on in the scene as it is now, or None."""
    if stand is None:
        return "No saved state in this cache"
    try:
        kaputt = unvollstaendig(stand)
    except (KeyError, TypeError):
        kaputt = INFO
    if kaputt:
        return f"Saved state incomplete ({kaputt}): bake again"
    if masse is None or list(masse[:3]) != stand["gitter"] \
            or abs(masse[3] - stand["dx"]) > 1e-6 * stand["dx"] \
            or any(abs(a - b) > 1e-3 * stand["dx"] for a, b in zip(versatz, stand["versatz"])):
        return "Domain or Resolution changed since the saved state: bake again"
    if frame_start != stand["frame_start"]:
        return "Start frame changed since the saved state: bake again"
    if frame_end <= stand["frame"]:
        return f"Already baked to frame {stand['frame']}: raise End to go on"
    return None


def _laden_datei(ordner, name):
    return open(os.path.join(ordner, name + ".data"), "rb")


def _lies(datei, laenge):
    """Exactly laenge bytes: the engine takes the count, not the buffer
    length, and would read past a shorter buffer."""
    daten = datei.read(laenge)
    if len(daten) != laenge:
        raise ValueError(f"saved state truncated: {os.path.basename(datei.name)}")
    return daten


def laden(sim, stand, gischt=True):
    """Queue the saved particles in the engine, before sim.initialize().
    gischt=False leaves the whitewater out: with whitewater off the engine
    never moves it again, yet every later state would save it."""
    ordner = stand["pfad"]
    n = stand["wasser"]
    with _laden_datei(ordner, WASSER[0][0]) as pos, _laden_datei(ordner, WASSER[1][0]) as vel:
        affin = [_laden_datei(ordner, name) for name, _ in AFFIN] if stand["apic"] else []
        stufe = _laden_datei(ordner, STUFE[0][0]) if stand["stufen"] else None
        try:
            for start in range(0, n, PRO_SCHREIBEN):
                zahl = min(PRO_SCHREIBEN, n - start)
                sim.load_marker_particle_data(zahl, _lies(pos, zahl * 12), _lies(vel, zahl * 12))
                if affin:
                    sim.load_marker_particle_affine_data(zahl, *(_lies(d, zahl * 12) for d in affin))
                if stufe is not None:
                    sim.load_marker_particle_level_data(zahl, _lies(stufe, zahl))
        finally:
            for datei in affin + ([stufe] if stufe is not None else []):
                datei.close()

    g = stand["gischt"] if gischt else 0
    if g:
        dateien = [_laden_datei(ordner, name) for name, _ in GISCHT]
        try:
            for start in range(0, g, PRO_SCHREIBEN):
                zahl = min(PRO_SCHREIBEN, g - start)
                sim.load_diffuse_particle_data(zahl, *(_lies(d, zahl * b)
                                                       for d, (_, b) in zip(dateien, GISCHT)))
        finally:
            for datei in dateien:
                datei.close()

    sim.set_current_frame(stand["engine_frame"])
    if stand.get("hybrid_zeiten"):
        sim.set_adaptive_particle_times(*stand["hybrid_zeiten"])
