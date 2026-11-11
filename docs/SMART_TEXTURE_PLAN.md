# Smart Texture plan

Date: 2026-11-10. Status: **T0 and T1 done; T2 built and passing on synthetic data, not yet on real photos;
T3 editor, T4 masks and T5 first slice built (2026-11-11); T6 and T7 not started.** See "Progress" at the end.

**Goal.** Texture the model from the landmark's own reference photos, the ones already in `input/sources.json`
with a licence that allows texture use. Every photo pixel must land on the model surface it shows: each window on
its window, each cornice on its cornice. Where no photo shows a surface, the texture is synthesised and labelled as
such. Accuracy is measured, not judged by eye.

This plan replaces `autotex.run_v2` as the default texture step once its exit gate is met. It follows the rules in
`docs/README.md`:
- no landmark-specific branches;
- every change backed by a deterministic metric and tests;
- a held-out set run once, with the tool frozen.

---

## 1. Why the current textures are inaccurate

Evidence: Bưu điện, `reports/autotex/qa_sheet.jpg` and `grid_photo1.jpg` (2026-11-10).

| # | Cause in the code | What it does to the result |
|---|---|---|
| C1 | The AI reads quad corners off a 10 × 10 grid on a photo shrunk to at most 1400 px (`grid_overlay`, `PROMPT_V2`). Nothing refines the corners against the image. | Corner errors of 1–5 % of the photo width, often a whole bay or storey. Tile 1 of Bưu điện contains sky and roof; tile 3 is mostly sky. |
| C2 | No real camera. A view is an azimuth plus a coarse elevation class (`cam_of`, `ELEVATIONS`). There is no focal length, position or roll. | The model cannot be projected into the photo, so nothing can check where a wall really is. Visibility is guessed from the facing angle. |
| C3 | Each face is mapped by one 4-point homography. This assumes a flat wall and a distortion-free pinhole camera. | Curved fronts and stitched or wide-angle panoramas cannot be rectified. Photo 1 of Bưu điện is a panorama with a bent cornice, and tile 4 is a curved drum flattened onto a flat face. |
| C4 | One AI-chosen bay is repeated `n_bays` times (`tile_face`). The tile's phase and width come from the AI's corners, not from the windows. | Seams cut through windows, and the repeat does not line up with the modelled bay grid. The texture also hides the real asymmetries of the facade. |
| C5 | A photo panel is a flat quad 5–8 cm in front of the face (`add_panels`), and it removes the generated detail on that face. | Relief is lost. Openings, pilasters and recesses become a flat picture. The panel also floats in front of returns and corners. |
| C6 | Occluders are handled by one AI look at a contact sheet (`qa_v2`): drop, mirror spans, clean band. | Trees, people, flags and sky remain in kept textures. Mirroring assumes symmetry that may not exist. |
| C7 | Colour is fixed by global gains (`harmonise`, `match_wall_colour`). | Visible seams between faces and between photos. Shading baked into the photo is not separated from the colour. |
| C8 | The texture is fitted to the spec's geometry, whatever it is. | When the massing is wrong (plan IoU 0.44 on UBND, 2026-11-10), the texture is stretched to fit the wrong wall and the error is hidden instead of reported. |
| C9 | Quality is checked only by AI eye. The judge's noise is up to 1.67 points on identical images (`docs/M0_M8_STATUS.md`). | Changes cannot be shown to help or hurt. |

Root cause: **the tool never establishes where each photo was taken from, relative to the model.** C1–C6 follow
from that. The fix is a calibrated camera per photo, then projective texturing onto the real geometry.

---

## 2. Approach

Per photo, solve the full camera: intrinsics, lens distortion, rotation and position, in the model's local frame.
Then project the photo onto the model surface through a visibility buffer. This is single-view calibration against
a known model. It does not depend on structure-from-motion, so it works with one photo per facade and avoids the
fold problem M7 hit on repetitive facades. When M7 does produce cameras, they enter the same contract.

```
photos (texture-licensed)
  │ T1 intake: EXIF, lens model, panorama check, undistort
  ▼
undistorted photos ──► T2 camera solve against the spec (VP + keypoints + snap + PnP/LM)
  │                         │ residual too high ► "geometry mismatch" issue (A2), never stretched
  ▼                         ▼
T4 masks (sky, occluders, self-occlusion)   input/cameras.json (+ T3 manual edits)
  │                                          │
  └──────────────► T5 projective bake (UV atlas, best view per texel, blending, exposure)
                          │
                          ▼
                  T6 fill unseen texels from observed bays (grid from the photo) or swatch
                          │
                          ▼
                  T7 metrics + overlay sheets ► gate at A3
```

---

## 3. Milestones

Each milestone delivers code, tests, metric output and a dev-set measurement. They follow the order below. T3 may
run in parallel with T4.

### T0: measure the current tool first
- Add metrics computed from the textures alone, with no camera needed:
  - **Foreign-pixel share:** sky, vegetation and people. Sky comes from colour, position and connection to the top
    edge; the rest from the AI occluder boxes of T4 once available.
  - **Bay phase error:** window column centres, found by periodicity (autocorrelation of vertical edge energy),
    against the modelled bay grid.
  - **Seam ΔE:** CIEDE2000 colour difference across face borders.
- Run them on `run_v2` output for the dev set. That is the baseline every later milestone must beat.
- Make golden correspondences for the dev set: about 12 hand-placed model-keypoint ↔ pixel pairs per photo, made
  with the T3 editor once it exists, by the owner or reviewed by the owner. They are stored in `tests/fixtures/`
  only as numbers, never the photos (licence).
- **Exit gate:** baseline numbers recorded in `reports/benchmark/BENCHMARK.md`.

### T1: photo intake and lens
- Read EXIF focal length and sensor size, and give a prior on the field of view.
- **Distortion:** detect long line segments, fit a one-parameter division model that straightens them, and
  undistort the photo. Keep the parameters.
