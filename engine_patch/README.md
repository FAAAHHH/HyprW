# Engine extensions for HyprW

HyprW computes with the FLIP Fluids engine (MIT license), but needs a few
extensions that do not exist in the original engine. All of them are in
`hydrokit-engine.patch` (`src/engine/` plus two lines in `CMakeLists.txt`
for the GPU support, MIT throughout).

Without the patch HyprW keeps running, only wind and the mist fine-tuning
have no effect.

## 1. Wind on whitewater

FLIP Fluids computes the air drag of whitewater against STILL air:

    dragvec = -dragCoefficient * dp.velocity * dt

The patch computes it against MOVING air:

    relv    = dp.velocity - windVelocity
    dragvec = -dragCoefficient * relv * dt

A drop flying with the wind feels no drag; a drop at rest is accelerated to
wind speed. This affects spray and dust.
New: `set_diffuse_wind_velocity(x, y, z)`.

## 2. Mist constants

The engine keeps turbulence factors and the maximum distance to the obstacle
for dust/mist as fixed constants. The patch makes them adjustable:
`setDustTurbulenceFactors`, `setMaxDustEmitterToObstacleDistance`.

## 3. Wind on the water surface

Pulls the water in a thin layer below the surface slightly in the wind
direction. Only cells with distance `0 .. -depth` to the surface, weighted
per axis by the share of the wind direction — so a crosswind does not slow
down a falling jet.
New: `set_surface_wind_velocity`, `set_surface_wind_factor`,
`set_surface_wind_depth`.

## 4. Mist model: spray turns into mist — after literature values

At waterfalls mist forms almost only on IMPACT, not in free fall
(Frontiers in Env. Science 2023: "Splashing is the main atomization source for a
jet impinging on a liquid layer"). The model has three paths, all from
measurements:

1. **Breakup in the air** only above Weber number We = rho_air v^2 d / sigma > 12
   (Pilch & Erdman 1987), breakup time after their correlation
   T(We) * d/v * sqrt(rho_water/rho_air). For 0.8 mm drops that takes
   ~30 m/s — so practically never in waterfalls below ~45 m height.
2. **Splashing on impact with water** from K = We * Oh^-0.4 > 2100
   (Cossali 1997), using the normal component of the impact speed. For 0.8 mm
   that is 4.6 m/s; spray at its sinking speed (~3.3 m/s) therefore does not
   splash. The fine fraction grows with K and saturates (Okawa 2006: mass
   fraction of secondary drops 0..1); the shape of the curve is an
   approximation.
3. **Jet impact**: fast spray that forms in an impact zone (obstacle or deep
   water up to three cells below) is itself splash water of the impact and
   gets the same fine fraction.

Once mist, always mist. After that the particle flies with mist physics:
slowly sinking, carried by the wind.
New: `set_diffuse_spray_mist_model(diameter_m, fine_fraction, k_threshold)`.

## 5. Mist domain

Mist leaving the water domain keeps flying ballistically up to a boundary of
its own. Outside the grid the engine delivers zero flow; there mist only
drifts with wind, drag and sinking. That only costs moving the particles, no
water computation. Beyond the boundary it is removed.
Spray, on the other hand, disappears at the water domain (only mist flies on).
New: `set_diffuse_dust_outer_bounds(AABB)`.

## 6. Droplet physics for spray and mist

Replaces FLIP's mixing drag (which coupled mist practically rigidly to the air
— it did not sink) with the equation of motion from the dam atomisation
spray model (Lian et al. 2019, Nazixia, IJERPH 16(3):316):

    x'' = -3/4 Cd/d rho_a/rho_w |v-u| (v-u) + (1 - rho_a/rho_w) g

- Cd after Schiller-Naumann: 24/Re (Re <= 0.2), 24/Re(1+0.15 Re^0.687) (up to 800), 0.44
- From 0.5 mm on, drops flatten; there the drag is calibrated to Atlas,
  Srivastava & Sekhon (1973): vt = 9.65 - 10.3 exp(-0.6 D[mm])
