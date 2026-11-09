# Landmark3D production baseline

Status: release gate  
Applies to: Detail Engine v2 and all later production releases  
Implementation plan: [`DETAIL_ENGINE_V2_PLAN.md`](DETAIL_ENGINE_V2_PLAN.md)

## 1. Purpose

This document defines what "production-quality output" means for Landmark3D. A feature is not production-ready because it builds, uses more triangles, or looks better in one screenshot. It is production-ready only when the applicable integrity, quality, budget, compatibility, licence, and operational gates below pass with stored evidence.

The baseline separates:

- fidelity supported by the inputs;
- visual quality of the generated asset;
- structural and legal correctness;
- runtime/export constraints;
- reproducibility and approval integrity.

The product must show the achieved fidelity tier and confidence. It must not present inferred hidden architecture as measured reconstruction.

## 2. Fidelity tiers

| Tier | Minimum usable input | Product promise | Hidden surfaces |
|---|---|---|---|
| F0 massing | footprint plus height/levels | correct plan, scale, parts, roof class, orientation | module defaults |
| F1 procedural exterior | F0 plus at least one accepted exterior photo | recognisable facade grammar and style-consistent modules on visible sides | explicitly `style_inferred` |
| F2 multi-view procedural | at least 3 useful exterior views covering 2 or more sides | observed floor/bay layout and landmark-defining visible components | grammar reuse with confidence |
| F3 capture-assisted | overlapping photo set/video sufficient for camera reconstruction | metric camera evidence, fitted procedural shell, baked appearance | reconstruction evidence where covered |
| F4 source-model assisted | CAD/BIM/LiDAR or licensed dense source model | source-constrained exterior with documented simplification | determined by source coverage |

Tier is determined by validated evidence, not user selection. A build may be visually polished while remaining F1. The release notes must state the tier, unsupported surfaces, inferred component count, and source coverage.

## 3. Export profiles

Profiles are product presets. Users may override limits, but a release is certified only against a named profile.

| Profile | Triangle target | Triangle hard limit | GLB hard limit | Texture guidance | Intended use |
|---|---:|---:|---:|---|---|
| `legacy_web` | 40k | 50k | 5 MB | base color, up to 2K | backward compatibility |
| `web` | 50k | 65k | 10 MB | up to 2K PBR atlas | browser/mobile viewing |
| `standard` | 100k | 150k | 25 MB | 2K–4K PBR atlases | default quality product |
| `showcase` | 250k | 400k | 75 MB | up to 4K PBR atlases | desktop presentation/archive |

Additional hard limits for all profiles:

- materials: 12 for `legacy_web`/`web`, 20 for `standard`, 32 for `showcase`;
- no texture dimension above the profile limit without an explicit release warning;
- no unreferenced texture, material, mesh, camera, or image in the exported GLB;
- no single repeated component duplicated as unique mesh data when a shared representation is supported;
- GLB must pass the configured Khronos glTF validator with zero errors.

The Detail Engine should normally stop near 90% of target, not hard limit. A build below 60% target is acceptable only when it records a valid reason and passes visual metrics. Utilization by itself is never a reason to invent unsupported detail.

## 4. Release-blocking integrity gates

Every item in this section is severity `blocker`.

### 4.1 State-machine integrity

- An approval is accepted only when that exact phase is `waiting_approval`.
- CLI and API enforce the same transition rules.
- Mutating sources, source licence/use, observations, spec, texture, component library, allocator settings, profile, or budget invalidates every affected downstream result and approval.
- Only one pipeline worker may operate on a project at a time.
- A release cannot proceed from a failed, blocked, pending, or stale prerequisite.

### 4.2 Artifact identity

The build manifest must contain hashes or stable versions for:

- effective project state and budgets;
- landmark spec and observation files;
- every source file used by geometry or texture;
- source licence and use state;
- manual and automatic texture definitions;
- component library and schema version;
- allocator settings/version;
- Blender executable version;
- build script/version.

