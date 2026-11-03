# HyprW — Hyper Realistic Water

A water simulation add-on for Blender. HyprW runs its own simulation on the
[FLIP Fluids](https://github.com/rlguy/Blender-FLIP-Fluids) engine (MIT,
bundled and extended) and takes care of everything around it: whitewater,
mist, wind, drops on the camera lens, materials. Wherever it can, it uses
measured physics with a source instead of values tuned by eye; where it
has to approximate, the code says so.

The FLIP Fluids add-on is **not** needed.

## What it does

**Own simulation** — mark objects with a role (Domain, Water, Obstacle,
Inflow, Outflow, Mist Domain, Spray Domain) in the *HyprW — Own Engine*
panel, press Bake. Each role has its own settings; results are drawn as
light previews and loaded per frame from the cache. While baking the panel
shows the frame, a progress bar and the time left; **Lighter Viewport**
draws a lighter mesh in the viewport while renders keep the full one.

**Adaptive particles** — deep below the surface pairs of particles merge
into larger ones; near the air, near obstacles and in fast flow they split
back, so the surface stays as fine as the domain resolution (Adams, Pauly,
Keiser & Guibas 2007). A large lake behind a fine waterfall no longer costs
as many particles as the waterfall. Test scene, 30 frames on the GPU:
2.76 million particles carried by 1.39 million, 75 s → 64 s, same volume;
at a higher resolution 11.8 million carried by 4.5 million. Off by default,
and then bit-identical to before.

**GPU** — surface mesh, whitewater, water particles, advection and pressure
can each run on the CPU or an NVIDIA GPU (RTX 20xx or newer). Mesh,
whitewater and water particles are bit-identical to the CPU, advection
agrees to float rounding, pressure gave the same solution in every test.
**Pick the Fastest per Step** measures both devices on a copy of your file
instead of guessing. Waterfall test scene: 1.67x faster in total.

**Whitewater** — foam, bubbles and spray as point clouds with a size spread
and motion blur. Foam is aerated water (20 % air, 1 mm bubbles): the light
path through every particle is solved in the shader, so it renders the same
on CPU and GPU. Checked against Cycles path-tracing the same medium.

**Water** — absorption of pure water (Pope & Fry 1997), dissolved organic
matter and the murkiness of real rivers, set as **visibility** — how far a
black object can be seen under water (4.8 / attenuation, Davies-Colley
1988). Thin water stays clear, deep water takes its colour by itself.
Light reaches the ground under water through the surface, with caustics,
capillary ripples at 1.71 cm (where capillary and gravity waves meet) and
optional dispersion with water's measured refractive index per colour
(Daimon & Masumura 2007). Files from older versions get the current look
when opened.

**Mist** — spray breaks up into mist after literature values (air break-up,
impact splashing), drifts with wind and is rendered as a volume.

**Wind** — acts on whitewater in the air and on a thin layer of the water
surface, against moving air instead of still air. Shown in m/s and Beaufort.

**Lens drops** — drops on the camera lens driven by the simulation: spray
and mist that reach the camera land, merge, slide and leave trails, with
contact angles of real glass coatings.

**Grid overlay** — the simulation grid on the domain walls in the viewport,
so you see whether an obstacle is thick enough for the resolution.

## Installation

1. Download `hyprw-<version>.zip` from
   [Releases](https://github.com/FAAAHHH/HyprW/releases).
2. Blender: *Edit → Preferences → Get Extensions → Install from Disk*.
3. The tab **HyprW** appears in the 3D viewport sidebar (N).

Requirements: **Blender 5.2**, **Linux x86-64** with a libstdc++ from GCC 13
or newer (Ubuntu 24.04, Fedora 39 and later). The bundled engine
(`ffengine/lib/libffengine.so`) is built for Linux. For Windows or macOS,
build the engine from FLIP Fluids' source with `engine_patch/` applied
(see `engine_patch/README.md`) and put the library into `ffengine/lib/`.
GPU support needs an NVIDIA driver; without one everything runs on the CPU.

## Layout

    sim/                    own simulation: bake, cache, display, look, lens
    operators/              one module per feature
    properties/             settings per scene and object
    ui/                     sidebar panels, grid overlay, faster scrolling
    ffengine/               the FLIP Fluids engine (MIT) and its Python API
    engine_patch/           HyprW's engine extensions, with documentation
    tests/                  background test, see below

## Tests

    blender -b --factory-startup --python tests/blender_kameratropfen.py -- <path to this folder>

174 checks: bake, roles, adaptive particles, GPU against CPU, materials, lens drops, register
and unregister.

## License

The add-on is GPL-3.0-or-later (`LICENSE.md`). The engine in `ffengine/`
and the engine patch are MIT (`ffengine/LICENSE_MIT.md`). Third-party code
inside the engine library is listed in `THIRD_PARTY.md`.