- **Panorama and fisheye check:** if a stitched panorama still bends straight lines after the best division model,
  mark the photo `projection: "panorama"`. It is then used only for swatches and observations, never projected.
  Cylindrical panorama support is a later option.
- **Exit gate:**
  - line straightness residual under 0.3 % of the diagonal on every photo kept for projection;
  - unit tests on synthetic distorted grids.

### T2: camera solve against the model
1. **Vanishing points:** from the line segments, get the vertical vanishing point and one or two horizontal ones.
   They give roll, pitch and focal length (when two orthogonal ones exist), and a yaw seed checked against the
   azimuth the AI matched to the view.
2. **Keypoints:** the spec gives named 3D keypoints: part corners at base and top, eave ends, roof ridge ends, and
   floor-line ends from the facade grid. The visible ones are drawn with labels on a render from the seed camera.
   The AI sees that render next to the photo and gives coarse pixel positions for the keypoints it can identify,
   with a confidence.
3. **Snap:** each coarse point is refined in a local window to the nearest matching image feature: a corner, or the
   intersection of fitted edge lines whose directions agree with the model's. A point that cannot be snapped keeps
   a low weight.
4. **Solve:** robust PnP (RANSAC), then Levenberg–Marquardt on focal length and pose. Principal point and
   distortion are fixed from T1. Report:
   - RMS reprojection error;
   - per-part residuals;
   - the share of inlier keypoints.
5. **Edge refinement:** a final optimisation of the pose that minimises the chamfer distance between the projected
   silhouette and crease edges of the model and the photo's edges (reusing `quality.edge_distance`).
6. **Geometry check:** if one part's residuals are consistently over the threshold while the others fit, the model
   is wrong there, not the camera. Raise a `geometry_mismatch` issue on that part, with an overlay image, back to
   spec review. The texture for that part is not stretched to fit. A proposal to adjust the part's dimensions may
   be made later as a correction for the owner to approve, never applied silently.
- **Output:** `input/cameras.json`, version 1. Per photo: K, distortion, R, t, residuals, the keypoints used and
  their source (`auto`, `ai`, `manual`, `m7`).
- **Exit gate:**
  - on the golden set, median reprojection error at most 0.5 % of the image diagonal;
  - edge chamfer at most 1 % of the diagonal;
  - a solved camera for at least 80 % of texture-licensed photos that show the building.

### T3: camera and correspondence review in the web app
- New A2.5 panel, "Texture cameras". It shows each photo with the projected model wireframe on top, and the
  keypoints.
- The owner can drag a keypoint to its true place, or add one. The camera re-solves at once and the residual
  updates.
- Manual points are saved with `source: "manual"` and always win over automatic ones.
- **Why this matters:** this is the "exact, spot by spot" tool for the cases automation misses. It is also how the
  golden set is made.
- **Exit gate:**
  - the editor round-trip is tested;
  - re-solving a typical photo takes under 1 s.

### T4: masks
- **Sky:** a colour and position classifier connected to the top edge, refined by the projected model silhouette:
  pixels inside the silhouette are not sky.
- **Occluders** (trees, people, vehicles, poles, signs, flags): AI boxes per photo, refined to masks with
  colour-based segmentation (GrabCut) inside each box.
- **Optional:** a local segmentation model behind a setting. It needs its licence reviewed first.
- **Self-occlusion:** the visibility buffer (part and face ID per pixel) from a Blender render with the solved
  camera. A pixel textures only the face that is visible at that pixel.
- **Exit gate:** on a labelled mask sample from the dev set, occluder IoU of at least 0.7 and a sky false-positive
  share on walls of at most 0.5 %.

### T5: projective bake onto the real geometry
- **UV atlas:** in Blender, unwrap each part with uniform texel density (planar charts per facet, sized by texel
  density), the gutter padding already in `textures.py`, and a texture size per budget.
- **Best view per texel:** each texel is projected into every solved photo. A view is scored by:
  - viewing angle (cos θ);
  - resolution (pixels per metre);
  - distance to a mask edge;
  - masks (an occluded or sky pixel scores zero);
  - visibility in the ID buffer.
  
  Graph-cut labelling with a smoothness term keeps large regions from one photo.
- **Blending:** multi-band (Laplacian pyramid) across view seams. Before blending, solve per-photo exposure and
  white-balance gains jointly from overlap regions (least squares), anchored to the material colour.
- **Output:** per-part base colour (or one atlas), a coverage map (texel source: photo id, synthesised or
  material), and a texel density report.
- **Builder:** the builder applies the baked textures to the actual meshes: walls, recesses and roofs seen in
  photos. **Photo panels (`add_panels`) are retired**, so relief and openings stay. Curved and tapered faces work,
  because projection does not need quads.
- **Exit gate**, on the dev set compared with T0:
  - foreign-pixel share at most 2 %;
  - seam ΔE00 at most 6 at view borders;
  - no texel from a view that does not see it (ID-buffer test).

### T6: filling unseen surfaces from the facade grammar
- Rectify the baked texture of each observed facade to an orthophoto.
- Detect the real bay grid and floor lines: periodicity plus line detection.
- Feed this into the existing M3 observations as accepted observations from source `photo_projection`, so the
  modelled openings move onto the photographed ones. This is the generic rule: a calibrated photo wins over the AI
  guess of the grid.
- Unseen faces of the same part, and the same facade family, are filled bay by bay from observed bays. Bays are
  chosen by lowest occlusion and aligned to the grid phase, never by blind repetition.
- With no observed bay, the material swatch is used.
- Every filled texel is marked `synthesised` in the coverage map and listed at A3.
- **Exit gate:**
  - window-centre alignment between texture and geometry within 5 % of the bay, or 0.15 m;
  - M3 grid alignment of at least 80 % on observed facades. This also closes the M3 gate in
    `docs/M0_M8_STATUS.md`.

### T7: quality gate, review sheets, benchmark
- **`reports/texture_quality.json`:**
  - camera residuals;
  - edge chamfer;
  - foreign-pixel share;
  - seam ΔE;
  - coverage (photo, synthesised, material);
  - texel density;
  - window alignment;
  - metric version and input digests, as in `quality.py`.