The release phase recomputes identity and refuses to package when the build manifest is stale. `model.glb`, `.blend`, renders, build result, spec, source attribution, and approval must describe the same identity.

### 4.3 Licence and provenance

- Build-time checks use current source records, not cached texture metadata.
- `reference_only` imagery contributes no exported pixels.
- Permission revocation invalidates generated textures and the build.
- Every exported texture/component records its source IDs and transformation method.
- CC BY/CC BY-SA/permission-required attributions contain title, author, licence, URL or local permission record, and use.
- CC BY-NC output is allowed only when the project is explicitly non-commercial; otherwise it is blocked.
- Satellite imagery remains reference-only unless its licence and product policy explicitly permit the intended export use.
- AI-generated or externally generated assets record provider, model/version, input source IDs, output licence, and whether unseen geometry was inferred.

### 4.4 Structural correctness

- zero severity-error results from structure, support, cantilever, facade overlap, material reference, and geometry validation;
- all mesh vertices and material values are finite;
- all face indices are valid and all exported visible surfaces have defined materials;
- no generated part has negative dimensions, inverted height range, or invalid footprint;
- no facade component violates its placement envelope unless its semantic type explicitly permits projection;
- unknown component/material IDs are blocking, never silently replaced in certified builds.

## 5. Quantitative quality gates

Metrics must be computed with a versioned implementation and stored with their inputs. Thresholds apply only where evidence exists; an unavailable metric is reported as `not_measurable`, never as a pass.

### 5.1 Plan and structure

| Metric | F0/F1 | F2+ |
|---|---:|---:|
| footprint IoU against accepted footprint | >= 0.95 | >= 0.97 |
| known total-height relative error | <= 12% | <= 8% |
| unsupported plan area | 0 blocking errors | 0 blocking errors |
| accepted major-part recall | >= 80% | >= 90% |

Footprint IoU excludes explicitly documented porches, canopies, gates, and permitted overhangs. Height warnings cannot be dismissed by a higher visual score.

### 5.2 Reference-view fidelity

Compute on every accepted exterior reference with a solved or approved camera.

| Metric | Production threshold |
|---|---:|
| median silhouette IoU | >= 0.78 |
| minimum silhouette IoU | >= 0.62, or explicit occlusion/close-up exclusion |
| median normalized facade edge distance | <= 0.045 of image diagonal |
| protected main-view edge distance | <= 0.035 of image diagonal |
| visible building coverage in comparison render | 70–95% of frame unless source is a detail close-up |

The exact edge detector and normalization are versioned. Changing metric implementation requires recalibrating thresholds on development fixtures before viewing held-out results.

### 5.3 Semantic facade fidelity

Evaluate only accepted observations with confidence `>= 0.7`.

| Metric | F1 | F2+ |
|---|---:|---:|
| floor-line match within 5% facade height | >= 80% | >= 90% |
| bay-line match within 5% facade width | >= 75% | >= 88% |
| observed major-component recall | >= 75% | >= 85% |
| observed major-component precision | >= 85% | >= 90% |
| opening/pilaster/entrance overlap violations | 0 | 0 |
| visible facade runs with at least LOD1 openings | 100% | 100% |

Major components include entrance portals, towers/crowns, domes/cupolas, prominent clocks/rose windows, colonnades, large balconies, brise-soleil/screens, dormers, major roof tiers, and other elements occupying at least 1% of the visible facade or explicitly marked landmark-defining.

### 5.4 Detail and material quality

