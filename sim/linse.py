# HyprW — drops on the camera lens
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Whatever flies onto the camera stays on the glass as drops. Small drops
# stick, large ones slowly slide down and collect smaller ones on the way
# — like on a window pane. Computed after baking from the cache, frame by
# frame, and stored as files of their own (linse/NNNNNN.npy), so scrubbing
# stays fast.
#
# PHYSICS (sources in engine_patch/README.md, section on lens drops):
#   Sticking Quere 1998 (Langmuir 14, 2213), eq. 3, measured for water on glass:
#            sticks as long as  pi r gamma (cos th_r - cos th_a) >= rho V g sin(a)
#            r = contact radius, a = tilt of the glass against the horizontal
#   Shape    spherical cap with contact angle th (Quere 1998, eq. 1)
#   Sliding  Le Grand, Daerr & Limat 2005 (JFM 541): Ca = c (Bo_a - Bo_c),
#            Ca = eta U / gamma, Bo_a = V^(2/3) rho g sin(a) / gamma.
#            Bo_c follows from the same sticking condition. c = 0.00666 (fig. 7,
#            silicone oil 10 cP — there is no cleanly published value for
#            water; the law itself also holds for water, Varagnolo et al.
#            2013, PRL 111)
#   Glasses  Quere 1998, table 1 (water on float glass):
#            uncoated th_a 60 / th_r 44.
#            Fluorine-coated lens: static contact angle 115 degrees and water
#            sliding angle 16 degrees (Daikin OPTOOL DSX-F datasheet, a common
#            lens anti-smudge coating); hysteresis of such coatings 11..15
#            degrees (US patent 11061171). Taken: 115 +- 5.5 -> th_a 120.5 /
#            th_r 109.5, the low end of the range — with it a ~29 uL drop
#            slides at 16 degrees, the closest match to the datasheet (which
#            does not state its drop volume). APPROXIMATION within that range.
#            Formerly Quere's fluorinated float glass, 115/100, which held
#            drops about twice as strongly.
#
#   Impact   Splashes from K = We^0.5 Re^0.25 > 57.7 (Mundo, Sommerfeld & Tropea
#            1995, IJMF 21:151). Spreads to D_max, Laan et al. 2014
#            (Phys. Rev. Appl. 2, 044018), eq. 3:
#            (D_max/D0) Re^-1/5 = P^1/2 / (1.24 + P^1/2), P = We Re^-2/5.
#            For the spray here (~1 mm, ~3 m/s) D_max is ~3.6 D0, far more
#            than the footprint of the resting drop (~1.8 D0): so the edge
#            recedes until it reaches the receding angle th_r (contact angle
#            hysteresis, de Gennes 1985, Rev. Mod. Phys. 57:827): a resting
#            drop sits at th_r, not at the mean, and so holds on with a
#            wider contact line.
#
#   Mist     A mist particle is a cloud, not a drop. Its water follows from
#            the calibrated optics (sim/aussehen.py): extinction
#            sigma = n Q pi d^2/4 with Q = 2 for drops >> light wavelength
#            (van de Hulst 1957, "extinction paradox") -> water content
#            LWC = n pi d^3/6 = sigma d / 3. Deposited per frame:
#            LWC * U * A * eta * dt, eta after Langmuir & Blodgett:
#            eta = St / (St + pi/2), St = rho_w d^2 U / (18 mu_air R),
#            R = half the lens width. The formula holds for a cylinder —
#            for the camera front an APPROXIMATION. Fine droplets stay put
#            (far below the sliding limit); a sliding drop picks up those it
#            runs over and leaves a clear track.
#            Touching droplets merge like drops; above a width (slider,
#            purely a drawing choice) they become normal drops.
#
#   Trails   Fast drops taper at the back and leave droplets behind, from
#            Ca = eta U / gamma = 7.2e-3 (Le Grand, Daerr & Limat 2005,
#            fig. 4: corner 5e-3, cusp 7.1e-3, pearls 7.2e-3; measured on
#            silicone oil — for water an APPROXIMATION, ~0.5 m/s). The size
#            of the pearls is not quantified there ("grows with speed"):
#            APPROXIMATION via slider linse_spur_groesse, spacing two pearl
#            diameters. Sliding drops pick up mist along their whole path,
#            not only at the end.
#
# NOT included: evaporation.
# APPROXIMATION: which share of a splashing drop stays on the glass was not
# found measured -> slider linse_spritz_rest.
#
# CONTROLS (domain properties): linse_an off = no computation. Per type
# (water, spray, mist) hits only count if linse_<art>_haftet is on, and of
# those only linse_<art>_haftanteil percent. Which hits those are is decided
# by a fixed random number per hit (_zufall): same settings, same result,
# and the types do not affect each other.

import math
import os

import numpy as np
import bpy
from mathutils import Vector

from . import cache, geometrie

RHO = 998.0          # kg/m^3
GAMMA = 0.0728       # N/m
ETA = 1.0e-3         # Pa s
G = 9.81             # m/s^2
LEGRAND_C = 0.00666
CA_PERLEN = 7.2e-3   # Le Grand et al. 2005, fig. 4e
ETA_LUFT = 1.81e-5   # Pa s, air at 20 °C
NEBEL_HOECHST = 20000  # fine droplets; more gets too expensive to draw
GLAESER = {'GLAS': (60.0, 44.0), 'FLUOR': (120.5, 109.5)}


def winkel(p):
    """Advancing and receding angle (radians) of the configured lens."""
    if p.linse_glas == 'EIGEN':
        vor = p.linse_winkel_vor
        return math.radians(vor), math.radians(min(p.linse_winkel_rueck, vor - 0.1))
    return tuple(math.radians(w) for w in GLAESER[p.linse_glas])
ORDNER = "linse"
OBJEKT = "HK_Linsentropfen"
HOECHSTZAHL = 6000   # drops on the glass; more gets too expensive to draw

# Particle types in the cache (engine): 2 spray, 3 mist
SPRAY, NEBEL = 2, 3
WASSER = 100         # not a cache type, only a key for _zufall
SAAT = 1234