- **Overlay sheets for A3:**
  - each photo with the projected textured model at 50 % opacity;
  - wireframe on photo;
  - coverage map.
  These let the owner judge accuracy at a glance, spot by spot.
- **Gate:** the thresholds above. A failed metric blocks A3 approval with the reason. An unmeasured metric is
  `not_measurable`, never a pass.
- **Benchmark:**
  - dev set: Bưu điện, Đức Bà, UBND and Nhà hát;
  - then a new held-out set, run once.
  
  The AI judge is reported as a secondary number only, because of its noise.

---

## 4. Data contracts (new)

| File | Contents |
|---|---|
| `input/cameras.json` | `{version, photos: [{source_id, path, projection: pinhole/panorama, K, dist, R, t, residual_px, keypoints: [{id, xyz, uv, source, weight, inlier}], status: solved/failed/manual}]}` |
| `input/texture_masks/<source_id>.png` | 8-bit labels: 0 clear, 1 sky, 2 occluder, 3 other building |
| `output/textures/<part>_basecolor.(jpg/ktx2)` and `coverage.png` | Baked textures and the per-texel source |
| `reports/texture_quality.json` | Metrics, gate, digests |
| `reports/texture_review/` | Overlay sheets (never committed) |

The JSON schemas go in `schemas/`, next to the existing ones. The cache key extends `autotex.texture_key` with the
camera file's digest and the manual edits.

---

## 5. Decisions for the owner

1. **Dependency `opencv-python-headless`** (Apache-2.0). **Decided 2026-11-10: added.** It is pinned at 5.0.0.93
   in `requirements.lock` and required as `>=4.10` in `pyproject.toml`. `tests/test_opencv_dependency.py` checks
   line detection, PnP with RANSAC, GrabCut and pyramids.
2. **Optional local segmentation model** for occluders (T4). Off by default; licence review first.
3. **Retiring photo panels** changes the GLB: textures go on the real meshes instead of the overlay quads. Older
   projects rebuild their textures on the next build.
4. **The new A2.5 "Texture cameras" review step** (T3). It adds an owner step; automatic results with low residual
   could be approved in one click.

---

## 6. Risks

- **Wrong massing.** Accurate texturing needs the geometry to be roughly right. T2 measures this and reports it
  (`geometry_mismatch`) rather than hiding it. This depends on the massing regression noted on 2026-11-10.
- **Few photos** (at most 6 per project today). Unseen faces will be synthesised; the coverage map makes this
  visible. More owner photos improve coverage directly.
- **Panoramas and heavy edits** in Commons photos. T1 excludes them from projection.
- **Licences.** Only texture-licensed photos are projected. Masks and overlays are derived images and are never
  committed.
- **Performance.** Baking runs in Blender per part. The budget target is under 2 minutes per landmark on the dev
  machine.

---

## 7. Order and rough effort

| Milestone | Depends on | Size |
|---|---|---|
| T0 metrics and baseline | none | S |
| T1 intake and lens | none | S |
| T2 camera solve | T1 | L |
| T3 camera editor | T2 | M |
| T4 masks | T2 (silhouette, ID buffer) | M |
| T5 projective bake | T2, T4 | L |
| T6 grammar fill and grid feedback | T5, M3 | M |
| T7 gate, sheets, benchmark | all | S |

The first visible improvement comes after T2 and T5: photos projected with solved cameras onto the real geometry.
T6 removes the remaining "approximate" look on faces the photos do not show.

---

## 8. Progress (2026-11-10)

Code: `landmark3d/smarttex/`. Commands:
- `python -m landmark3d texture-metrics <project>` (T0);
- `python -m landmark3d cameras <project> [--photo n]` (T1 + T2).

Tests: `tests/test_smarttex_t0.py`, `_t1.py`, `_t2.py` (39 tests, offline).

### T0: done
`smarttex/metrics.py` measures three things:
- **sky_share:** sky, including sky tinted by the colour correction, connected to the top edge.
- **bay_phase:** window centre against the bay centre, from the periodic darkness at the modelled bay count.
- **seam_de:** CIEDE2000 across neighbouring faces, checked against the published reference pairs.

Results go to `reports/texture_quality.json` under `t0`. Baseline of the current `run_v2` textures (dev set,
2026-11-10):

| Project | Faces | Faces with sky > 2 % | Bay phase median (0 = centred, 0.5 = window on the bay line) | Faces off by > 0.10 bay | Seam ΔE00 median | Seams > 6 |
|---|---|---|---|---|---|---|
| Bưu điện | 16 | 7 | 0.34 | 11 of 15 | 13.97 | 12 of 16 |
| Đức Bà | 39 | 3 | 0.20 | 8 of 8 | 4.50 | 12 of 28 |
| UBND | 28 | 1 | 0.175 | 24 of 27 | 15.56 | 21 of 26 |

The numbers confirm the problems seen by eye: sky in the textures, windows off the bay grid, and colour jumps at
corners.

### T1: done
`smarttex/lens.py` works as follows:
- EXIF 35 mm focal gives a prior.
- LSD segments are linked into long chains.
- A one-parameter division model is fitted. A correction is kept only when it straightens the lines by at least
  15 %.
- Straightness is the worse of the vertical and horizontal medians.
- A photo is marked as a panorama when its aspect ratio is over 1.9 or its lines stay bent.

On the 18 dev photos:
- straightness is 0.13–0.23 % of the diagonal after correction, inside the 0.3 % gate;
- one correction was applied (UBND photo 3);
- the stitched panorama (Bưu điện photo 1) is excluded from projection.

The undistorted photos are written to `input/undistorted/`.

### T2: built; the real-photo gate is not met yet
`smarttex/keypoints.py` and `camera.py`, `run.py`:
- **Keypoints:** wall corners at base and top of every part.
- **Sketch for the AI:** a shaded render with hidden corners removed, and numbered, well separated corners.
- **Focal sweep:** the focal length is held fixed per candidate (EXIF, vanishing points, 30–100°) during the
  snapping rounds.