- Diameter per particle gamma-distributed (shape 2, scale 0.5 d50), fixed via
  the ID. Spray d50 = 3 mm (Lian 2019), limited to their measured range
  0.125-8.5 mm. Mist: fine fraction from jet impact, measured 40-400 µm,
  most common 100-156 µm (lab measurements on impacting jets — no measurement
  was found for natural waterfalls).
- u = wind. The air dragged along by the falling water ("downdraft") is not
  included — the engine has no air phase.

Checked against Gunn & Kinzer (1949): 0.2-5 mm within +-2 %, 0.1 mm -8 %.
New: `set_diffuse_spray_droplet_model(d50, dmin, dmax)`,
`set_diffuse_dust_droplet_model(d50, dmin, dmax)` (metres).

## 7. Faster whitewater and mist emission

Stack samples during a waterfall bake with mist showed FLIP's emitter search
running on a single core over every marker particle, every substep:

- `_sortMarkerParticlePositions`: jitter, SDF lookup and air-neighbour check
  per marker particle, serial, with `rand()` (locked) for the jitter. Now in
  parallel; the jitter comes from a per-particle hash, uniform in
  [-jitter, jitter] like before, with a new seed per call.
- `_getDiffuseDustParticleEmitters`: interpolated the velocity of EVERY
  marker particle a second time, although only those near an obstacle are
  used. Now only those.
- `_getInsideDiffuseParticleEmitters`: the turbulence lookup per inside
  particle runs in parallel; the random draws stay serial and in order.

Measured on the 90-frame waterfall test scene (5.3 cm voxels, mist on),
frames with whitewater: 2.93 -> 2.38 s per frame (1.23x); frames without
whitewater unchanged. Foam, spray and mist counts per frame stay within the
normal run-to-run spread — the engine is not bit-reproducible between runs
even without this change (threaded level set), so files cannot be compared
byte by byte. The rest of the frame time is FLIP's fluid solver itself
(pressure solve, level set, advection), which is already multi-threaded.

## 8. GPU support (NVIDIA, CUDA)

The time of a frame is spread over five steps that all run on every CPU
thread already — measured on the waterfall at resolution 150 (1.17 s per
frame): whitewater 22 %, surface mesh 20 %, water particles 18 %, advection
18 %, pressure 14 %. So each step gets a GPU version of its own; the add-on
lets you choose CPU, GPU or, per step, both. All five have a GPU version now:
surface mesh, whitewater (its surface curvature), water particles (the liquid
level set), advection (the velocity transfer) and pressure (the PCG solve).

| Step | On the GPU | Agreement with the CPU (60 waterfall frames) |
|---|---|---|
| Surface mesh | distance field from the particles | bit-identical, 26.9 M values |
| Whitewater | curvature: level set reinitialization | bit-identical, 41.0 M values |
| Water particles | liquid level set from the particles | bit-identical, 41.0 M values |
| Advection | FLIP transfer particles -> grid | float rounding, at most 1.9e-6 m/s over 125 M faces |
| Pressure | PCG with the CPU's MIC(0) factor | same solution and iteration count in all 96 solves |

Of the mesh time three quarters went into the scalar field (for every node
the distance to the nearest marker particle minus the radius, ~100 ms per
frame), the rest into marching cubes, smoothing and cleanup. The field now
runs on the GPU (`hk_mesher_scalar_field` in `hkgpu_kernels.cu`), the rest
stays on the CPU.

- **Bit-identical.** The kernel repeats `ParticleMesher` operation for
  operation: float or double exactly where the CPU code uses them, the same
  reciprocals, the same particle-to-block assignment, no fused multiply-add
  (`-fmad=false`). The minimum does not depend on the order, so the parallel
  result is exact. Checked: 60 waterfall frames, 26.9 million nodes, 0 differ
  in any bit — the GPU mesh is the CPU mesh.