- Every visible opening in `standard`/`showcase` uses a real recess or an explicitly justified curtain-wall assembly; a front-floating dark quad alone is not sufficient.
- Main entrances and accepted landmark-defining components use the highest LOD that fits hard budgets unless rejected for low confidence.
- Photo-derived facade material does not remove compatible opening/articulation geometry.
- Texel density differs by no more than 2x between adjacent equally visible facade regions, except intentional unique panels.
- Normal maps use glTF tangent-space convention and show no inverted green-channel or seam failure in the validation fixture.
- Atlas padding survives mipmapped inspection with no visible bleed at standard QA distances.
- PBR values remain in valid ranges; non-metal architectural surfaces are not made metallic to force reflections.
- Materials and texture sources shown in release notes match those actually referenced by the GLB.

### 5.5 Complexity utilization

For projects with supported candidates:

- target triangle utilization: 60–95%;
- hard-limit utilization: below 100%;
- at least 50% of detail triangles affect a facade or roof visible in an accepted photo or protected QA view;
- allocator output lists selected and rejected candidate IDs with cost and reason;
- increasing a profile budget cannot reduce a protected component's LOD unless another hard constraint changes;
- unused target above 40% requires one of: `no_supported_candidates`, `byte_limited`, `material_limited`, `draw_call_limited`, `low_confidence`, or `generator_failure`.

`generator_failure` makes the production build fail; the other reasons are visible release warnings.

## 6. Protected quality categories

The following categories cannot regress to improve an aggregate score:

- structure and support;
- accepted footprint/plan;
- licence compliance;
- primary silhouette;
- known height;
- accepted main entrance and landmark-defining components;
- source attribution;
- output hard limits.

Candidate selection and AI review compare categories lexicographically: blockers first, then protected categories, then aggregate visual score. An overall judge score cannot override a protected-category regression.

## 7. Benchmark protocol

### 7.1 Sets

Maintain three separate sets:

1. `synthetic`: generated fixtures with exact ground truth, safe for continuous tests;
2. `development`: named real landmarks used for feature development and prompt tuning;
3. `held_out`: landmarks and inputs frozen before a milestone evaluation.

Minimum style coverage at a production milestone:

- classical/Beaux-Arts;
- Gothic/religious;
- modernist with screens or colonnade;
- contemporary curtain wall/tapered tower;
- market/industrial hall;
- tiered East Asian or Sino-French roof form.

### 7.2 Freeze discipline

- Record repository commit, settings, provider/model, source manifest, licences, input hashes, component-library version, profile, and metric version before a held-out run.
- Run each held-out project once for the decision.
- Do not change code, prompts, settings, or inputs after seeing a held-out result and then count the rerun as held-out.
- Failed input acquisition is a product result, not silently removed from the denominator.
- Store numeric summaries and derived comparison sheets; do not commit restricted source images or project folders.

### 7.3 Milestone acceptance

A production milestone passes when:

- 100% synthetic fixtures satisfy structural and applicable semantic gates;
- at least 80% of development projects satisfy all applicable quantitative gates;
- at least 75% of held-out projects complete to A3 without code/spec hand edits;
- at least 70% of completed held-out projects satisfy all applicable quantitative gates;
- no held-out project has a blocker or protected-category regression;
- median by-eye review is at least 4/5 using fixed comparison sheets;
- failure categories and confidence are reported for every non-passing project.

These thresholds may be raised after a successful release. Lowering them requires a documented product decision and a new baseline version, never an implementation-only edit.

## 8. Human approval baseline

Automation supports, but does not replace, approval:

- A1 confirms source identity, licence, use and exclusions.
- A2 shows plan, observations, grammar, inferred/observed provenance, profile, allocation plan, and unresolved metric warnings.
- A3 shows the GLB, close-up facade cameras, source/render comparisons, PBR inspection, numeric gates and artifact identity.
- A6 shows the exact release manifest, attribution, fidelity tier, limitations and approvals.

The approval UI must make inferred unique components visually distinguishable or filterable. Approval notes become part of the artifact identity when they change the intended model.

## 9. Operational baseline

### 9.1 Failure semantics