- **Snapping:** segments must lie along the model edge direction and on its side of the corner; long edges are
  preferred.
- **Choice of camera:** by corner support minus edge distance.
- **Edge refinement.**
- **Mismatch:** `geometry_mismatch` per part and level.
- **Output:** `input/cameras.json` (schema `schemas/cameras.schema.json`), plus overlays and the AI answer cache
  in `reports/texture_review/`.

**Synthetic renders** (known camera, 21 views):
- With coarse points at 1.5 % noise, every view is recovered: median 0.04 % of the diagonal, all within 0.5 %.
- At 3 % noise, 81 % are recovered. The failures show a higher edge distance.
- A 4 m height error on one part is reported as `geometry_mismatch`.

**Real photos, Bưu điện (photos 2–4):** no camera passes.
- The AI places only 3–5 of 14 corners with confidence, against a coarse model of this building (plan IoU 0.79).
- The first runs also showed that the RMS on the solve's own points and the edge distance can look good while the
  overlay is visibly wrong. An ornate facade has edges everywhere. Two independent checks were added to the gate:
  - corner support of at least 0.35;
  - agreement with the given points within 3 % of the diagonal.
- Now all three photos are correctly reported as failed.
- Photos 5–6 have no matched viewpoint in the spec and were skipped.

**Rotation from vanishing points (added the same day).**
- `camera.scene_vps` finds the vertical vanishing point from near-vertical segments and up to two horizontal ones
  from the rest.
- `model_directions` gives the model's dominant wall directions.
- `rotations_from_vps` enumerates the sign and direction ambiguities. It keeps rotations that keep the image upright
  and look at the building from within 70° of the matched view.
- `translation_for` solves position linearly from 2 or more points.
- These starts compete with the PnP starts under the same score. The snapping rounds now continue from the current
  camera instead of re-solving from scratch.
- **Result:** 2 points are now enough (PnP needed 4). On synthetic renders, 3 coarse points recover the camera, and
  the 3 % noise sweep went from 81 % to 86 %.
- **Bưu điện photos 2–4** (cached AI points, no new AI calls): all three now start from the vanishing-point
  rotation, and photo 2's overlay is correctly oriented. Position and scale are still off. Corner support is
  0.13–0.18 against the 0.35 gate, so all three are still correctly reported as failed. The cause is 3–5 AI points
  on a coarse model.
- A photo takes about 25 s.

**Facade-grid lines, sky outline and position search (added the same day).**
- `smarttex/grid.py` turns each visible wall into model lines: floor lines from the level height, base, wall top,
  bay lines and the two face ends. Each family is matched as a comb, the whole family shifted together, so a
  single cornice cannot pull one line onto the wrong floor. Matched lines add point-to-line residuals to the
  refinement.
- **On synthetic banded facades the grid alone barely helps**: median true error 0.47 % against 0.48 % without it.
  The failures were not refinement problems. With 3 AI points 3–7 % off, no start was near the truth.
  Corners and grid lines repeat along a facade, so a camera off by a bay still scores well. One such wrong camera
  even passed the gate.
- **Added `camera.silhouette`:** the share of model pixels on sky, and the share of the sky boundary that follows
  the model outline. This does not repeat. It now counts in the camera choice and in the gate: score at least
  0.7 when the photo shows sky.
- **Added `translation_search`:** with the rotation fixed, it searches shift and scale (coarse, then fine) for the
  best outline and corner score. It fixed 2 of 3 synthetic failure cases (error 0.04 %). The third fails because
  of the focal sweep, so it is kept out of the tests.
- **Bưu điện photos 2–4 with the search: still no camera passes, and about 3 minutes per photo.** The overlays
  remain visibly wrong.
- **What blocks the real photos is no longer the solver alone.** The model differs from the building, the AI gives
  only 3–5 points, and trees cut the skyline. Without trusted correspondences, no automatic score can tell a
  near-miss from a correct camera on these facades.
- The test suite now takes about 2 minutes (39 Smart Texture tests).

Next steps for T2, in order:
1. ~~Rotation from vanishing points.~~ Done.
2. ~~Denser correspondences from the facade grid.~~ Done; little gain without good starts.
3. **The T3 editor (recommended next).** The owner drags 4–6 points; the solve and every check above run at once.
   It also gives the golden set that the T2 exit gate needs.
4. **Better focal estimation:** EXIF is missing from these Commons photos, and the vanishing-point focal is often
   unreliable.
5. **Speed:** the position search is pure Python and needs vectorising.

Earlier list (kept for history):
2. **Denser correspondences** from the facade grid (floor lines, bay lines and the entrance), not only corners.
3. **The T3 editor**, so the owner can place points where the AI cannot.
4. **A golden correspondence set** for the dev set, which the T2 exit gate needs and which T3 provides.

---

## 9. Case study: Đức Bà, fixes to the current automatic textures (2026-11-11)

**Why.** T2–T5 are not usable yet. In the meantime, `autotex.run_v2` (the texture step in the build) gets
deterministic fixes to its photo crops. Đức Bà is the case study; Bưu điện and UBND check that the fixes hold in
general. There are no landmark-specific rules. Code: `smarttex/quads.py`, wired into `autotex.run_v2` (texture key
suffix `-v5`). Tests: `tests/test_smarttex_quads.py`.

**What Đức Bà showed** (32 face textures):
- 28 of the 32 were one repeated bay tile.
- In 22 of the 28 the window period was too weak to measure (strength < 0.25): the crop did not follow the real
  bays.
- Crops included roof slopes and sky.
- One east-facade crop was taken at a steep angle.
- Untextured walls kept a flat colour next to dark photo brick.
- The AI's swatch boxes landed on sky or the wrong surface. The colour check rejected them, correctly.

**Fixes** (each generic and measurable):
1. **Corner snapping (`refine_quad`).** Each quad edge moves onto the strongest image line along it, and the corners
   are re-intersected. Corner moves are capped at 5 % of the diagonal, and the quad must stay convex.
