# M0–M8 status (Detail Engine v2)

Date: 2026-11-10. Scope: `docs/DETAIL_ENGINE_V2_PLAN.md` section 10 and `docs/PRODUCTION_BASELINE.md` section 5.
This report replaces the milestone table in `docs/M2_M5_RUN_REPORT.md`, which predates the later commits.
The `detail_engine_v2` flag stays **off** by default in code (`settings.py`). It is switched on in this machine's
local settings for the 2026-11-10 benchmark. Nothing in this pass was committed, pushed or released.

Labels used below:
- **Built**: the code exists and offline tests cover it.
- **Measured**: a deterministic check or a real Blender build has produced a number.
- **Not measured**: the exit gate needs the AI benchmark, a held-out set or real photos, and none was run.

## Summary

| Milestone | Built | Exit gate |
|---|---|---|
| M0 correctness foundation | all seven tasks, with tests for each acceptance criterion | met by tests (`test_m0_integrity.py`) |
| M1 contracts and accounting | types, registry, schemas, v1 adapter, accounting, flag | met by tests; library version is now 1.1.0 |
| M2 opening assemblies | LOD0–2, allocator, arch segments now scale with size and LOD, unknown materials block | partly: see M2 below; on the dev set the judge sees no facade gain (see the benchmark below) |
| M3 grammar and observations | observation step, grammar, A2 facade step, corrections, per-side grids, grid-alignment metric | not measured: no AI observations were run |
| M4 articulation and roof | 21 registered components, all eight plan slices covered; mansard and gambrel roofs | placement checked in Blender; on the dev set no AI spec requests a component, so nothing is placed |
| M5 PBR and baking | material checks, texel warnings, AO and normal bakes, KTX2, emissive slot, mip-safe atlas padding | validator zero errors on earlier samples; not rerun on new builds |
| M6 quality loop | metrics with plan IoU, silhouette IoU, edge distance, semantic and line scores, draw calls, coverage; baseline thresholds; gate wired into build approval | plan IoU measured on 4 real builds: all 4 fail the 0.95 gate; view metrics need solved reference cameras |
| M7 photogrammetry (optional) | readiness checks, pose checks (coverage, overlap, diversity), adapter contract, gravity and wall placement, planes, dense normal bake | wall placement not validated on a reconstruction: see M7 below |
| M8 generative components (experimental) | adapter, admission gate, mesh cleaning, decimation, anchors, collision and budget placement, A2/A3 review items | no provider is approved, so nothing is admitted |

Tests: 530 offline tests pass (450 before this pass). `git diff --check` is clean.
Blender: the CI smoke build passes; a flag-on synthetic build with the new roofs and components passes (below).

## What this pass added

### M2
- `openings.arch_segments(width, lod)`: the half circle keeps its chord under 0.6 / 0.3 / 0.15 m at LOD0 / 1 / 2,
  between 4 and 32 segments. Before, every arch had 8 segments.
- `pipeline.unknown_material_issues`: a part or feature that names an undefined material is a blocking issue at
  approval. Before, the builder replaced it silently and only logged a warning.
- Still open: the wall is not cut through (the recess is a projecting surround). Before/after close-up fixtures
  (plan task 7) were not rendered.

### M3
- `facade_grid.alignment(spec, observations)`: the share of observed floor and bay lines that the built grid
  reproduces within 5 % (baseline 5.3), counting only accepted observations (confidence 0.7 or more) on applied sides.
  The result goes into `reports/quality.json`.
- The 80 % gate is **not measured**: that needs AI observations on benchmark close-ups.

### M4
- Roof types `mansard` and `gambrel` are real geometry now. Before, they were mapped to hip and gable. OSM tags map to
  them, the AI prompt lists them, and the schema accepts them.
- New generators: `string_course`, `roof_gutter` (fascia and gutter), `bundled_tubes`, `sign_panel` (sign or frieze).
  Together with the earlier slices, every item of the plan's M4 order has a generator. Deep eaves and multi-tier
  pavilions were already handled by `overhang` and `skirts` in `geom.py`.