- No AI, photogrammetry, bake, optimization, or validator failure is silently converted to a certified default.
- Optional enhancement failure may fall back only when the resulting output still passes the selected profile and its fidelity tier is downgraded visibly.
- A failed build removes or marks stale prior build results; the UI must not display an old GLB as the current result.
- Timeouts state the failed step and preserve diagnostic logs without prompts, secrets, or licensed image payloads.

### 9.2 Performance

Measured after spec/observations are available on the documented CI/reference machine:

| Profile | Build/export p95 | Viewer first useful frame target |
|---|---:|---:|
| `legacy_web`/`web` | <= 8 min | <= 4 s on reference broadband/device |
| `standard` | <= 15 min | <= 7 s |
| `showcase` | <= 30 min | <= 12 s on desktop reference |

AI research/review latency is reported separately. Performance failures do not permit skipping validation or lowering LOD without recording the fallback.

### 9.3 Observability

Every build result records:

- phase durations and external process exit status;
- component counts by semantic type and LOD;
- base/detail triangles and estimated/exported bytes;
- target/hard-limit utilization;
- selected/rejected allocation candidates;
- materials, texture pixels, atlas occupancy and source IDs;
- metric values, versions, exclusions and reasons;
- warnings grouped by correctness, fidelity, licence, budget and performance;
- artifact identity and tool versions.

Prompts and full AI replies remain excluded from logs. Keys, tokens, private paths beyond what diagnostics require, and source-image bytes must not be logged.

## 10. Compatibility baseline

- Windows, macOS and Linux remain supported at the same Python versions declared by the project.
- Blender minimum/current CI versions both build the synthetic standard fixture.
- Version-1 specs and existing projects remain readable through a tested adapter.
- Existing API fields remain available for one documented deprecation window.
- New schema versions reject unknown required semantics explicitly; they do not guess.
- The viewer handles every extension emitted by production profiles, with a tested fallback when an optional extension is unavailable.
- Offline tests never require network, AI accounts, licensed photos, or external photogrammetry tools.

## 11. CI and release evidence

Required CI jobs:

1. unit/property tests on the supported Python/OS matrix;
2. schema and migration fixtures;
3. concurrency/state-machine tests;
4. licence/cache invalidation tests;
5. Blender smoke builds on minimum and current supported Blender;
6. six synthetic golden fixtures on at least one Blender version;
7. glTF validation for every generated fixture;
8. artifact manifest mismatch tests;
9. package/install tests with component metadata and schemas included;
10. repository scan preventing projects, restricted images, credentials, keys, e-mail addresses, oversized binaries, and generated builds from entering a release.

Release evidence must include:

- commit and version;
- CI links/results;
- profile and schema versions;
- development and held-out summary tables;
- known failures and fidelity limitations;
- third-party dependency and model licences;
- migration notes;
- checksum manifest.

## 12. Production readiness checklist

A release owner may mark Detail Engine v2 production-ready only when all answers are yes:

- [ ] M0–M6 exit gates in the implementation plan pass.
- [ ] CLI/API state transitions are equivalent and tested.
- [ ] Artifact identity prevents spec/model/source mismatch.
- [ ] Current licence/use state is enforced at build and release time.
- [ ] Version-1 project migration and rebuild pass.
- [ ] All six synthetic style fixtures pass structural and visual gates.
- [ ] Development and held-out acceptance thresholds pass under freeze discipline.
- [ ] Standard-profile GLBs validate with zero errors.
- [ ] Visible standard-profile openings have geometric depth.
- [ ] PBR maps, atlas padding and normal orientation pass viewer inspection.
- [ ] Allocation accounting explains low utilization or reaches the target band.
- [ ] Protected quality categories have no regression.
- [ ] A2/A3/A6 expose provenance, confidence, fidelity tier and limitations.
- [ ] Installers/package include schemas, component metadata and viewer dependencies.
- [ ] Release notes and attribution describe the exact shipped artifact.

If any blocker is waived, the output is an experimental build and must not carry the production-ready designation.