2. **Oblique crops.** A crop whose opposite edges differ by more than 1.8× is rejected rather than stretched.
3. **Window centring (`centre_bay`).** Each bay tile is rolled so its window sits mid-bay, as in the geometry.
4. **Deterministic checks before the AI's own check (`auto_qa`).**
   - Sky at the top is trimmed when at least 60 % of the wall remains; otherwise the texture is dropped.
   - Crops dominated by diagonal lines (roof or gable slopes) are dropped.
5. **Colour per part (`match_part_colours`).** All faces of a part are brought to the part's median colour.
6. **Wall swatches.** The AI is now asked for wall swatches as well as roof ones. When none passes the colour
   check, a plain wall sample is cut from the kept face textures of that material (`wall_swatch`).
7. **Cache.** The AI plan and AI check answers are cached in `reports/autotex/plan_ai.json` and `qa_ai.json`, so the
   deterministic steps can be re-run and compared without new AI calls.
   `reports/autotex/notes.json` records what happened to each crop.

**Results (T0 metrics; earlier run_v2 → fixed):**

| Project | Faces with sky > 2 % | Bay phase median (bays) | Faces off grid > 0.10 | Seam ΔE00 median | Seams > 6 |
|---|---|---|---|---|---|
| Đức Bà | 3/39 → 0/32 | 0.20 → 0.006 | 8/8 → 4/12 | 4.50 → 3.67 | 12/28 → 8/20 |
| Bưu điện | 7/16 → 0/20 | 0.34 → 0.006 | 11/15 → 4/20 | 13.97 → 6.06 | 12/16 → 10/20 |
| UBND | 1/28 → 0/24 | 0.175 → 0.002 | 24/27 → 0/15 | 15.56 → 6.75 | 21/26 → 15/24 |

Earlier = 2026-11-10 textures. Đức Bà's same-day rerun of the old method was worse still: phase 0.45, 16/24 seams
over the limit.

**Visual check.** Đức Bà and Bưu điện are more even: the walls read as one material and the windows sit in their
bays. UBND is mixed: some wings now have a dark ground floor, and one wall swatch could not be found because the
spec's cream colour is far from the photographed walls.

**Still wrong, and why:**
- The AI sometimes picks the right photo but the wrong region. For example, the nave tile shows the aisle roofs
  below the clerestory. Snapping makes the crop exact, but exact on the wrong thing.
- A single repeated bay looks mechanical on long walls.
- Unique faces are lost when the AI's check drops them; the Bưu điện portal fell back to bays.
- Roof swatches still fail, because the AI's boxes are wrong.

**Next generic steps:**
1. **Check each crop against the facade grammar.** The floor count, from horizontal periodicity, and the window
   count per bay must match the part's `level_height` and bays; otherwise reject the crop. This catches a right
   photo with the wrong region.
2. **Tiles of 2–3 bays** with their natural variation instead of one bay.
3. **Roof swatches** sampled from the roof area above the snapped wall-top line.
4. **The T3 editor** for exact, spot-by-spot corrections.
5. **T5:** projection from solved cameras.

### 9.1 Follow-up: wrong-region crops (2026-11-11)

**Facade-grammar check: measured, not adopted.**
- The test: row darkness of each bay tile against the model's expected window rows, computed from `level_height`,
  `sill` and `opening_height`, and compared by normalised correlation.
- It flagged some wrong crops: the Đức Bà nave (0.10) and the UBND tower stage (−0.63).
- It also scored plausible crops low: the Đức Bà portal (−0.15) and the Bưu điện upper stage (−0.57).
- The spec's opening heights are themselves AI guesses, so they cannot judge a photo. The check is not used as a
  gate.

**Location-aware AI check: adopted** (texture key `-v6`).
- Each cell of the AI check sheet now shows the source photo with the crop outlined in red, next to the rectified
  texture.
- The prompt gives each part's wall height, floors, base and the building's height range (`_part_note`). The AI is
  told to drop crops on a neighbouring block, a roof, or only part of the height.
- One AI call per project, as before. The plan answer stays cached.

| Project | Photo faces kept | Faces off grid > 0.10 | Seam ΔE00 median | Seams > 6 |
|---|---|---|---|---|
| Đức Bà | 32 → 14 | 4/12 → 0/8 | 3.67 → 1.93 | 8/20 → 0/8 |
| Bưu điện | 20 → 16 | 4/20 → 4/16 | 6.06 → 5.84 | 10/20 → 8/16 |
| UBND | 24 → 24 | 0/15 → 4/15 | 6.75 → 5.77 | 15/24 → 12/24 |

The AI's reasons match the faults seen by eye:
- the nave crop sat on a small upper block;
- the aisle crop included a motorbike and a lower roofline;
- the crossing crop was on the roof;
- the transept crop was sheared.

Walls whose crops were dropped now get the wall swatch, so Đức Bà reads as one brick building. The towers, apse
chapels, gable and portal keep their photo faces.

**What remains:**
- The brick swatch is blotchy at large scale, and roof swatches still fail.
- Fewer photo faces means more procedural walls. More photos, or the T3/T5 work, are needed to cover them
  accurately.

### 9.2 Follow-up: swatches (2026-11-11)

**Problem.** The repeat textures (swatches) looked blotchy when tiled. There were three causes:
- the samples contained pilasters, shadows or roof ridges;
- the old `make_tileable` blended in a half-shifted copy, which left a cross-shaped ghost;
- the light fell off across the sample.

Roof swatches failed everywhere.

**Changes** (`smarttex/quads.py`, `autotex._swatches_v2`, texture key `-v9`):
- **`structure`:** the luminance std after a blur at 1/12 of the sample size. Fine brick or tile texture is blurred
  away; pilasters, cornices, shadows and ridges remain. A swatch must score 9 or less, otherwise the material keeps
  its plain colour, because a structured swatch repeats visibly.
- **`plain_patch`:** the largest plain sample of the material colour, tried at 128 → 32 px (4 m → 1 m). Smooth
  stucco is often plain only over a small area.