# ----------------------------------------------------------------- Selection
def _mischen(h):
    """splitmix64 finaliser: spreads similar inputs evenly."""
    h = h + np.uint64(0x9E3779B97F4A7C15)
    h = (h ^ (h >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
    h = (h ^ (h >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
    return h ^ (h >> np.uint64(31))


def _zufall(*schluessel):
    """A fixed number in [0, 1) per combination of integer keys — the same
    keys always give the same number, whatever else happens in the frame.
    Not a running random stream: otherwise switching off one type would
    shift the selection of the others."""
    teile = [np.ascontiguousarray(s, np.int64) for s in schluessel]
    form = np.broadcast_shapes(*(t.shape for t in teile))
    h = np.full(form, SAAT, np.uint64)
    for t in teile:
        h = _mischen(h ^ np.broadcast_to(t.view(np.uint64), form))
    return (h >> np.uint64(11)).astype(np.float64) / float(1 << 53)


def _haftet(prozent, *schluessel):
    """Mask of the hits that stick: the share `prozent` (0..100). A higher
    share keeps all hits of the lower one and adds more."""
    return _zufall(*schluessel) < prozent / 100.0


# --------------------------------------------------------------- Geometry
def _kappe(V, theta):
    """Contact radius r and height h of a spherical cap with volume V (Quere eq. 1)."""
    c = math.cos(theta)
    R = (3.0 * V / (math.pi * (2.0 + c) * (1.0 - c) ** 2)) ** (1.0 / 3.0)
    return R * math.sin(theta), R * (1.0 - c)


def _kappe_np(V, theta):
    c = math.cos(theta)
    R = np.cbrt(3.0 * V / (math.pi * (2.0 + c) * (1.0 - c) ** 2))
    return R * math.sin(theta), R * (1.0 - c)


def kritisches_volumen(th_a, th_r, sin_a):
    """Volume from which a drop slides (Quere eq. 3 solved for V)."""
    if sin_a < 1e-6:
        return float("inf")
    # pi r gamma dcos = rho V g sin_a, r = k V^(1/3) with k from the cap shape
    # of the resting drop: after receding it sits at th_r
    k, _ = _kappe(1.0, th_r)
    dcos = math.cos(th_r) - math.cos(th_a)
    return (math.pi * k * GAMMA * dcos / (RHO * G * sin_a)) ** 1.5


def _gamma_durchmesser(d50, dmin, dmax, kennung):
    """Diameter per ID as in the engine: Gamma(2, 0.5 d50), quantile
    (id + 0.5)/256 — so a drop on the lens is as large as the particle
    that brought it."""
    u = (np.arange(256) + 0.5) / 256.0
    lo = np.zeros(256); hi = np.full(256, 50.0)
    for _ in range(60):
        x = 0.5 * (lo + hi)
        F = 1.0 - np.exp(-x) * (1.0 + x)
        lo = np.where(F < u, x, lo); hi = np.where(F < u, hi, x)
    tabelle = np.clip(0.5 * (lo + hi) * 0.5 * d50, dmin, dmax)
    return tabelle[kennung]


# ----------------------------------------------------------------- Camera
def _kamera_daten(scene, cam):
    """World->camera matrix and half image width/height as tangent."""
    rd = scene.render
    breite = rd.resolution_x * rd.pixel_aspect_x
    hoehe = rd.resolution_y * rd.pixel_aspect_y
    seiten = breite / max(hoehe, 1e-6)
    daten = cam.data
    fit = daten.sensor_fit
    if fit == 'AUTO':
        fit = 'HORIZONTAL' if seiten >= 1.0 else 'VERTICAL'
    if fit == 'HORIZONTAL':
        t_w = daten.sensor_width / (2.0 * daten.lens)
        t_h = t_w / seiten
    else:
        t_h = daten.sensor_height / (2.0 * daten.lens)
        t_w = t_h * seiten
    return np.array(cam.matrix_world.inverted(), np.float64), t_w, t_h, seiten


def _kamera_bewegt(cam):
    """True if the camera can change between frames: animation or drivers
    on it, its lens data or a parent, or any constraint (its target may move)."""
    def animiert(id_):
        ad = id_.animation_data if id_ is not None else None
        return bool(ad and (ad.action or len(ad.drivers) or len(ad.nla_tracks)))
    if animiert(cam.data):
        return True
    o = cam
    while o is not None:
        if animiert(o) or len(o.constraints):
            return True
        o = o.parent
    return False


def _in_kamera(M, p):
    return p @ M[:3, :3].T + M[:3, 3]


# ------------------------------------------------------------ Computation
def berechnen(scene, melden=None):
    """Compute and store the drops for all baked frames in one go (scripts,
    background mode). Returns (frames, drops in the last frame)."""
    schritte = berechnen_schritte(scene)
    try:
        while True:
            f, n, _ = next(schritte)
            if melden:
                melden(f, n)
    except StopIteration as fertig:
        return fertig.value or (0, 0)


def ist_aktuell(scene):
    """True if lens drops exist and are newer than the bake."""
    domain = geometrie.finde_domain(scene)
    if domain is None:
        return False
    pfad = cache.pfad_aufloesen(domain.hydrokit.cache_pfad)
    info = os.path.join(pfad, "info.json")
    erste = os.path.join(pfad, ORDNER, f"{scene.frame_start:06d}.npy")
    return (os.path.exists(info) and os.path.exists(erste)
            and os.path.getmtime(erste) >= os.path.getmtime(info))


def berechnen_schritte(scene):
    """The same as berechnen(), one frame per step: yields (frame, drops,
    number of frames), so a modal operator can spread the work over many
    UI updates — computing everything at once froze Blender for a minute
    on a 300-frame scene."""
    from . import anzeige
    domain = geometrie.finde_domain(scene)
    cam = scene.camera
    if domain is None or cam is None or cam.type != 'CAMERA':
        return 0, 0
    p = domain.hydrokit
    if not p.linse_an:
        return 0, 0
    tiefenschaerfe_einrichten(cam, domain)
    pfad = cache.pfad_aufloesen(p.cache_pfad)
    info = cache.info_lesen(pfad)
    if info is None:
        return 0, 0
    frames = sorted(cache.gebackene_frames(pfad))
    if not frames:
        return 0, 0
    ziel = os.path.join(pfad, ORDNER)
    os.makedirs(ziel, exist_ok=True)
    for name in os.listdir(ziel):
        if name.endswith(".npy"):
            os.remove(os.path.join(ziel, name))

    versatz = np.asarray(info.get("versatz", [0, 0, 0]), np.float64)
    th_a, th_r = winkel(p)
    theta = 0.5 * (th_a + th_r)
    dcos = math.cos(th_r) - math.cos(th_a)
    D = p.linse_durchmesser / 1000.0          # m, covers the image width
    fang = p.linse_fangradius
    dt = 1.0 / (scene.render.fps / scene.render.fps_base)

    # Drops on the glass: x, y in metres (image centre = 0), volume in m^3
    tx = np.zeros(0); ty = np.zeros(0); tv = np.zeros(0)
    ts = np.zeros(0)       # elongation of the outline along downhill (1 = round)
    nx_n = np.zeros(0); ny_n = np.zeros(0); nv_n = np.zeros(0)   # mist droplets

    # Mist cloud: water per particle from display radius and density
    from . import aussehen
    masse = geometrie.gitter_masse(domain, p.aufloesung)
    voxel = masse[3] if masse else 0.03
    r_teil = voxel * aussehen.NEBEL_RADIUS_VOXEL
    r_wolke = voxel * (aussehen.NEBEL_RADIUS_VOXEL + aussehen.WEICH_SCHRITTE)
    sigma = aussehen.NEBEL_OPTISCHE_TIEFE / r_teil * p.nebel_dichte_anzeige / 2.0
    d_nebel = p.nebel_groesse / 1000.0
    wasser_teil = sigma * d_nebel / 3.0 * (4.0 / 3.0) * math.pi * r_teil ** 3
    nebel_d = _gamma_durchmesser(d_nebel, 40e-6, 400e-6, np.arange(256))
    nebel_v_mittel = float((math.pi / 6.0 * nebel_d ** 3).mean())

    alt_frame = scene.frame_current
    # frame_set makes FLIP Fluids load its mesh (~150 ms per frame); the drops
    # only need the camera, so a camera that cannot move skips it
    bewegt = _kamera_bewegt(cam)
    M, t_w, t_h, seiten = _kamera_daten(scene, cam)
    anzeige.handler_aus()          # frame changes without cache loading: fast
    try:
        for f in frames:
            if bewegt:
                scene.frame_set(f)
                M, t_w, t_h, seiten = _kamera_daten(scene, cam)
            # Gravity in camera coordinates -> direction and tilt on the glass
            g_cam = M[:3, :3] @ np.array([0.0, 0.0, -1.0])
            g2 = g_cam[:2]
            sin_a = float(np.hypot(*g2))
            richtung = g2 / sin_a if sin_a > 1e-6 else np.array([0.0, -1.0])
            v_krit = kritisches_volumen(th_a, th_r, sin_a)

            neu_x, neu_y, neu_v, neu_m = [], [], [], []

            def treffer(pos, vel):
                """Particles that enter the capture sphere around the camera in
                this frame, fly towards it and lie in the image."""
                pc = _in_kamera(M, pos)
                vc = vel @ M[:3, :3].T
                abstand = np.linalg.norm(pc, axis=1)
                vorher = np.linalg.norm(pc - vc * dt, axis=1)
                vorne = pc[:, 2] < -1e-4
                nx = np.where(vorne, pc[:, 0] / np.maximum(-pc[:, 2], 1e-6) / t_w, 9.0)
                ny = np.where(vorne, pc[:, 1] / np.maximum(-pc[:, 2], 1e-6) / t_h, 9.0)
                zu = (vc * -pc).sum(axis=1) / np.maximum(abstand, 1e-9)
                m = ((abstand < fang) & (vorher >= fang) & (zu > 0.0)
                     & (np.abs(nx) <= 1.0) & (np.abs(ny) <= 1.0))
                return m, nx, ny, zu

            # --- Spray and mist
            roh = cache.partikel_lesen(pfad, f)
            if roh is not None and len(roh[3]):
                pos = np.frombuffer(roh[0], np.float32).reshape(-1, 3).astype(np.float64) + versatz
                vel = np.frombuffer(roh[1], np.float32).reshape(-1, 3).astype(np.float64)
                ken = np.frombuffer(roh[3], np.uint8)
                art = np.frombuffer(roh[4], np.uint8)
                m, nx, ny, zu = treffer(pos, vel)
                for code, haftet, prozent, d50, dmin, dmax in (
                        (SPRAY, p.linse_spray_haftet, p.linse_spray_haftanteil,
                         p.spray_groesse / 1000.0, 0.125e-3, 8.5e-3),):
                    if not haftet:
                        continue
                    sel = m & (art == code)
                    # Key: type, particle number in the frame, frame. Not the
                    # ID: it has only 256 values and sets the diameter, so all
                    # hits of the same size in a frame would otherwise have
                    # drawn the same lot.
                    idx = np.flatnonzero(sel)
                    sel[idx] = _haftet(prozent, code, idx, f)
                    if sel.any():
                        d = _gamma_durchmesser(d50, dmin, dmax, ken[sel])
                        v = np.maximum(zu[sel], 1e-3)
                        we = RHO * v * v * d / GAMMA
                        re = RHO * v * d / ETA
                        vol = math.pi / 6.0 * d ** 3
                        # Splashing (Mundo 1995): only a remainder stays
                        spritzt = p.linse_spritzen & (np.sqrt(we) * re ** 0.25
                                                      > p.linse_spritz_schwelle)
                        vol = np.where(spritzt, vol * p.linse_spritz_rest / 100.0, vol)
                        # On impact it briefly spreads to D_max (Laan et
                        # al. 2014, eq. 3, valid 10 < We < 1700, 70 < Re < 17000)
                        # and merges with everything it touches
                        P = we * re ** -0.4
                        dmax = d * re ** 0.2 * np.sqrt(P) / (1.24 + np.sqrt(P))
                        gilt = (we > 10.0) & (re > 70.0)
                        neu_x.append(nx[sel] * D / 2.0)
                        neu_y.append(ny[sel] * D / 2.0 / seiten)
                        neu_v.append(vol)
                        neu_m.append(np.where(gilt & p.linse_ausbreiten,
                                              0.5 * dmax * p.linse_ausbreit_faktor, 0.0))

                # Mist: clouds enveloping the camera deposit droplets
                mn = art == NEBEL
                if p.linse_nebel_haftet and mn.any():
                    cam_pos = -M[:3, :3].T @ M[:3, 3]
                    nah = np.linalg.norm(pos[mn] - cam_pos, axis=1) < r_wolke
                    if nah.any():
                        lwc = nah.sum() * wasser_teil / (4.0 / 3.0 * math.pi * r_wolke ** 3)
                        U = float(np.linalg.norm(vel[mn][nah], axis=1).mean())
                        st = RHO * d_nebel ** 2 * U / (18.0 * ETA_LUFT * D / 2.0)
                        eta = st / (st + math.pi / 2.0) if p.linse_nebel_fang else 1.0
                        menge = (lwc * U * (D * D / seiten) * eta * dt
                                 * p.linse_nebel_haftanteil / 100.0 * p.linse_nebel_menge)
                        n_neu = int(menge / nebel_v_mittel + _zufall(NEBEL, f, 1)[()])
                        if n_neu:
                            i = np.arange(n_neu)
                            kn = np.minimum((_zufall(19, f, i) * 256).astype(np.int64), 255)
                            nx_n = np.r_[nx_n, (_zufall(13, f, i) - 0.5) * D]
                            ny_n = np.r_[ny_n, (_zufall(17, f, i) - 0.5) * D / seiten]
                            nv_n = np.r_[nv_n, math.pi / 6.0 * nebel_d[kn] ** 3]
                            # Touching droplets merge (like the drops);
                            # grown ones become normal drops that are drawn
                            # in detail and can slide
                            if p.linse_nebel_verschmelzen:
                                nx_n, ny_n, nv_n = _nebel_verschmelzen(nx_n, ny_n, nv_n, th_r)
                                r_n, _ = _kappe_np(nv_n, th_r)
                                gross = 2.0 * r_n > p.linse_nebel_zu_tropfen / 1000.0
                                if gross.any():
                                    neu_x.append(nx_n[gross]); neu_y.append(ny_n[gross])
                                    neu_v.append(nv_n[gross]); neu_m.append(np.zeros(int(gross.sum())))
                                    nx_n, ny_n, nv_n = nx_n[~gross], ny_n[~gross], nv_n[~gross]
                            grenze = p.linse_max_nebel        # drawing limit, not physics
                            if len(nv_n) > grenze:
                                nx_n, ny_n, nv_n = (a[len(a) - grenze:] for a in (nx_n, ny_n, nv_n))

            # --- Water: surface vertices that reach the camera.
            # Per 2 mm cell of the glass that the surface reaches,
            # one drop of 2 mm diameter — a modelling choice (the surface
            # has no drop size). Earlier every drop sat exactly at the
            # sliding limit, the slightest growth made it run off and the
            # glass was wiped clean.
            if p.linse_wasser_haftet:
                mesh = cache.mesh_lesen_np(pfad, f)
                if mesh is not None and len(mesh[0]):
                    pos = mesh[0].reshape(-1, 3).astype(np.float64) + versatz
                    pc = _in_kamera(M, pos)
                    abstand = np.linalg.norm(pc, axis=1)
                    vorne = pc[:, 2] < -1e-4
                    nx = np.where(vorne, pc[:, 0] / np.maximum(-pc[:, 2], 1e-6) / t_w, 9.0)
                    ny = np.where(vorne, pc[:, 1] / np.maximum(-pc[:, 2], 1e-6) / t_h, 9.0)
                    sel = (abstand < fang) & (np.abs(nx) <= 1) & (np.abs(ny) <= 1)
                    if sel.any():
                        x = nx[sel] * D / 2.0; y = ny[sel] * D / 2.0 / seiten
                        zelle = p.linse_wasser_groesse / 1000.0
                        zellen = np.unique(np.round(np.c_[x, y] / zelle).astype(np.int64), axis=0)
                        zellen = zellen[_haftet(p.linse_wasser_haftanteil, WASSER,
                                                zellen[:, 0], zellen[:, 1], f)]
                        vol = math.pi / 6.0 * zelle ** 3
                        neu_x.append(zellen[:, 0] * zelle)
                        neu_y.append(zellen[:, 1] * zelle)
                        neu_v.append(np.full(len(zellen), vol))
                        neu_m.append(np.zeros(len(zellen)))

            if neu_x:
                tx = np.concatenate([tx] + neu_x)
                ty = np.concatenate([ty] + neu_y)
                tv = np.concatenate([tv] + neu_v)
                ts = np.r_[ts, np.ones(len(tv) - len(ts))]
            # Spreading radius on impact, only in this frame (0 = at rest)
            aufprall = np.r_[np.zeros(len(tv) - sum(len(a) for a in neu_m)),
                             np.concatenate(neu_m)] if neu_m else np.zeros(len(tv))

            # --- Sliding (Quere adhesion, Le Grand speed)
            wege = []
            if len(tv) and sin_a > 1e-6 and p.linse_rutschen:
                r, _ = _kappe_np(tv, th_r)
                halt = math.pi * r * GAMMA * dcos * p.linse_haft_faktor
                gewicht = RHO * tv * G * sin_a
                rutscht = gewicht > halt
                if rutscht.any():
                    v13 = np.cbrt(tv[rutscht])
                    bo = v13 * v13 * RHO * G * sin_a / GAMMA
                    bo_c = halt[rutscht] / (GAMMA * v13)
                    U = GAMMA / ETA * LEGRAND_C * p.linse_tempo_faktor * np.maximum(bo - bo_c, 0.0)
                    idx = np.flatnonzero(rutscht)
                    alt_x, alt_y = tx[idx].copy(), ty[idx].copy()
                    tx[rutscht] += richtung[0] * U * dt
                    ty[rutscht] += richtung[1] * U * dt
                    wege.append((alt_x, alt_y, tx[idx].copy(), ty[idx].copy(), r[idx]))

                    # Pearls behind fast drops (Le Grand 2005)
                    ca_perlen = p.linse_perlen_schwelle * 1e-3
                    perlt = ETA * U / GAMMA > ca_perlen
                    if perlt.any() and p.linse_spuren and p.linse_spur_groesse > 0.0:
                        px, py, pv, pa = [], [], [], []
                        for k in np.flatnonzero(perlt):
                            i = idx[k]
                            # Size grows with speed (Le Grand 2005, qualitative;
                            # assumed linear), break-up like a dripping tap: pearls
                            # vary in size, often with a small satellite. Spacing
                            # random (defects in the glass), two diameters on average,
                            # position wobbles sideways around the tail tip. All APPROXIMATION.
                            d0 = (p.linse_spur_groesse / 100.0 * 2.0 * r[i]
                                  * min(ETA * U[k] / GAMMA / ca_perlen, 3.0))
                            weg = U[k] * dt
                            hx = alt_x[k] - richtung[0] * r[i]; hy = alt_y[k] - richtung[1] * r[i]
                            quer = np.array([-richtung[1], richtung[0]])
                            rest = 0.5 * tv[i]          # it gives off at most half
                            g_str = p.linse_spur_streuung / 100.0
                            a_mit = p.linse_spur_abstand
                            a_str = p.linse_spur_abstand_streuung / 100.0
                            sat = p.linse_spur_satelliten / 100.0
                            sat_g = p.linse_spur_satellit_groesse / 100.0
                            st_lo = p.linse_spur_streckung_min
                            st_hi = max(p.linse_spur_streckung_max, st_lo)
                            s_p = d0 * _zufall(29, f, i, 0)[()]
                            for j in range(1, 200):
                                dp = d0 * (1.0 + g_str * (2.0 * _zufall(31, f, i, j)[()] - 1.0))
                                vp = math.pi / 6.0 * dp ** 3
                                rp, _ = _kappe(vp, th_r)
                                # only behind the new rear edge, otherwise it merges at once
                                if s_p > weg - rp * math.sqrt(3.0) or vp > rest:
                                    break
                                seit = (_zufall(37, f, i, j)[()] - 0.5) * p.linse_spur_wackeln * r[i]
                                teile = [(dp, vp, 0.0)]
                                if _zufall(41, f, i, j)[()] < sat:          # satellite
                                    ds = dp * sat_g * (2.0 / 3.0 + 2.0 / 3.0 * _zufall(43, f, i, j)[()])
                                    teile.append((ds, math.pi / 6.0 * ds ** 3, -1.2 * dp))
                                for dd, vv, versatz_s in teile:
                                    px.append([hx + richtung[0] * (s_p + versatz_s) + quer[0] * seit])
                                    py.append([hy + richtung[1] * (s_p + versatz_s) + quer[1] * seit])
                                    pv.append([vv])
                                    # Trail droplets are elongated: remnants of the tail,
                                    # held back while the drop moves on.
                                    # Aspect ratio along the running direction
                                    # (APPROXIMATION, no measurements found)
                                    pa.append([st_lo + (st_hi - st_lo) * _zufall(53, f, i, j, len(pa))[()]])
                                    tv[i] -= vv; rest -= vv
                                s_p += dp * a_mit * (1.0 + a_str * (2.0 * _zufall(47, f, i, j)[()] - 1.0))
                        if px:
                            flach = lambda liste: np.concatenate([np.ravel(a) for a in liste])
                            tx = np.r_[tx, flach(px)]
                            ty = np.r_[ty, flach(py)]
                            tv = np.r_[tv, flach(pv)]
                            ts = np.r_[ts, flach(pa)]

            # --- Merging: drops whose contact areas touch
            if len(tv) > 1:
                aufprall = np.r_[aufprall, np.zeros(len(tv) - len(aufprall))]
                tx, ty, tv, ts = _verschmelzen(tx, ty, tv, ts, th_r, richtung,
                                               p.linse_unregelmaessig, aufprall)

            # --- Drops pick up the mist droplets beneath them
            if len(nv_n) and len(tv):
                rr, _ = _kappe_np(tv, th_r)
                # Each droplet goes to the first drop (lowest number) above it
                i, j = _paare_in_reichweite(tx, ty, rr, nx_n, ny_n)
                erster = np.full(len(nv_n), len(tv))
                np.minimum.at(erster, j, i)
                weg = erster < len(tv)
                tv = tv + np.bincount(erster[weg], weights=nv_n[weg], minlength=len(tv))
                # Along the path of sliding drops: a clear track. The water
                # of these droplets (~10 nl each) is no longer counted.
                for ax, ay, bx, by, rw in (wege if p.linse_spur_wischen else []):
                    if not len(ax):
                        continue
                    stuecke = (np.hypot(bx - ax, by - ay) / np.maximum(rw, 1e-5)).astype(np.int64) + 2
                    k = np.repeat(np.arange(len(ax)), stuecke)
                    t = (np.arange(stuecke.sum()) - np.repeat(np.cumsum(stuecke) - stuecke, stuecke)) \
                        / np.repeat(stuecke - 1, stuecke)
                    _, j = _paare_in_reichweite(ax[k] + t * (bx[k] - ax[k]), ay[k] + t * (by[k] - ay[k]),
                                                rw[k], nx_n, ny_n)
                    weg[j] = True
                if weg.any():
                    nx_n, ny_n, nv_n = nx_n[~weg], ny_n[~weg], nv_n[~weg]

            # --- Whatever leaves the glass is gone
            innen = (np.abs(tx) <= D / 2.0) & (np.abs(ty) <= D / 2.0 / seiten)
            tx, ty, tv, ts = tx[innen], ty[innen], tv[innen], ts[innen]
            if len(tv) > p.linse_max_tropfen:
                keep = np.argsort(tv)[-p.linse_max_tropfen:]
                tx, ty, tv, ts = tx[keep], ty[keep], tv[keep], ts[keep]

            # Output: resting drops at th_r, sliding ones at the mean
            if len(tv):
                r, h = _kappe_np(tv, th_r)
                if sin_a > 1e-6 and p.linse_rutschen:
                    rr, hr = _kappe_np(tv, theta)
                    weg = RHO * tv * G * sin_a > math.pi * r * GAMMA * dcos * p.linse_haft_faktor
                    r = np.where(weg, rr, r); h = np.where(weg, hr, h)
            else:
                r, h = np.zeros(0), np.zeros(0)
            np.save(os.path.join(ziel, f"{f:06d}.npy"),
                    np.c_[tx, ty, r, h, ts].astype(np.float32))
            rn, hn = _kappe_np(nv_n, th_r) if len(nv_n) else (np.zeros(0), np.zeros(0))
            np.save(os.path.join(ziel, f"{f:06d}_nebel.npy"),
                    np.c_[nx_n, ny_n, rn, hn].astype(np.float32))
            yield f, len(tv), len(frames)
    finally:
        scene.frame_set(alt_frame)
        anzeige.handler_an()
    return len(frames), len(tv)


def _beruehrradius(V, theta):
    """How far a drop reaches sideways. Up to 90 degrees that is the contact
    radius; above (a bead on a water-repellent coating) the cap bulges out
    beyond its footprint to the sphere radius R = r / sin(theta), and two
    beads touch — and merge — before their footprints do."""
    r, _ = _kappe_np(V, theta)
    return r / math.sin(theta) if theta > math.pi / 2.0 else r


def _paare_in_reichweite(qx, qy, qr, px, py):
    """All pairs (i, j) where point j lies within qr[i] of query i (distance
    <= qr, like KDTree.find_range) — via a cell grid of the points in numpy
    instead of a tree filled point by point in Python."""
    leer = np.zeros(0, np.int64)
    if not len(qx) or not len(px):
        return leer, leer
    c = max(float(qr.max()), 1e-9)
    schluessel = (np.floor(px / c).astype(np.int64) * 1_000_003
                  + np.floor(py / c).astype(np.int64))
    ordnung = np.argsort(schluessel, kind="stable")
    zellen, start, belegt = np.unique(schluessel[ordnung], return_index=True,
                                      return_counts=True)
    qcx = np.floor(qx / c).astype(np.int64); qcy = np.floor(qy / c).astype(np.int64)
    paare_i, paare_j = [], []
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            ziel = (qcx + dx) * 1_000_003 + (qcy + dy)
            k = np.minimum(np.searchsorted(zellen, ziel), len(zellen) - 1)
            anzahl = np.where(zellen[k] == ziel, belegt[k], 0)
            if not anzahl.any():
                continue
            i = np.repeat(np.arange(len(qx)), anzahl)
            versatz = np.arange(anzahl.sum()) - np.repeat(np.cumsum(anzahl) - anzahl, anzahl)
            j = ordnung[np.repeat(start[k], anzahl) + versatz]
            nah = np.hypot(qx[i] - px[j], qy[i] - py[j]) <= qr[i]
            paare_i.append(i[nah]); paare_j.append(j[nah])
    if not paare_i:
        return leer, leer
    return np.concatenate(paare_i), np.concatenate(paare_j)


def _gruppen(n, i, j):
    """Connected groups of n items joined by the pairs (i, j), via label
    propagation: index of its group per item, groups ordered by their
    lowest member."""
    label = np.arange(n)
    while True:
        m = np.minimum(label[i], label[j])
        neu = label.copy()
        np.minimum.at(neu, i, m); np.minimum.at(neu, j, m)
        neu = neu[neu]                                  # shortcut
        if np.array_equal(neu, label):
            break
        label = neu
    return np.unique(label, return_inverse=True)[1]


def _nebel_verschmelzen(x, y, v, theta):
    """Like _verschmelzen, but for tens of thousands of fine, round droplets:
    neighbours via a cell grid instead of a tree, groups via label
    propagation — all in numpy. Touching = contact circles overlap.
    Repeated until nothing merges any more (at most 6 times); after the
    first pass only droplets that just merged can touch anything new, so
    only those are searched (same result, a fraction of the work)."""
    aktiv = None                     # None = search all droplets
    for _ in range(6):
        n = len(v)
        if n < 2:
            break
        r = _beruehrradius(v, theta)
        c = 2.0 * float(r.max())
        cx = np.floor(x / c).astype(np.int64); cy = np.floor(y / c).astype(np.int64)
        schluessel = cx * 1_000_003 + cy
        ordnung = np.argsort(schluessel, kind="stable")
        # Search in sorted order and per occupied cell, not per droplet: the
        # key is linear in (cx, cy), so the neighbour cells' keys stay sorted.
        # Searching every droplet in random order took 90 ms per frame.
        zellen, start, belegt = np.unique(schluessel[ordnung], return_index=True,
                                          return_counts=True)
        zelle_von = np.repeat(np.arange(len(zellen)), belegt)
        xs, ys, rs = x[ordnung], y[ordnung], r[ordnung]
        if aktiv is None:
            quelle = np.arange(n)
            nachbarn = ((0, 0), (1, -1), (1, 0), (1, 1), (0, 1))  # half neighbourhood
        else:
            quelle = np.flatnonzero(aktiv[ordnung])
            nachbarn = [(a, b) for a in (-1, 0, 1) for b in (-1, 0, 1)]
        paare_i, paare_j = [], []
        for dx, dy in nachbarn:
            ziel = zellen + (dx * 1_000_003 + dy)
            k = np.minimum(np.searchsorted(zellen, ziel), len(zellen) - 1)
            da = zellen[k] == ziel
            zq = zelle_von[quelle]
            anzahl = np.where(da, belegt[k], 0)[zq]
            if not anzahl.any():
                continue
            lo = start[k][zq]
            i = np.repeat(quelle, anzahl)
            versatz = np.arange(anzahl.sum()) - np.repeat(np.cumsum(anzahl) - anzahl, anzahl)
            j = np.repeat(lo, anzahl) + versatz
            if dx == 0 and dy == 0:
                halb = (i < j) if aktiv is None else (i != j)
                i, j = i[halb], j[halb]
            nah = np.hypot(xs[i] - xs[j], ys[i] - ys[j]) < rs[i] + rs[j]
            paare_i.append(ordnung[i[nah]]); paare_j.append(ordnung[j[nah]])
        i = np.concatenate(paare_i) if paare_i else np.zeros(0, np.int64)
        j = np.concatenate(paare_j) if paare_j else np.zeros(0, np.int64)
        if not len(i):
            break
        idx = _gruppen(n, i, j)
        vv = np.bincount(idx, weights=v)
        x = np.bincount(idx, weights=x * v) / vv
        y = np.bincount(idx, weights=y * v) / vv
        v = vv
        aktiv = np.bincount(idx) > 1                        # grew in this pass
    return x, y, v


def _umriss(x, y, streck, unregel, bergab, S):
    """Edge distance per drop in S directions (angle 2 pi k / S on the glass),
    in multiples of the contact radius. _tropfen_netz draws exactly like this
    and _verschmelzen checks like this — otherwise drops visibly touch without
    merging.
      Edge     modes 2..5 with a fixed random phase per drop from its position
               (0.5 mm grid), strength unregel.
      Ellipse  semi-axis sqrt(A) along downhill, 1/sqrt(A) across (streck = A).
    The contact area stays that of the circular cap."""
    n = len(x)
    phi = np.arange(S) * (2.0 * math.pi / S)
    ix = np.round(x / 5e-4).astype(np.int64); iy = np.round(y / 5e-4).astype(np.int64)
    form = np.ones((n, S))
    for m in range(2, 6):
        amp = unregel / m * (0.5 + 0.5 * _zufall(7, m, ix, iy))
        phase = 2.0 * math.pi * _zufall(11, m, ix, iy)
        form += amp[:, None] * np.cos(m * phi[None, :] + phase[:, None])
    if streck is not None:
        psi = phi[None, :] - math.atan2(bergab[1], bergab[0])
        A = np.maximum(streck, 1.0)[:, None]
        form *= 1.0 / np.sqrt((np.cos(psi) / np.sqrt(A)) ** 2 + (np.sin(psi) * np.sqrt(A)) ** 2)
    form = np.maximum(form, 0.3)
    return form / np.sqrt((form ** 2).mean(axis=1, keepdims=True))


def _verschmelzen(tx, ty, tv, ts, theta, bergab, unregel, aufprall=None):
    """Merge touching drops into one (volume added, position and
    elongation weighted by volume). Repeated until nothing touches any more.
    Touching means: the drawn outlines (contact radius of the resting drop
    times _umriss, with irregular edge) overlap.
    Earlier a smaller circle counted, and drops that visibly touched
    stayed apart. aufprall: radius to which a drop that has just landed
    spreads (only applies in the first pass)."""
    for _ in range(4):
        r = _beruehrradius(tv, theta)
        S = 48                                         # finer than drawn: safe side
        form = _umriss(tx, ty, ts, unregel, bergab, S)
        if aufprall is None or len(aufprall) != len(tv):
            aufprall = np.zeros(len(tv))
        reich = np.maximum(r * form.max(axis=1), aufprall)   # farthest edge
        n = len(tv)
        rmax = float(reich.max())
        i, j = _paare_in_reichweite(tx, ty, reich + rmax, tx, ty)
        dist = np.hypot(tx[j] - tx[i], ty[j] - ty[i])
        kandidat = (j > i) & (dist < reich[i] + reich[j])
        i, j, dist = i[kandidat], j[kandidat], dist[kandidat]
        # Edge distance of both outlines in the direction of each other
        w = np.arctan2(ty[j] - ty[i], tx[j] - tx[i])
        ki = np.round(w / (2.0 * math.pi) * S).astype(np.int64) % S
        kj = (ki + S // 2) % S
        beruehrt = dist < (np.maximum(r[i] * form[i, ki], aufprall[i])
                           + np.maximum(r[j] * form[j, kj], aufprall[j]))
        i, j = i[beruehrt], j[beruehrt]
        if not len(i):
            break
        idx = _gruppen(n, i, j)
        v = np.bincount(idx, weights=tv)
        tx = np.bincount(idx, weights=tx * tv) / v
        ty = np.bincount(idx, weights=ty * tv) / v
        ts = np.bincount(idx, weights=ts * tv) / v
        tv = v
        aufprall = None                                # only the moment of impact
    return tx, ty, tv, ts


# ---------------------------------------------------------------- Display
SEGMENTE = 24        # points around the edge
RINGE = 6            # rings from the tip to the edge

# Level of detail by size IN THE IMAGE: (up to pixel radius, segments, rings).
# A drop 2 pixels across needs no 24-sided outline; one 40 pixels across does.
# Measured on Kamera_Nah in the mist: ~4,500 droplets of 2 px made up half of
# the 205,000 faces.
STUFEN = ((3.0, 6, 1), (8.0, 8, 2), (20.0, 12, 3), (float("inf"), SEGMENTE, RINGE))
GRENZEN = np.array([g for g, _, _ in STUFEN])


def pixel_je_meter(scene, p):
    """Image pixels per metre on the glass (the lens width fills the image width)."""
    rd = scene.render
    return rd.resolution_x * rd.resolution_percentage / 100.0 / (p.linse_durchmesser / 1000.0)


def stufe(r_px):
    """(segments, rings) for a drop with contact radius r_px in pixels."""
    for grenze, S, K in STUFEN:
        if r_px < grenze:
            return S, K
    return SEGMENTE, RINGE


_VORLAGEN = {}


def _vorlage(S, K):
    """Faces of one drop as flat corner indices, face sizes and smooth flags —
    the same for every drop with S segments and K rings, so built once.
    Dome outwards (smooth), bottom towards the camera (flat)."""
    if (S, K) not in _VORLAGEN:
        nv = 2 + K * S
        j = np.arange(S); j1 = (j + 1) % S
        ring = lambda k, jj: 1 + (k - 1) * S + jj
        teile = [np.c_[np.zeros(S, int), ring(1, j1), ring(1, j)]]
        for k in range(1, K):
            teile.append(np.c_[ring(k, j), ring(k, j1), ring(k + 1, j1), ring(k + 1, j)])
        boden = np.c_[np.full(S, nv - 1), ring(K, j), ring(K, j1)]
        ecken = np.concatenate([t.ravel() for t in teile] + [boden.ravel()]).astype(np.int64)
        groesse = np.array([3] * S + [4] * (S * (K - 1)) + [3] * S, np.int32)
        glatt = np.r_[np.ones(S * K, bool), np.zeros(S, bool)]
        _VORLAGEN[(S, K)] = (ecken, groesse, glatt, nv)
    return _VORLAGEN[(S, K)]


def _tropfen_netz(x, y, r, h, bergab, th_a, th_r, bo_c, unregel, S=SEGMENTE, K=RINGE,
                  streck=None):
    """Mesh of all drops: flat underside on the glass, bulging outwards.

    x, y, r, h in metres on the glass (contact radius r and height h of the
    spherical cap from berechnen()); bergab = unit vector of gravity on the
    glass. Returns: points (z = 0 is the glass, negative = outside),
    faces as a list of corner lists, and which faces are smooth.

    Shape:
      Edge     Never a circle: defects in the glass pin the contact line,
               in between it spans in arcs (Cubaud & Fermigier 2001,
               Oil Gas Sci. Technol. 56:23). Here as a superposition of modes
               2..5 with a fixed random phase per drop. The strength
               (linse_unregelmaessig) is an APPROXIMATION — there are no
               measurements of the deviation on lens glass.
      Angle    On tilted glass the advancing angle th_a downhill; uphill it
               falls linearly with the Bond number from th_a to th_r, until
               sliding off (ElSherbini & Jacobi 2004, JCIS 273:556).
      Profile  z = H (1 - t^p) per direction, t = distance/edge distance. p such
               that the flank meets the glass at the angle of that direction;
               H such that the volume of the spherical cap is preserved.
    """
    n = len(r)
    phi = np.arange(S) * (2.0 * math.pi / S)
    V = math.pi * h * (3.0 * r * r + h * h) / 6.0

    # Downhill angle per drop and direction
    rho_g = RHO * G / GAMMA
    bo = V ** (2.0 / 3.0) * rho_g
    q = np.clip(bo / bo_c, 0.0, 1.0) if math.isfinite(bo_c) else np.zeros(n)
    th_min = th_a - (th_a - th_r) * q
    psi = phi[None, :] - math.atan2(bergab[1], bergab[0])
    th = th_min[:, None] + (th_a - th_min[:, None]) * 0.5 * (1.0 + np.cos(psi))

    R = r[:, None] * _umriss(x, y, streck, unregel, bergab, S)

    # Height H from the volume: V = H * sum(R^2 p / (2 (p + 2))) dphi
    tan_th = np.tan(th)
    H = r * np.tan(0.25 * (th_a + th_min))
    for _ in range(6):
        pexp = np.maximum(R * tan_th / H[:, None], 0.2)
        flaeche = (R * R * pexp / (2.0 * (pexp + 2.0))).sum(axis=1) * (2.0 * math.pi / S)
        H = V / np.maximum(flaeche, 1e-30)
    pexp = np.maximum(R * tan_th / H[:, None], 0.2)

    # Points: tip, K rings of S points, centre of the underside
    t = (np.arange(1, K + 1) / K)[None, :, None]
    rx = R[:, None, :] * t * np.cos(phi)[None, None, :]
    ry = R[:, None, :] * t * np.sin(phi)[None, None, :]
    rz = -H[:, None, None] * (1.0 - t ** pexp[:, None, :])
    ring = np.stack([rx, ry, rz], axis=-1).reshape(n, K * S, 3)
    spitze = np.c_[np.zeros(n), np.zeros(n), -H][:, None, :]
    boden = np.zeros((n, 1, 3))
    pkt = np.concatenate([spitze, ring, boden], axis=1)
    pkt[:, :, 0] += x[:, None]; pkt[:, :, 1] += y[:, None]
    nv = 2 + K * S

    return pkt.reshape(-1, 3), nv


def _material():
    name = "HK Linsentropfen"
    m = bpy.data.materials.get(name)
    if m is None:
        m = _material_neu(name)
    # EEVEE: refraction via ray tracing, otherwise the water only reflects
    # the surroundings and every drop looks like a chrome ball
    for k in ("use_raytrace_refraction", "use_screen_refraction"):
        if hasattr(m, k):
            setattr(m, k, True)
            break
    return m


def _material_neu(name):
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    nt = m.node_tree
    b = next(x for x in nt.nodes if x.type == 'BSDF_PRINCIPLED')
    b.inputs["Base Color"].default_value = (1.0, 1.0, 1.0, 1.0)
    b.inputs["Roughness"].default_value = 0.0
    b.inputs["IOR"].default_value = 1.333           # water
    for k in ("Transmission Weight", "Transmission"):
        if k in b.inputs:
            b.inputs[k].default_value = 1.0
            break
    return m


def tiefenschaerfe_einrichten(cam, domain):
    """Drops sit centimetres in front of the lens: without depth of field
    they would be pin-sharp, which a real camera focused on the scene never
    shows. So it is on by default — once per camera, after that whatever the
    user sets on the switch applies. If the focus is still at Blender's
    default (10 m, no object), it is focused on the centre of the domain."""
    if cam is None or cam.type != 'CAMERA' or cam.data.get("hk_tiefenschaerfe"):
        return
    dof = cam.data.dof
    dof.use_dof = True
    if dof.focus_object is None and abs(dof.focus_distance - 10.0) < 1e-6:
        mitte = domain.matrix_world @ (sum((Vector(c) for c in domain.bound_box), Vector()) / 8.0)
        blick = cam.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        abstand = (mitte - cam.matrix_world.translation).dot(blick)
        if abstand > 0.0:
            dof.focus_distance = abstand
    cam.data["hk_tiefenschaerfe"] = True


from collections import OrderedDict
_NETZE = OrderedDict()   # finished mesh arrays of recently shown frames
NETZE_HOECHST = 16       # ~5 MB each with 10,000 droplets


def anzeigen(scene, frame):
    """Show the frame's drops on the camera object (or empty the object)."""
    domain = geometrie.finde_domain(scene)
    cam = scene.camera
    obj = bpy.data.objects.get(OBJEKT)
    if domain is None or cam is None or not domain.hydrokit.linse_an:
        if obj is not None:
            obj.data.clear_geometry(); obj.data.update()
        return 0
    pfad = os.path.join(cache.pfad_aufloesen(domain.hydrokit.cache_pfad), ORDNER,
                        f"{frame:06d}.npy")
    daten = np.load(pfad) if os.path.exists(pfad) else np.zeros((0, 4), np.float32)
    if obj is None:
        obj = bpy.data.objects.new(OBJEKT, bpy.data.meshes.new(OBJEKT))
        scene.collection.objects.link(obj)
    from . import anzeige
    anzeige.waehlbar_machen(obj, scene)
    if obj.parent is not cam:                # e.g. another scene camera
        tiefenschaerfe_einrichten(cam, domain)
        obj.parent = cam
        obj.matrix_parent_inverse.identity()
        obj.location = (0, 0, 0); obj.rotation_euler = (0, 0, 0); obj.scale = (1, 1, 1)
    for mod in [m for m in obj.modifiers if m.type == 'NODES']:
        obj.modifiers.remove(mod)          # earlier: spheres via Geometry Nodes

    # Glass just in front of the camera; the lens width D fills the image width
    M, t_w, t_h, seiten = _kamera_daten(scene, cam)
    abstand = max(cam.data.clip_start * 1.5, 0.02)
    p = domain.hydrokit
    D = p.linse_durchmesser / 1000.0
    s = (2.0 * abstand * t_w) / D           # metres on the glass -> local units
    me = obj.data
    me.clear_geometry()
    if not me.materials:
        me.materials.append(_material())
    nebel_pfad = pfad[:-4] + "_nebel.npy"
    # Scrubbing back and forth draws the same frames again: the finished mesh
    # arrays of the last frames are kept, keyed by everything that shapes them
    schluessel = (pfad, _mtime(pfad), _mtime(nebel_pfad), p.linse_unregelmaessig, winkel(p),
                  p.linse_haft_faktor, p.linse_durchmesser, scene.render.resolution_x,
                  scene.render.resolution_percentage, round(abstand, 7), round(t_w, 7),
                  tuple(np.round(M[:3, :3].ravel(), 5)))
    netz = _NETZE.get(schluessel)
    if netz is None:
        nebel = np.load(nebel_pfad) if os.path.exists(nebel_pfad) else np.zeros((0, 4), np.float32)
        netz = _netz_bauen(scene, p, M, daten, nebel, s, abstand) if (len(daten) or len(nebel)) else ()
        _NETZE[schluessel] = netz
        while len(_NETZE) > NETZE_HOECHST:
            _NETZE.popitem(last=False)
    else:
        _NETZE.move_to_end(schluessel)
    if netz:
        anzeige.netz_fuellen(me, *netz)
    else:
        me.update(calc_edges=True)
    return len(daten)


def _mtime(pfad):
    try:
        return os.stat(pfad).st_mtime_ns
    except OSError:
        return 0


def _netz_bauen(scene, p, M, daten, nebel, s, abstand):
    """Flat mesh arrays (points, corners, face starts, smooth flags) of all
    drops and mist droplets of one frame."""
    th_a, th_r = winkel(p)
    g2 = (M[:3, :3] @ np.array([0.0, 0.0, -1.0]))[:2]
    sin_a = float(np.hypot(*g2))
    bergab = g2 / sin_a if sin_a > 1e-6 else np.array([0.0, -1.0])
    v_krit = kritisches_volumen(th_a, th_r, sin_a) * p.linse_haft_faktor ** 1.5
    bo_c = v_krit ** (2.0 / 3.0) * RHO * G / GAMMA if math.isfinite(v_krit) else float("inf")
    co, ecken, groesse, glatt = [], [], [], []
    basis = 0
    # Detail by size in the image (STUFEN); mist droplets without the
    # irregular edge — at a few pixels it would not show anyway
    px = pixel_je_meter(scene, p)
    gruppen = []
    for teil, unregel in ((daten, p.linse_unregelmaessig), (nebel, 0.0)):
        if not len(teil):
            continue
        welche = np.searchsorted(GRENZEN, teil[:, 2] * px, side="right")
        for w in np.unique(welche):
            _, S, K = STUFEN[w]
            gruppen.append((teil[welche == w], unregel, S, K))
    for teil, unregel, S, K in gruppen:
        m = len(teil)
        d = teil.astype(np.float64)
        streck = d[:, 4] if d.shape[1] > 4 else None
        pkt, nv = _tropfen_netz(d[:, 0], d[:, 1], d[:, 2], d[:, 3], bergab,
                                th_a, th_r, bo_c, unregel, S, K, streck)
        e, gr, gl, _ = _vorlage(S, K)
        versatz = basis + np.arange(m, dtype=np.int64) * nv
        ecken.append((e[None, :] + versatz[:, None]).ravel())
        groesse.append(np.tile(gr, m))
        glatt.append(np.tile(gl, m))
        co.append(pkt * s)
        basis += m * nv
    co = np.concatenate(co).astype(np.float32); co[:, 2] -= abstand
    ecken = np.concatenate(ecken).astype(np.int32)
    groesse = np.concatenate(groesse)
    start = np.concatenate([[0], np.cumsum(groesse)[:-1]]).astype(np.int32)
    return (co.ravel(), ecken, start, np.concatenate(glatt))