- **Never stops a bake.** The CUDA driver is opened at run time (`dlopen`);
  without an NVIDIA driver, with an older GPU (needs compute capability 7.5,
  RTX 20xx or newer), with too little GPU memory or on any driver error the
  field is computed on the CPU and the bake report says so.
- **Builds without CUDA.** The kernels are embedded as a fatbin
  (`hkgpu_kernels_fatbin.h`, machine code for sm_75 to sm_120 plus PTX).
  Only after changing `hkgpu_kernels.cu`: run `hkgpu_build_kernels.sh` (needs
  the CUDA toolkit; GCC newer than nvcc supports is fine, see the script).
- **Memory.** About 100 MB of GPU memory for the CUDA context and buffers
  (measured on the waterfall), released after every bake so Blender's
  viewport and Cycles get it back.
- **Speed.** Waterfall, 120 frames: mesh step 30.1 s on the CPU, 10.2 s with
  the GPU field (2.9x); the whole bake 151 s -> 140 s, as much as the mesh
  step's share allows.

### Whitewater: the surface curvature

FLIP Fluids counts the surface curvature as whitewater time: wave crest
emission needs it. More than half of the whitewater step is this curvature,
and most of the curvature is a level set reinitialization
(`LevelSetSolver::reinitializeUpwind`, Jacobi iterations over the band around
the surface). It now runs on the GPU (`hk_reinit_upwind_step`), again
bit-identical, including the convergence test — 41 million values over 60
waterfall frames, 0 differ. Directly measured per call (frames 40-60):
reinitialization 15.6 ms on the CPU, 3.6 ms on the GPU; whole curvature
29.5 ms -> 17.9 ms.

On the way, two bugs of the original engine, fixed for the CPU as well:

- The reinitialization's second grid was **uninitialized** (`new float[]`):
  cells at the edge of the band read their outside neighbours from it every
  second iteration — random memory, so not even two CPU runs had to agree.
  Both grids now start as the input (`reinitializeEno` had the same bug).
- The list of solver cells was built with `numValid` placeholder entries and
  then `numValid` real ones appended: half of the work recomputed cell
  (0, 0, 0). Now `reserve`. Whitewater step on the CPU: 28.3 s -> 19.4 s
  over 120 waterfall frames.

The whitewater's own emitter search (~54 ms per substep) is still on the CPU.

### Water particles: the liquid level set

`ParticleLevelSet::_computeSignedDistanceFromParticles` has the mesher's
structure (blocks, particles sorted into them, min of |cell - p| - r), at
cell centres instead of nodes and with the box one cell smaller. The same
kernel handles both (`halfOffset`, `boxPlusOne`). Bit-identical. Moving the
marker particles themselves (~4 % of a frame) stays on the CPU.

### Advection: the velocity transfer

`VelocityAdvector` (FLIP mode) splats every particle's velocity with a
kernel weight onto the faces of each velocity component. The weights are
computed exactly like the CPU, but a face's contributions are added up in
parallel (double atomics) instead of in the CPU's particle order, so the
results agree to float rounding, not bit for bit: at most 1.9e-6 m/s, valid
flags identical. The particle sorting the CPU needs falls away. APIC stays on
the CPU.

### Pressure: the PCG solve

The MIC(0) factor is formed on the CPU exactly as `PCGSolver` does (a few
ms). The iterations run on the GPU: matrix product and vector updates as on
the CPU; both triangular solves of the preconditioner row by row in levels
(rows of one level depend only on earlier ones, each row repeats the CPU's
subtractions in its order). The dot products are summed as a tree instead of
one after the other — but `BLAS::addScaled` takes its factor as float, which
rounds those tiny differences away: in all 96 test solves the solution and
the iteration count were the same as on the CPU.

### Choosing the device per step