- **`seamless`:**
  - flatten the illumination: divide by a heavy blur;
  - take the periodic component (Moisan's periodic plus smooth decomposition): it tiles with no seam and no
    ghosting.
- **Candidates per material:**
  - the AI's box (if its colour matches);
  - plain areas of the kept, colour-corrected face textures (walls);
  - plain areas of the band just above each snapped wall-top edge (roofs, `roof_region`).
  
  The plainest wins.

**Result.**
- **Walls:** all three now have a wall swatch (Đức Bà brick 2 m, Bưu điện stucco 1 m, UBND cream 1.5 m). The
  untextured walls read as the same material as the photo faces, and Đức Bà's brick is no longer blotchy.
- **Roofs:** only Đức Bà has roof swatches. Its "roof" sample came from brick just above a wall crop: the right
  colour, the wrong surface. Bưu điện and UBND have no clean roof area above their wall crops (balustrades), and
  their AI roof boxes failed the plainness check, so their roofs keep a plain colour.
- **T0 metrics:** unchanged (they measure the face textures, not the swatches).
- **Tests:** 583 pass.

**Next for swatches:** check that a roof sample has roof texture (tile rows parallel to the eave) rather than wall
brick, and search higher above the wall top, past the cornice or balustrade.

### 9.3 Follow-up: multi-bay tiles and the real bay (2026-11-11)

**Multi-bay tiles: measured, not adopted.**
- `extend_bays` widens the AI's bay quad by whole bays through the bay's own homography.
- `bay_match` correlates neighbouring bays. It came out low everywhere (−0.33 to 0.43), even on clearly periodic
  facades.
- The strips showed why: the AI's "bay" is not one real bay. On UBND the central hall's quad held 1.7 real bays
  and the entrance's 1.3, so its neighbours were not copies of it.

**Real-bay tiles: adopted where a period is clear** (`window_period`, `real_bay_tile`, `best_bay_tile`, key
`-v10`).
- A strip of up to 5 AI bays is rectified around the quad.
- The window period is found from the autocorrelation of column darkness, and exactly one period is cut with the
  window centred. One real bay then maps onto one model bay.
- Only used when the period is clear (strength ≥ 0.3) and between 0.6 and 2.5 real bays per AI bay. Outside that
  range the period is ornament, as on the UBND clock tower (4.7). Otherwise the old crop stays.

**Results:**
- A clear period was found on 3 of 25 tiles (UBND central hall and entrance, Bưu điện upper stage). On ornate or
  brick facades the column darkness is not periodic enough.
- The UBND hall and entrance tiles now hold exactly one arch, centred.
- The new tiles changed the AI check sheets, so the check ran again. It dropped more partial UBND crops (12 photo
  faces now, 24 before), and those walls now take the cream wall swatch.
- T0:
  - Đức Bà unchanged;
  - Bưu điện seam median 5.84 → 7.72 (the upper stage tile changed colour);
  - UBND seams over the limit 12/24 → 6/12.

**Roof swatches: not changed this round.** No deterministic test was found that tells roof tiles from wall brick
of the same colour in these photos.

---

## 10. T3 camera editor (2026-11-11)

**Where.** Project page → card "Camera ảnh" → "Mở trình chỉnh camera". One tab per texture photo.

**What the owner does:**
1. Choose a model corner from the list. The numbers match the model sketch beside the photo.
2. Click where that corner really is in the photo. The editor moves on to the next unplaced corner.
3. Drag pink points (placed) or cyan points (projected by the current camera) to adjust. Right-click a pink point
   to delete it.
4. Press "Giải camera". The camera is solved from the points at once, and the wireframe and checks update.

**Solve from hand-placed points** (`camera.solve_manual`):
- The points are trusted as given; no snapping.
- 4 or more points: PnP over the focal candidates, then LM with the focal free (weak prior from EXIF or vanishing
  points).
- 2–3 points: rotation from the vanishing points, position from the points.
- The same checks as the automatic solve: corner support, silhouette, edge distance. Points more than 1.2 % of
  the diagonal off their projection are named (`off_points`).
- Four fitting points replace the automatic solve's minimum of six.

**Storage.** `input/cameras.json`: `status: "manual"`, `manual: {keypoint: [u, v]}`, keypoints with
`source: "manual"`. Pixels are in the undistorted image (`input/undistorted/`), made on first open if T2 has not
run. The camera feeds T5 later; nothing in the build uses it yet.

**API:**
- `GET /api/projects/{id}/cameras`;
- `GET /api/projects/{id}/cameras/{n}`;
- `PUT /api/projects/{id}/cameras/{n}` with `{"points": {...}}` (empty clears).

Code: `smarttex/run.py` (`editor_*`), `server.py`, `web/app.js` (`renderCameras` …).

**Tests.** `tests/test_smarttex_t3.py` (6, offline):
- true points recover the camera (RMS < 2 px, gate clean);
- 2 points suffice with building lines;
- a misplaced point is named;
- 1 point fails with a reason;
- bad input is refused.

The editor was checked in the browser on Bưu điện: the photo, wireframe, sketch, corner list, point placement
and checks all display, with no console or server errors. No hand-placed points were saved for real photos; that
is the owner's step.

**Next.**
- **Golden set:** the owner places points on the dev photos. The T2 exit gate can then be measured against them.
- **T5:** project the photos with these cameras.

---

## 11. T5 first slice: projective texturing (2026-11-11)

**What it does** (`smarttex/bake.py`). Every wall face of the model is sampled on a 32 px/m grid of points on the
real wall plane. Each point is projected into every photo whose camera is solved or hand-placed and passes its
checks (`input/cameras.json`). A photo may colour a point only when:
- the point is in front of the camera and inside the image;
- the face is the visible surface at that pixel (face-id render of the model from that camera, so a part behind
  another takes nothing);
- the face is seen within about 75° of straight on;
- the pixel is not sky.

Photos are weighted by cos(angle) × pixels per metre, raised to the 4th power. The best view dominates, but
overlaps blend without hard seams. Holes are inpainted.

**Where it is used.** `autotex.run_v2`:
- Faces covered at least 70 % by projection replace the AI crops on those faces (`kind: "projected"`).
- Without AI, the step still runs: the pipeline now calls it with or without a provider, and only projected faces
  are made then.
- The texture cache key includes `input/cameras.json`, so editing a camera in T3 rebuilds the textures. Texture
  key suffix `-v11`.

**Checked:**
- `tests/test_smarttex_t5.py` (6 tests):
  - the photo's windows land on the model's windows (window-mask darkness);
  - faces turned away or hidden take nothing;
  - two cameras cover more;
  - holes are filled;
  - the texture step uses projection, is cached, and rebuilds when cameras change.
- End to end through Blender on a synthetic hall: hand-placed camera → 3 projected faces → build ok → the
  windows appear on the model's walls and tower in the renders.

**Not yet:**
- No real project has a passing camera yet. T5 changes real models only after the owner places points in the T3
  editor (or T2 succeeds).
- The plan's other T5 parts are still to do:
  - textures on the real meshes (UV atlas) instead of panel quads in front of the wall;
  - exposure matching between photos;
  - roofs;
  - the T4 occluder masks (trees, people, vehicles). Only sky is masked now.

---

## 12. T4 masks (2026-11-11)

**What it does** (`smarttex/masks.py`). Each photo with a usable camera gets a label image in
`input/texture_masks/<photo>.png` (undistorted pixels). Labels: 0 clear, 1 sky, 2 occluder, 3 other building.
- **Sky:** `metrics.sky_mask`, connected to the top edge.
- **Vegetation** (deterministic): excess green plus green hue, textured (leaves). Painted shutters are smooth, so
  they do not count.
  - Then an opening with a large disc (3.5 % of the short side), grown back inside the mask. Foliage is thick;
    painted ornament bands are thin, even when they touch a tree.
  - Long, sparse strips are dropped as ornament bands.
- **Other occluders:** one AI look per photo gives boxes with a class (tree, person, vehicle, pole, sign, flag,
  awning, other building). Answers are cached in `reports/texture_review/photo<n>_mask_ai.json`.
  - GrabCut cuts each object's shape out of its padded box.
  - When GrabCut keeps more than 85 % of the box, nothing was separated, so the AI's own box is used.
  - Other buildings keep their box.
- **Use.** The T5 projection skips every non-zero label, so a tree or a person is never painted onto a wall.
  Masks are made in the texture step for the photos with usable cameras (AI boxes when AI is available,
  otherwise sky and vegetation only).

**Checked:**
- `tests/test_smarttex_t4.py` (5 tests):
  - the tree is found and the smooth green shutters are not (IoU > 0.7);
  - a textured green ornament band is not foliage;
  - GrabCut cuts a car out of its box (IoU > 0.7);
  - the labels combine;
  - projection loses coverage where the photo is masked.
- **Real photos** (5 AI calls: Đức Bà 1–3, Bưu điện 2 and 4). Sky, trees, palms, street lamps, flags, people
  and vehicles are masked.
  - The first version took Bưu điện's green-painted cornice bands for foliage. The thickness rule fixed it, and
    the facade is now clear.
  - People standing in groups on the paving get rectangular masks, where GrabCut cannot separate them from the
    paving. That is harmless, since the paving is not projected onto walls.
- **Not measured:** the plan's exit gate (occluder IoU ≥ 0.7 on a labelled sample from the dev set) needs
  hand-labelled masks.

---

## 13. AI auto-place for the T3 editor (2026-11-11)

**Why.** The owner found hand placement too hard ("AI tự phân tích ra điểm đi").

**Feature.** The editor button "AI tự đặt điểm" (`POST /api/projects/{id}/cameras/{n}/auto`,
`run.auto_place`):
1. Rough corners from the existing AI keypoint step (sketch plus photo).
2. Each corner refined in one AI look at a sheet of zoomed crops (10 % of the photo, fine grid, red cross on the
   rough guess) (`ask_refine`).
3. The camera solved from the refined points as if hand-placed.
4. A wrong AI point is removed by leave-one-out. A wrong point bends the camera so that a right one looks worst,
   so dropping the worst residual removes the wrong point. Hand-placed points are never dropped, only named.

The points stay editable and are marked `points_by: "ai"`. Tests (fake AI): rough points 2.5 % off refined →
gate clean; one wrong point is dropped and RMS is under 2 px; no AI → 409.

**Real result, Bưu điện photos 2–4 (6 AI calls): no camera.**
- The refinement worked as asked: each crop's point sat on a real edge.
- But the rough step had matched model corners to the wrong real places. Crop 1 of photo 2 is a window arch,
  crops 2–4 are roof edges and ornaments. The model's corners have no clear counterpart in these photos, because
  the Bưu điện massing differs from the building (plan IoU 0.79): the central block's size and the wings' corners
  do not match.
- Photo 3 is a night photo largely hidden by trees.
- The cameras.json from before the run was restored, so the editor does not show wrong AI points.

**Conclusion.** Automatic corner placement needs a model whose corners exist in the photo. For Bưu điện the
blocker is the massing, not the corner finding. The benchmark found the same thing on 2026-11-10: AI massing
regressed in plan on all four landmarks.

### 13.1 Retry after the massing fit (2026-11-11)

**Changes:**
- The sketch is now seeded only from a camera that passed its checks. Before, a failed T2 camera drew the model
  from the wrong side.
- Top-of-wall corners are labelled first; bases are often behind fences, steps or cars.

**Result on Bưu điện photos 2 and 4 (plan IoU now 0.965): still no camera.**
- The AI's rough correspondences for photo 2 look plausible: the central pavilion's corners, the wing ends.
- Even the rough points alone give no consistent camera: RMS 83 px, focal 2347 px, no corner support.
- The model's elevations differ from the building. Part heights, the width of the central pavilion and its
  setback are AI estimates. Points that are right in the photo do not fit one pinhole view of that model.
- The zoomed refinement answered about 0.5, 0.5 for every crop: it kept the guess rather than locating the corner.
  It adds nothing as implemented.
- The cameras.json from before the run was restored.

**What this means.** A plan fitted to the footprint is not enough for a camera solve. The heights and the widths
of the volumes seen in elevation must also match. Options:
- fit the heights from the photos with the same camera (a joint solve, bundle-adjustment-like, of the camera and
  part heights, using the plan as fixed);
- get measured heights (owner or survey data);
- accept that T5 projection needs a better model and keep the improved automatic textures (section 9) as the
  production path.

### 13.2 Part heights from photos (2026-11-11)

**What it is** (`smarttex/heights.py`). One joint solve of every photo's camera and the wall heights of the parts,
with the plan fixed: it is fitted to the footprint and carries the metric scale.
- Keypoints are wall corners: base at `base_z`, top at `base_z + height`.
- A part standing on another stays on it when that part's height changes.
- Huber reprojection errors, weak priors keeping the heights near the AI's (within 0.6–1.7×), outlier drop, then
  a second solve.
- Accepted only with at least 2 photos and at least 8 more observed coordinates than unknowns (7 per camera plus
  1 per height), and RMS ≤ 1.2 % of the diagonal.
- `apply` writes the heights and lifts parts standing on changed parts.

**Synthetic** (hall 12 m and tower 14 m written in the spec as 9 m and 20 m; 2 photos, all corners):

| Point noise | Accepted | Median height error |
|---|---|---|
| 0.4 % | yes | within 6–8 % (tests) |
| 1 % | 3 of 4 | 4–7 % |
| 2 % | 0 of 4 | 8–18 % |
| 3 % | 0 of 4 | 15–24 % |

At 2–3 % the guard correctly refuses.

**Bưu điện, real AI points (photos 2 and 4).**
- Photo 4 had only 4 AI points, too few to start a camera.
- Photo 2 alone had 6 points left for 10 unknowns. The fit moved the central block from 12.2 m to 17 m, which is
  meaningless, and the guard now refuses it. **No heights were written to the spec.**

**Conclusion.** The method works when corner points are precise to about 1 % of the diagonal. The AI's points are
2–3 % off, only 4–8 per photo, and the zoomed refinement does not improve them. What blocks camera solving and
height fitting on real photos is precise point placement. Precise points can come from:
- hand placement in the T3 editor, which takes a few corners per photo;
- a corner detector constrained by the model's edge directions, once one camera is roughly right.

The heights solver is ready for either.

---

## 14. Generated facade textures with Codex image generation (2026-11-11)

**Why.** Photo reuse (crops or projection) needs good views and, for projection, solved cameras. The owner pointed
out that the signed-in Codex CLI can generate images. Generation needs neither, gives clean faces (no trees,
people or sky), and covers faces no photo shows. Codex CLI 0.162 has `image_generation` (stable, on) through the
web login, so no API key is needed, which respects the project rule.

**What it does** (`smarttex/genfacade.py`, setting `generated_textures`, default on). For each part with a facade
grid:
1. Draw a layout from the model: n bays (up to 3; at most the part's narrowest face), floors from the level
   height, openings with their width, height, sill and arched or flat heads.
2. Call `codex exec` (workspace-write sandbox in a temp dir, prompt on stdin) with up to two reference photos
   (style, material, colour) plus the layout. The prompt asks for an orthographic elevation with the same
   proportions and openings exactly on the layout, filling the frame from wall top to wall base, with no sky,
   ground, people, trees or text.
3. Check deterministically: no sky (≤ 2 %), windows centred in the model's bays (bay phase ≤ 0.18), wall colour
   within 95 of the material. On failure, regenerate once with the problems named.
4. Cache by prompt, layout and references in `reports/genfacade/<part>.png` and `.json`.
5. Tile over the part's faces by whole bays, so the windows stay on the model's grid.

**Priority per face:** projected (T5) > unique photo face > generated > photo bay crop. The entries carry licence
`ai_generated` (now an allowed texture licence), `generated: true`, and author "codex image generation, style from
<photos>". Release notes list them as such.

**Tests.** `tests/test_genfacade.py` (5, fake generator):
- the layout follows the grid;
- the checks pass a faithful image and name the problems of others (sky, colour, phase);
- one retry with feedback, then the cache is used;
- tiles keep the windows on the bay grid;
- faces already textured are left alone.

`tests/conftest.py` disables the real generator in every test.

**Bưu điện (first real run).**
- 8 parts, all accepted on the first attempt, about 2 minutes each. The whole texture step took 18 minutes with
  the build.
- 43 faces generated.
- The model now reads as the real building: yellow walls, arched first-floor windows, green shutters, white
  mouldings and green bands, even on all sides. Before, it was a patchwork of photo crops with dark and wrong
  regions.
- **Trade-off:** generated faces are photo panels, and the builder drops the modelled openings behind a panel.
  The GLB went from 14,569 to 3,675 triangles: flat walls with pictures. Relief would come back with textures on
  the real meshes (the UV-atlas part of T5) instead of panels.

### 14.1 Generated textures on the real mesh (2026-11-11)

Generated facades follow the model's own bay grid, so they no longer need a flat panel in front of the wall.
- `textures.build_job` marks generated entries `skin: true`.
- The builder (`blender_build.apply_skins`) keeps the modelled detail under them.
- After the part mesh is linked, every face of that part on a textured facade plane gets the image, with UVs from
  its position along the facade and its height:
  - within 0.9 m in front of or behind the plane;
  - any face except those facing back into the building, so wall, pilaster fronts and sides, sills and cornice
    tops are all covered.
- Openings keep their own material, so the modelled window recesses sit exactly under the painted windows.
- Photo panels (crops, projections) stay overlay quads: they follow the photo, not the model's grid.

**Bưu điện:** 19,321 triangles with 7,584 skinned faces. The flat-panel version had 3,675 triangles. The
cornices, pilasters and window recesses keep their relief under the generated texture. The first version left
pilaster sides white; skinning the side and top faces fixed it. The CI smoke build still passes.
