# M2–M5 run report (Detail Engine v2)

> Superseded by [`M0_M8_STATUS.md`](M0_M8_STATUS.md) (2026-11-10). Kept for history.

Scope: `docs/DETAIL_ENGINE_V2_PLAN.md` section 10 (M2–M5) and the exit gates in `docs/PRODUCTION_BASELINE.md`.
Status: **partial**. The offline parts of M2–M5 are implemented and tested. Several exit gates are not met, and each
one is listed below with its reason. The flag `detail_engine_v2` stays **off** by default. Nothing was pushed or released.

## Summary

| Milestone | Built | Exit gate | Status |
|---|---|---|---|
| M2 opening assemblies | LOD0–LOD2 assemblies, window allocator, flag-gated builder | partly met | partial |
| M3 grammar from observations | compile, fallback policy, spec v2 part | not met (AI step and A2 panel missing) | partial |
| M4 articulation and roof | 3 of 8 slices (plinth, brise-soleil array, chimney stack) | not met (not wired, no dev measurement) | partial |
| M5 deterministic PBR and export checks | material ranges, arch non-metal rule, texel ratio, atlas padding, attribution | not met (no normal/AO bake, KTX2, GLB validator run) | partial |

Tests: 322 offline tests pass (249 before this run). `git diff --check` is clean. No landmark names appear in the new code.

## Verified by test or by a real Blender build

- **Opening LOD monotonic** (triangles rise from LOD0 to LOD2 for rectangular and arched windows).
- **Recess is real**: at LOD1 the surround projects 0.08 m or more out of the wall; LOD0 stays flat.
- **Clearance**: the surround never reaches the next pilaster (checked on a real layout and on a tight synthetic window).
- **Allocator**: greedy by utility per triangle, dependency-aware, stops at 90% of its allowance, reports why a result is low.
- **Blender, synthetic fixture (no AI)**: flag off 1434 triangles, flag on 6714 triangles, all 120 windows at LOD2, build valid.
- **Blender, dev projects (no AI)**, real specs unchanged:

| Project | Flag off | Flag on | Target use (flag on) | Bytes (on) |
|---|---|---|---|---|
| UBND | 13,687 tris | 26,045 tris | 0.65 | 180 KB |
| Chợ Bình Tây | 3,772 tris | 20,258 tris | 0.51 | 123 KB |

  Accounting totals equal the triangle counts reported by Blender in both cases.
- **Registry**: all implemented components have registered generators; `validate_registry()` is empty.
- **M3 grammar**: symmetric and asymmetric facades, confidence threshold, rejected and foreign-part observations,
  determinism under input order, hidden facades marked `style_inferred`, fallback policy, spec v2 validity.
- **M4 slices**: finite geometry and matching accounting; plinth wraps the outline at base height; brise-soleil fins
  stand out from the correct face only; chimney rises from its base with a cap; arrays that do not fit are refused.
- **M5 checks**: range and metallic rules, glass exemption, texel density, neighbour ratio 2x, mip-aware padding,
  attribution mapping and its failure cases.

## Exit gates not met, and why

### M2
- **Allocator reaches at least 60% of target.** UBND reaches 0.65. Chợ Bình Tây reaches 0.51: every supported window
  upgrade was taken, and the remaining budget has no other supported candidate, because roof and skyline detail
  (M4) is not yet placed by the builder. The result reports `budget_limited` only when a budget actually rejected
  something; otherwise it reports `no_supported_candidates`.
- **Arch tessellation scales by projected size.** Not done. Arches use a fixed 8 segments.
- **Unknown material slot falls back with a blocking issue.** Not done in the assembly path.
- **Standard-profile visual check.** Only a synthetic render was looked at, by eye. Not measured.
- **Wall is not cut.** The recess is shown by a projecting surround and set-back glass. The wall mesh is not
  booleaned, so the opening does not pierce it. Stated in the module docstring.

### M3
- **AI produces observations.** No prompt or pipeline step yet, so the 80% grid-alignment gate (benchmark close-ups)
  is not measured.
- **Persist camera-to-facade assignments and rectified quads**, and **A2 observation panel**, not done.
- **Version 2 facade written by the pipeline.** Not done; the contract is validated but nothing writes it, per plan
  invariant 6 (migration tests first).

### M4
- **Eight slices planned, three built**: plinth, brise-soleil array, chimney stack. Still to build: string course
  (use v1 bands for now), stepped cornice (v1 has cornice tiers), pilaster profile, colonnade, balcony/balustrade,
  mansard and dormers, pavilion tiers, cupola/lantern, ridge cap, lattice mast, clock and rose window, sign panel,
  finial.