In verify mode every GPU call is also computed on the CPU — and both are
timed (`addVerifyTiming`, `ffengine.gpu.verify_times`). The add-on's
"Pick the Fastest per Step" uses that: `sim/geraete_probe.py` bakes four
frames of a copy of the file with a water block in the lower quarter of the
domain, in a separate Blender process, and each step gets the faster device.
Waterfall (resolution 150): GPU faster everywhere — mesh 4.9x, whitewater
11.8x, water particles 8.9x, advection 5.5x, pressure 2.1x. A 24-cell test
domain: mesh and pressure faster on the CPU, where the GPU's transfers
outweigh the little work.

Files: `hkgpu.h/.cpp` (driver, buffers, fallback), `hkgpu_kernels.cu`,
`hkgpu_kernels_fatbin.h`, `hkgpu_build_kernels.sh`, `c_bindings/hkgpu_c.cpp`,
`ffengine/gpu.py`; `ParticleMesher` and `FluidSimulation` gain the switch
(`enable_gpu_surface_meshing`), `LevelSetSolver`, `ParticleLevelSet` and
`FluidSimulation` the whitewater one (`enable_gpu_whitewater`), and
`enable_gpu_water_particles`, `enable_gpu_advection`, `enable_gpu_pressure`
(`ParticleLevelSet`, `VelocityAdvector`, `PressureSolver`).
`CMakeLists.txt` lists the two new sources
and links `${CMAKE_DL_LIBS}` — the only change outside `src/engine/`.

## Lens drops (sim/linse.py, no engine change)

Computed after baking from the cache; the sources for each part:

- Sticking and sliding limit: Quere 1998, Langmuir 14:2213 (eq. 1 and 3,
  table 1: water on float glass 60/44 degrees)
- Fluorine-coated lens: static contact angle 115 degrees, water sliding
  angle 16 degrees (Daikin OPTOOL DSX-F datasheet); hysteresis 11..15 degrees
  for such coatings (US patent 11061171). Used: 120.5/109.5, the low end —
  then a ~29 µl drop slides at 16 degrees, closest to the datasheet, which
  does not state its drop volume (approximation within the range)
- Sliding speed: Le Grand, Daerr & Limat 2005, JFM 541:293 (Ca = c (Bo - Bo_c),
  c = 0.00666 measured on silicone oil; not measured for water)
- Trails: Le Grand, Daerr & Limat 2005, fig. 4 (pearling from Ca = 7.2e-3);
  pearl size, spacing, satellites and elongation are approximations
- Splashing on the lens: Mundo, Sommerfeld & Tropea 1995, IJMF 21:151
  (K = We^0.5 Re^0.25 > 57.7); the remaining share is an approximation
- Spreading on impact: Laan et al. 2014, Phys. Rev. Appl. 2:044018, eq. 3
  (A = 1.24, valid 10 < We < 1700, 70 < Re < 17000)
- Receding to the receding angle: de Gennes 1985, Rev. Mod. Phys. 57:827
- Irregular edge: Cubaud & Fermigier 2001, Oil Gas Sci. Technol. 56:23
  (strength is an approximation)
- Contact angle around the drop on tilted glass: ElSherbini & Jacobi 2004,
  JCIS 273:556
- Mist water content: extinction efficiency Q = 2 (van de Hulst 1957);
  collection efficiency St/(St + pi/2) after Langmuir & Blodgett (cylinder,
  for the camera front an approximation)

## Applying

    cd <FLIP Fluids source>
    git apply /path/to/hydrokit-engine.patch
    cd build && cmake --build . --target ffengine -j$(nproc)

Then copy into HyprW:

    build/.../ffengine/lib/libffengine.so  ->  hydrokit/ffengine/lib/
    src/engine/ffengine/fluidsimulation.py ->  hydrokit/ffengine/

Careful: `file(COPY ...)` in CMakeLists only runs at configure time. So copy
the .py file directly from `src/`, not from the build folder.

A loaded `.so` cannot be reloaded: restart Blender after a rebuild.

## Checked

`git apply --check` against the unchanged state: applies cleanly.