- Per-slice "dev improvement" is **not measured** (it needs the AI benchmark).

### M5
- Emissive slot: `emissive` and `emissive_strength` on spec materials, with range checks. The GLB carries
  `emissiveFactor` and `KHR_materials_emissive_strength` (checked on the flag-on build).
- `materials.pbr_slots` names the glTF slots each material fills.
- Panel atlas: an 8 px gutter around each cell, filled with the cell's own edge pixels. The check
  `padding_ok` passes for the generated atlas. Before, the gutter was 2 px and white.

### M6
- `quality.py` version 1.1.0 adds:
  - plan IoU (rasterised on a fixed grid);
  - mask IoU for silhouettes;
  - edge distance (central-difference edges, two-pass chamfer distance, as a share of the diagonal);
  - semantic recall and precision (one-to-one matching by class and box IoU);
  - line match;
  - frame coverage;
  - draw calls and facade coverage from the build.
- `quality.evaluate` applies the baseline F0/F1 thresholds. A metric without evidence is `not_measurable`, never a
  pass.
- Every successful build writes `reports/quality.json`: metrics, inputs, version, digest, gate and baseline.
  With the flag on, gate problems block the build approval. With the flag off, they are only reported.
- Silhouette IoU and edge distance need a solved or approved camera per reference photo. They are implemented but not
  fed automatically yet.

### M7
- `capture.assess_poses(cameras)`: coverage (12 directions around the building), overlap (gap between neighbouring
  cameras) and viewpoint diversity (height or distance spread), from solved poses. The centre is where the optical
  axes meet, so a one-sided set is not mistaken for full coverage.
- `align_from_walls` now refuses a one-sided or folded camera set with that reason. Before, the plane fit failed with
  "no two opposite pairs" and gave no cause.
- **Finding on synthetic data.** A level ring of 32 renders of a hall with identical arcades registered all 32 images
  in COLMAP. But the recovered view directions repeat every 90 degrees: the four facades were reconstructed on top of
  each other. Adding a corner tower and a porch with a different texture did not stop the fold. The facade's repeated
  pattern outweighs the unique features. Wall placement is therefore still **not validated** on a reconstruction.
  Possible next steps:
  - pose priors from photo GPS;
  - a capture rule asking for corner shots;
  - a test scene with less repetition.

### M8
- `generative.clean` merges duplicate vertices and drops degenerate, repeated and broken faces.
- `decimate` reduces a mesh over the triangle limit by vertex clustering. It refuses when the clusters would exceed
  10 % of the component size. Before, every over-limit mesh was refused.
- `anchors` and `place` give the base and top anchors and check the part bounds, collisions and the part budget.
- `review_items` puts every generated component into the A2/A3 issues as "generated, not observed".
- `APPROVED_PROVIDERS` is still empty, so nothing is admitted.

## Blender checks in this pass
- CI smoke build: ok, 744 triangles, 8 renders.
- Flag-on synthetic build: a mansard hall with string course, gutter and an emissive frieze; a gambrel wing; a
  bundled-tube tower. Results:
  - ok, no errors and no warnings;
  - 8,912 triangles, 6 materials, 9 draw calls;
  - all four articulation items placed.
  The renders are in the session scratchpad and are not committed.

## Flag-on dev benchmark (2026-11-10)
Run: UBND from research, then the dev set, with `detail_engine_v2` on. All four builds succeed and wait at A3.
- The M6 gate fails plan IoU on all four. The new AI specs are worse in plan than the earlier ones: plan IoU of
  the spec itself, before any detail, earlier → now:
  - UBND 0.94 → 0.44;
  - Bưu điện 1.00 → 0.79;
  - Đức Bà 0.94 → 0.70;
  - Bitexco 0.79 → 0.76.
  The cause is the AI massing step, not the flag. The 0.95 threshold may be too strict for towers: Bitexco's earlier
  spec also scores 0.79. Not investigated.
