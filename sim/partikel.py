# HyprW — particle types, mass and air drag
# SPDX-License-Identifier: GPL-3.0-or-later
#
# A drop falls slowly AND follows the wind strongly when it is light.
# Both hang on the same lever: air drag.
#
# The engine integrates explicitly:
#
#     v_new = v + g*dt - c*(v - wind)*dt
#
# At equilibrium (v_new = v, no wind) 0 = g - c*v  ->  c = g / v_term.
# So the drag follows directly from the terminal fall speed, and that in
# turn from the drop size. Measured values (sources see README):
#
#     diameter        terminal velocity
#     0.02 mm  mist         0.01 m/s      practically floating
#     0.1  mm  fine haze    0.3  m/s
#     0.5  mm  drizzle      2.0  m/s
#     1.0  mm  spray        4.0  m/s
#     2.0  mm  drop         6.5  m/s
#     5.0  mm  large drop   9.0 m/s       breaks up above this
#
# WIND RESPONSE follows by itself: the time constant of adapting to the air
# speed is 1/c. Mist (large c) takes on the wind speed almost at once, a heavy
# drop hardly at all.

ERDBESCHLEUNIGUNG = 9.81

# Support points (diameter in mm, terminal velocity in m/s)
FALLKURVE = [
    (0.02, 0.01),
    (0.10, 0.30),
    (0.50, 2.00),
    (1.00, 4.00),
    (2.00, 6.50),
    (5.00, 9.00),
]

# Explicit integration becomes unstable when c*dt approaches 2. With the
# usual substeps (about 10 ms) 60 is the safe upper limit.
# That equals a terminal velocity of 16 cm/s — slow enough for mist.
MAX_WIDERSTAND = 60.0


def endgeschwindigkeit(durchmesser_mm):
    """Terminal fall speed in m/s, interpolated between the support points."""
    d = max(durchmesser_mm, 1e-4)
    if d <= FALLKURVE[0][0]:
        # below: Stokes regime, v ~ d^2
        d0, v0 = FALLKURVE[0]
        return v0 * (d / d0) ** 2
    if d >= FALLKURVE[-1][0]:
        return FALLKURVE[-1][1]
    for (d0, v0), (d1, v1) in zip(FALLKURVE, FALLKURVE[1:]):
        if d0 <= d <= d1:
            # interpolate logarithmically: the curve spans decades
            import math
            t = (math.log(d) - math.log(d0)) / (math.log(d1) - math.log(d0))
            return v0 + t * (v1 - v0)
    return FALLKURVE[-1][1]


def widerstand(durchmesser_mm):
    """Air drag coefficient for the engine."""
    v = endgeschwindigkeit(durchmesser_mm)
    if v <= 1e-6:
        return MAX_WIDERSTAND
    return min(ERDBESCHLEUNIGUNG / v, MAX_WIDERSTAND)


def windangleichung(durchmesser_mm, flugzeit=0.5):
    """Share of the wind speed the particle takes on during its flight
    — 0 to 1.

    The adaptation runs exponentially with the time constant 1/c:

        v(t) = wind * (1 - exp(-c*t))

    The bare coefficient is useless as a display: c drops from 60 to 1.5, while
    the actual wind uptake over a half-second flight only drops from
    100 % to 53 %. Perceived, that is the difference between "blown away
    completely" and "noticeably deflected".
    """
    import math
    c = widerstand(durchmesser_mm)
    return 1.0 - math.exp(-c * max(flugzeit, 1e-3))


def beschreibung(durchmesser_mm):
    v = endgeschwindigkeit(durchmesser_mm)
    if v < 0.05:   return "schwebt"
    if v < 0.5:    return "sinks slowly"
    if v < 2.5:    return "falls gently"
    if v < 5.0:    return "falls briskly"
    return "falls fast"


# ---------------------------------------------------------------------------
# TWO DIFFERENT MODELS IN THE ENGINE — do not mix them up:
#
#   Spray:  nextv = v + g*dt - c*(v - wind)*dt
#           c is a RATE. Upper limit 5.0, equals 1.96 m/s terminal
#           velocity. Spray does not fall slower than that in this model.
#
#   Mist (dust channel):
#           nextv = v + dt*buoyancy + c*(vAir - v)
#           c is a MIXING FACTOR 0..1. At 1.0 the particle takes on the
#           air speed at once — exactly how mist behaves. The sinking
#           does NOT come from c but from the buoyancy coefficient.
#
#   Bubbles: a mixing factor like mist, but against the water flow.
#   Foam:    mixing factor against the flow, floats on the surface.

SPRAY_MAX_RATE = 5.0


def spray_widerstand(durchmesser_mm):
    """Drag rate for the spray channel, limited to the engine's bound."""
    return min(widerstand(durchmesser_mm), SPRAY_MAX_RATE)


def nebel_kopplung(durchmesser_mm):
    """Mixing factor 0..1: how completely mist follows the air.

    Fine mist follows practically at once, coarser drops more sluggishly.
    """
    v = endgeschwindigkeit(durchmesser_mm)
    # full coupling up to 0.3 m/s, decreasing above
    return max(min(0.3 / max(v, 1e-4), 1.0), 0.05)


def nebel_sinken(durchmesser_mm):
    """Buoyancy coefficient for the dust channel. Negative means sinking.

    FLIP's default is -3.0 — that lets mist fall THREE times as fast as
    gravity, because the channel is meant as sinking sediment there.
    For mist the value is derived from the drop size and stays small.
    """
    v = endgeschwindigkeit(durchmesser_mm)
    return -max(min(v / 3.0, 3.0), 0.0)


# Defaults per type — diameter in mm
ARTEN = {
    "nebel":  ("Mist",   0.03, "Floats in the air, follows the wind almost completely"),
    "spray":  ("Spray",  0.80, "Flying drops, clearly affected by wind"),
    "foam":   ("Foam",   2.00, "Floats on the surface"),
    "bubble": ("Bubbles", 1.50, "Inside the water, no wind"),
}
