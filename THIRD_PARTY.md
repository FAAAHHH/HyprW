# Third-party code

**FLIP Fluids engine** — `ffengine/` and `ffengine/lib/libffengine.so`,
built from <https://github.com/rlguy/Blender-FLIP-Fluids> (`src/engine`)
with `engine_patch/hydrokit-engine.patch` applied.
Copyright (C) 2026 Ryan L. Guy & Dennis Fassbaender, MIT license
(`ffengine/LICENSE_MIT.md`). The patch is MIT as well.

Compiled into the engine library, as in the FLIP Fluids repository:

- **Eigen** (header-only linear algebra), Mozilla Public License 2.0,
  <https://eigen.tuxfamily.org>.
- **Mixbox** v1.2, (c) 2022 Secret Weapons, for non-commercial use only,
  <https://scrtwpns.com/mixbox/>. FLIP Fluids ships its header in the
  public engine source; HyprW never calls the Mixbox functions.