- No articulation component was placed in any project. The AI specs do not request them, so the M4 generators are
  unused in practice.
- Same-spec A/B: the same spec judged with the flag off, then on (3 readings each).

  | Project | Off | On |
  |---|---|---|
  | UBND | 5.33 | 5.33 |
  | Bưu điện | 4.33 | 4.67 |
  | Đức Bà | 5.33 | 5.33 |
  | Bitexco | 6.67 | 5.00 |

  Bitexco's off and on renders are pixel-identical (no window assemblies apply to its curtain walls), yet the
  scores differ by 1.67. The judge's noise is therefore larger than any effect of the flag. The flag's effect on
  quality is **not resolved** by the AI judge. Facade scores stay at 2 with LOD2 openings.

Open problems from this run (not started):
- **Judge noise.** A 3-reading mean moves by up to 1.67 on identical images. "Keep if the score does not drop" cannot
  separate a real change from noise at this sample size. Possible fixes: more readings, a paired comparison of both
  images in one prompt, or deterministic metrics as the primary gate.
- **AI massing regressed in plan.** Plan IoU fell on all four. The M6 gate catches it at A3, but the massing step
  does not use plan IoU while generating.
- **Articulation unused.** The massing and facade prompts do not offer the M4 components.
- **Plan IoU threshold for towers.** Bitexco scores 0.79 even with the earlier spec.

The four projects wait at A3 for the owner's decision. The flag-off copies are in
`projects/_baseline_flag_off_2026-11-10/`; the judge sheets are in `reports/judge_flag_off/` and
`reports/judge_flag_on/` of each project (not committed).

## Not done, and why
- **Held-out set.** None exists. The 2026-11-08 set has been seen, so a new list of landmarks is needed first.
- **Silhouette and edge metrics fed from real reference photos.** They need solved cameras per photo (M7, or approved
  views). The functions exist.
- **M2 close-up before/after fixtures, and cutting real holes in the wall.**
- **M7 wall placement on a real reconstruction.** Blocked by the fold described above.
- **M8 provider.** None has passed the held-out benchmark and the licence review.

## Massing fitted to the footprint (2026-11-11)

**Root cause of the plan IoU regression.** The AI now builds every volume as rectangles placed from its reading of
the satellite image. The shapes are right, but the whole model, or single volumes, come out shifted by 2–9 m,
turned by up to 4° and scaled by 5–13 %. The earlier specs reused the OSM outline polygons, so they matched.

**Fix.** `landmark3d/plan_fit.py` fits the plan to the measured footprint, deterministically:
1. Fit the whole model: shift, turn and scale (pattern search, up to 12 m / 12° / 20 %).
2. Fit each ground volume, largest first: shift (up to 30 % of its size) and stretch (up to 20 %).
3. Volumes standing on another one move with it and keep their size.

A step is kept only when plan IoU improves and the structure check reports no new error. It runs in `build_spec`
right after the photo review, so the judge and the facade rounds see the fitted plan. The report is stored in
`spec.plan_fit` and in the log. For existing projects: `python -m landmark3d refit-plan <project>`, which saves the
spec and sends the project back to A2.

**Result on the four dev specs** (no AI calls; structure errors stayed at 0):

| Project | Plan IoU before | After |
|---|---|---|
| UBND | 0.45 | 0.82 |
| Bưu điện | 0.79 | 0.97 |
| Đức Bà | 0.71 | 0.96 |
| Bitexco | 0.76 | 0.92 |

Bưu điện and Đức Bà now pass the 0.95 gate. UBND and Bitexco do not: UBND's AI volumes do not cover the
south-west wing's return, and Bitexco's podium outline is irregular. The four projects were sent back to A2, and
their earlier specs are kept as `spec/landmark_spec.before_refit_2026-11-11.json`. The judge was not re-run, since
that costs quota.

Tests: `tests/test_plan_fit.py` (6).