- **Not placed by the builder.** The three slices are library generators. Nothing in a spec asks for them yet.
- **Dev improvement per slice** (exit gate) not measured; it needs the AI benchmark, which was not run.

### M5
- **Normal maps (tangent space), AO and normal baking, KTX2 with PNG/JPEG fallback**: not done.
- **GLB validator (zero errors)**: not run. `gltf-validator` is not installed on this machine.
- **Texel density and atlas padding** are checked as functions only; the exporter does not call them yet.
- **Texture pixels in the build result** are reported as 0: the builder does not pass texture sizes to the accounting.

## Benchmarks and held-out

- **AI benchmark on dev projects: not run.** Changes in M2 act on geometry after the spec is made, and the build phase
  itself calls the vision AI for textures. Running it would spend quota without measuring the change cleanly. It can
  be run once the user approves the quota cost.
- **Held-out set: none exists.** The four landmarks used on 2026-11-08 have already been seen and must not be reused.
  Held-out results are therefore not reported.

## Known limits of this run

- The scheduled run (00:02, 10/11) did not execute: the first Bash call needed a permission the unattended run could
  not get, so it stopped with no changes. This in-session run replaced it.
- Blender returns exit code 0 when a Python exception stops the builder. The build still fails correctly because no
  result file is written, but the exit code is not a reliable signal. Logged for M0/M6 follow-up.

## Decisions needed from the user

1. Push the local commits (M2–M5) to `origin/main`, and wait for CI on Linux and macOS.
2. Release? If yes, keep the flag off in the release, since M2–M5 are not production-ready.
3. Approve an AI benchmark on the dev projects with the flag on (costs quota).
4. A held-out set: fix a list of new landmarks before any held-out run.
5. Continue with the remaining M4 slices and the M3 AI observation step, or stop here.


## Update (second pass, same day)

### Dev benchmark: judge score with the flag off and on (same spec, same photos)
Judge: gpt-5.6-sol (provider chosen by routing "auto" at run time). Three independent readings per score.

| Project | Flag off | Flag on | Change |
|---|---|---|---|
| UBND | 4.67 (5, 4, 5) | 4.33 (5, 4, 4) | -0.33, within reading noise |
| Chợ Bình Tây | 5.00 (5, 5, 5) | 5.00 (5, 6, 4) | 0.00 |

Facade score stayed at 2.0 in all four runs. The flag does not yet produce a judge improvement. By the
baseline rule (keep a change only if the score does not drop), the flag-on geometry is not an improvement and
must not be enabled by default.

Bug found before the benchmark: preview renders (used by the judge and the photo review) did not carry the
engine flag, so a flag-on benchmark would have scored version 1 geometry. Fixed in dde6d29 with a regression test.

### M3 observation step
`landmark3d/observe.py`: asks the model for one observation document per photo, validates it against the schema and
the project, retries with the validation errors, and writes `input/facade_observations.json` only when it
validates. Tested with a fake provider (accepted first try, accepted after retry, refused after retries, file
written only when valid). Not wired into the pipeline and not run against the model, so the 80% grid-alignment
gate is still not measured.

### M4 slices
Ten generators are now registered (plinth, pilaster, brise-soleil array, dormer, cupola, ridge cap, chimney,
lattice mast, rose window, finial), each with tests. The builder does not place them yet: a spec has no field
that asks for them. The "dev improvement per slice" gate is not measured.

### M5 measured checks
- glTF validator (gltf-validator, latest npm release) on four built GLB files: **0 errors, 0 warnings** for
  the UBND and Chợ Bình Tây builds with the flag off and on, and the M2 synthetic fixture.
- Build accounting now reports real texture pixels (it reported 0 before).
- Still not done: normal maps, AO and normal baking, KTX2 with a PNG/JPEG fallback, and wiring the texel and
  padding checks into the exporter.

### Test count
346 offline tests pass on `main` (1640fc2). Blender smoke build (CI's synthetic hall) passes.

### Status against the exit gates (unchanged in substance)
- M2: partial. Allocator reaches 0.65 on UBND, 0.51 on Chợ Bình Tây; visual check only.
- M3: core and observation step built; AI observations, grid-alignment gate and A2 panel not done.
- M4: ten slices built, not placed; no dev improvement measured.
- M5: deterministic checks, validator zero-error on samples; bakes and KTX2 not done.

### Still open, needs a decision
Enabling the flag, pushing `main`, a release, the held-out set, and whether to spend quota on a
flag-on AI benchmark again after the M4 slices are placed.
