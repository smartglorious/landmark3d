# Detail Engine v2 implementation plan

Status: approved implementation plan  
Owner: Landmark3D maintainers  
Companion gate: [`PRODUCTION_BASELINE.md`](PRODUCTION_BASELINE.md)  
Scope: exterior architectural reconstruction to editable, validated GLB

## 1. Required outcome

Replace the current "massing plus flat facade marks" output with a hybrid procedural reconstruction that:

- preserves the measured footprint, height, part support, and photo viewpoints;
- represents facades as semantic floors, bays, openings, assemblies, and architectural features;
- selects detail level by visual value and explicit geometry/byte budgets;
- keeps repeated elements as reusable modules or shared meshes;
- produces PBR-ready materials and retains geometry under photo-derived textures;
- records which details are observed, inferred from symmetry, style-derived, or defaults;
- remains generic: no landmark name may select geometry or a special-case rule;
- degrades safely when photos are sparse and never labels inferred hidden detail as observed.

The first production target is the `standard` profile in the production baseline. Photogrammetry and generative component plug-ins are later optional inputs, not prerequisites for Detail Engine v2.

## 2. Non-goals

- Interior reconstruction.
- Survey-grade accuracy from one or two photos.
- A monolithic image-to-3D replacement for the parametric pipeline.
- Shipping raw NeRF, Gaussian splat, or dense photogrammetry output as the default GLB.
- Per-landmark code, hand-edited benchmark assets, or prompts containing benchmark landmark names.
- Spending triangles only to increase utilization. Detail must pass a named visual or structural gate.

## 3. Invariants

These rules apply to every milestone:

1. The footprint, structure, licence, approval, and output-budget gates remain blocking.
2. AI proposes observations and semantic intent; deterministic code owns geometry, constraints, cost, and final validation.
3. Every generated component has provenance: `observed`, `symmetry`, `style_inferred`, `source_data`, or `default`.
4. Every non-default component has a confidence in `[0, 1]` and references its supporting source or rule.
5. Hidden facades may reuse a confirmed grammar, but must be marked `style_inferred`; unique features may not be mirrored without evidence.
6. Existing spec version 1 remains readable. New writers emit spec version 2 only after the migration and round-trip tests pass.
7. A photo texture never deletes valid geometry. It may suppress duplicate color/detail layers, not the wall assembly itself.
8. A lower-confidence component cannot displace a higher-confidence observation solely to improve an AI judge score.
9. Repeated modules share geometry whenever the exporter permits it.
10. A build is reproducible from the persisted spec, observations, component-library version, and settings.

## 4. Current bottlenecks and replacement points

| Current behavior | Location | Required replacement |
|---|---|---|
| Openings are shallow polygons in front of the wall | `landmark3d/geom.py::facade_openings` | Opening assemblies with recess, jamb, sill, lintel, frame, glass, optional surround |
| Features are a small flat list | `landmark3d/spec.py::PART_SCHEMA`, `geom.py::_feature_3d` | Typed component instances and style-neutral module generators |
| Triangle target acts only as a cap | `engine/blender_build.py::main` | Candidate LOD upgrades plus deterministic complexity allocator |
| Detail exists only when AI explicitly writes it | `pipeline.py::facade_pass` | Observation-to-grammar compiler with minimum detail policy |
| Photo panels remove generated face detail | `engine/blender_build.py::main`, `geom.clear_covered_faces` | Registered material layers over retained semantic geometry |
| Materials expose base color and scalar roughness/metallic | `engine/blender_build.py::make_material` | PBR texture slots: base color, normal, ORM, emissive where justified |
| Quality is mostly an AI judgement | `pipeline.py::judge` | Deterministic plan, silhouette, edge, semantic, coverage, and budget metrics |

Do not rewrite all geometry at once. Introduce v2 behind versioned adapters and migrate one vertical slice at a time.

## 5. Target pipeline

```text
research sources and user photos
    -> photo triage and licence gate
    -> camera/viewpoint records
    -> rectified facade observations
    -> observation validation and confidence merge
    -> facade grammar compiler
    -> massing + semantic component candidates
    -> structural placement and collision checks
    -> LOD candidates with cost/utility estimates
    -> complexity allocator
    -> procedural high-detail build
    -> UV/PBR generation and optional high-to-low bake
    -> GLB optimization
    -> deterministic QA renders and metrics
    -> AI explanation/judge as a secondary signal
    -> approval gates
```

Persist every arrow whose output affects the build. Do not rely on prompts or process memory to reconstruct a release.

## 6. Data contracts

### 6.1 `input/facade_observations.json`

This file is source evidence, not final geometry. Coordinates inside `quad`, `bbox`, `floor_lines`, and `bay_lines` use normalized photo coordinates with top-left origin.

```json
{
  "version": 1,
  "observations": [
    {
      "id": "FO-001",
      "source_id": "SRC-012",
      "photo_path": "input/photos/photo_03.jpg",
      "part_id": "main_hall",
      "facade_run": 2,
      "quad": [[0.08, 0.18], [0.92, 0.19], [0.94, 0.90], [0.06, 0.89]],
      "camera": {"azimuth_deg": 181.0, "elevation_deg": 7.0},
      "floor_lines": [0.90, 0.67, 0.43, 0.18],
      "bay_lines": [0.08, 0.20, 0.32, 0.44, 0.56, 0.68, 0.80, 0.92],
      "elements": [
        {
          "id": "FE-001",
          "type": "arched_window",
          "bbox": [0.22, 0.45, 0.30, 0.63],
          "floor": 1,
          "bay": 1,
          "symmetry_group": "upper_windows",
          "attributes": {"surround": true, "keystone": true},
          "confidence": 0.91,
          "provenance": "observed"
        }
      ],
      "confidence": 0.88,
      "review": {"status": "accepted", "notes": []}
    }
  ]
}
```

Validation requirements:

- referenced project source and part must exist;
- source must be an exterior photo not marked excluded;
- coordinates must be finite and inside `[0, 1]`;
- floor and bay lines must be ordered after rectification;
- element boxes must have positive area and remain inside the facade quad;
- duplicate observations of one element are merged by source confidence, not last-write-wins;
- incompatible high-confidence observations become a review issue rather than being averaged silently.

### 6.2 Spec version 2 facade contract

Version 2 keeps `parts`, `footprint`, `materials`, and `budgets`. A part may contain `facade_system`; the existing `facade` object remains the v1 compatibility input.

```json
{
  "facade_system": {
    "style_family": "classical",
    "style_confidence": 0.76,
    "runs": [
      {
        "run": 2,
        "grammar": {
          "floor_zones": [
            {"kind": "ground", "from": 0, "to": 0, "module": "storefront_arch"},
            {"kind": "repeat", "from": 1, "to": 2, "module": "arched_window_classical"},
            {"kind": "cornice", "module": "cornice_stepped"}
          ],
          "bay_count": 7,
          "bay_width_m": 3.85,
          "symmetry": "mirror"
        },
        "overrides": [
          {"floor": 0, "bay": 3, "module": "entrance_portal", "params": {"width_m": 6.4}}
        ],
        "evidence": ["FO-001"]
      }
    ]
  }
}
```

Do not store fully expanded repeated instances in the editable spec. Store grammar plus overrides; expand deterministically during the build.

### 6.3 Component definition

Component metadata belongs in code or versioned JSON under `landmark3d/components/`. Geometry implementation stays in Python until a stable external asset format is required.

```json
{
  "id": "arched_window_classical",
  "semantic_type": "opening_assembly",
  "style_tags": ["classical", "colonial", "beaux_arts"],
  "parameters": {
    "width_m": {"min": 0.6, "max": 5.0},
    "height_m": {"min": 1.0, "max": 8.0},
    "recess_m": {"default": 0.18, "min": 0.04, "max": 0.8}
  },
  "material_slots": ["wall", "trim", "glass"],
  "lods": [
    {"level": 0, "generator": "opening_panel"},
    {"level": 1, "generator": "opening_recessed"},
    {"level": 2, "generator": "opening_arch_surround"}
  ]
}
```

Every generator must return geometry plus machine-readable accounting:

```python
GeneratedComponent(
    mesh=mesh,
    triangle_count=triangles,
    material_slots={"trim", "glass"},
    bounds=bounds,
    anchors={"center": transform},
    warnings=[],
)
```

It must not write files, mutate the project state, call AI, or inspect a landmark name.

## 7. Component library v1

Implement these reusable families before adding more one-off primitives.

### Opening assemblies

- rectangular, square, arch, storefront, curtain-wall bay;
- real recess with jamb, sill, lintel and glass plane;
- frame profiles with one or two mullion axes;
- optional surround, pediment, keystone, awning and shutter;
- entrance portal as a multi-bay override;
- LOD0 panel, LOD1 recess/frame, LOD2 surround/ornament.

### Horizontal articulation

- plinth and base course;
- string course and spandrel band;
- stepped cornice with controlled projection;
- parapet, balustrade and cresting;
- canopy and roof-edge gutter.

### Vertical articulation

- pilaster with base/capital profiles;
- column orders represented by generic proportions, not branded assets;
- buttress and pier;
- brise-soleil/louver/screen arrays;
- arcade, colonnade and recessed loggia.

### Roof and skyline

- mansard and gambrel;
- dormer, chimney, ridge cap, cupola/lantern and turret;
- multi-tier pavilion and deep eaves;
- lattice mast and bundled vertical tubes;
- silhouette-preserving bevels and roof-edge subdivisions.

### Unique components

- clock/rose-window assembly;
- sign/frieze panel with depth;
- finial/pinnacle/crest;
- sculpture proxy with explicit `style_inferred` provenance unless reconstructed from input.

## 8. Complexity allocator

### 8.1 Candidate model

Build the lowest valid LOD, then emit upgrades rather than directly spending a shared integer budget.

```python
DetailCandidate(
    id="main.run2.window_group.lod1_to_lod2",
    component_ids=[...],
    current_lod=1,
    target_lod=2,
    incremental_triangles=1240,
    incremental_bytes_estimate=31000,
    incremental_materials=set(),
    utility=0.0,
    reasons={...},
)
```

### 8.2 Utility

Use deterministic normalized inputs:

```text
utility =
    visibility
  * projected_screen_area
  * semantic_salience
  * reference_mismatch
  * confidence
  * repetition_value
  / max(normalized_incremental_cost, epsilon)
```

Definitions:

- `visibility`: maximum visible fraction across accepted photo cameras and standard QA cameras;
- `projected_screen_area`: fraction of QA pixels affected by the upgrade;
- `semantic_salience`: roof silhouette, main entrance, tower crown, and landmark-defining features rank above hidden trim;
- `reference_mismatch`: deterministic edge/silhouette/semantic difference when available, else `1`;
- `confidence`: evidence confidence; defaults may not receive a value above `0.5`;
- `repetition_value`: capped benefit for one reusable module appearing many times;
- `normalized_incremental_cost`: weighted triangle, byte, material, and draw-call cost.

The weights are settings with versioned defaults and must be recorded in the build job.

### 8.3 Allocation rules

1. Reserve massing geometry and all evidence-backed LOD0 components.
2. Reserve a minimum detail allocation for every facade visible in an accepted reference photo.
3. Apply required upgrades for structural correctness and silhouette before aesthetic upgrades.
4. Select remaining candidates by utility/cost with dependency constraints.
5. Re-estimate cost after shared-mesh/atlas grouping.
6. Stop at 90% of triangle and byte targets by default; hard limits remain blocking.
7. If utilization is below 60%, report why: `no_supported_candidates`, `byte_limited`, `material_limited`, or `generator_failure`.
8. Never add a low-confidence unique ornament only to raise utilization.

Expected default allocation for the standard profile is guidance, not a fixed quota: 10–15% massing, 30–40% opening/facade assemblies, 15–20% roof and skyline, 10–15% articulation, 5–10% bevel/silhouette, remainder for evidence-backed unique details.

## 9. Proposed code layout

```text
landmark3d/
  observations.py             # facade-observation schema, merge, validation
  facade_grammar.py           # observations + v1 facade -> normalized grammar
  detail/
    __init__.py
    types.py                  # GeneratedComponent, DetailCandidate, accounting
    registry.py               # component lookup and metadata version
    allocator.py              # cost/utility calculation and selection
    placement.py              # transforms, anchors, collision and facade bounds
    openings.py               # opening assemblies
    articulation.py           # bands, cornices, pilasters, screens, loggias
    roofs.py                  # mansard, dormer, cupola, skyline modules
    unique.py                 # clocks, signs, finials, sculpture proxies
    metrics.py                # detail coverage and component QA
  materials/
    pbr.py                    # material slots and map preparation
    atlas.py                  # atlas planning and texel-density accounting
    bake.py                   # Blender bake job definitions
  schemas/
    facade_observations.schema.json
    landmark_spec_v2.schema.json
  components/
    library_v1.json
```

Integration changes:

- `spec.py`: load/validate v1 and v2, expose migration helpers, keep geometry-independent schema code here.
- `pipeline.py`: add `facade_observation` and `detail_plan` steps inside spec generation; persist results; run deterministic metrics before the judge.
- `research/agent.py`: prompts return observations and component intent, never raw mesh coordinates.
- `geom.py`: retain pure low-level geometry helpers; migrate feature constructors only after compatibility tests exist.
- `engine/blender_build.py`: consume expanded detail plan, preserve geometry under textures, create PBR materials, and report allocation accounting.
- `engine/runner.py`: include observation, library, allocator, and material versions in build jobs.
- `autotex.py`: output registered material evidence and crops; stop owning geometry suppression.
- `server.py` and `web/app.js`: expose provenance, confidence, allocated LOD, profile, metric failures, and editable grammar overrides.

Avoid circular dependencies: `detail/*` may depend on `geom`, but `geom` must not depend on `detail`, project persistence, AI, or FastAPI.

## 10. Milestones and agent work packages

Each work package is independently reviewable. Do not start a dependent package until the required contract and tests from its predecessor are merged.

### M0 — Correctness foundation

Purpose: prevent a higher-quality renderer from producing untrustworthy releases.

Tasks:

1. Centralize phase-transition validation so CLI and API cannot pre-approve a phase.
2. Add downstream invalidation when spec, source licence/use, texture, or budget changes.
3. Add a per-project run lock covering check, registration, work, and cleanup.
4. Make build/release artifacts carry hashes of spec, sources, textures, settings, component library, and allocator version.
5. Re-check source licence/use at build time; include licence/use and file digest in auto-texture cache keys.
6. Correct OSM relation assembly, including split multipolygon outer rings.
7. Validate coordinates, budgets, compression mode, upload size/type, and all phase request bodies.

Tests:

- CLI cannot approve a non-waiting phase.
- two concurrent run requests start exactly one worker;
- editing a spec after A3 invalidates build and later approvals;
- revoking texture permission removes it from the next build;
- release refuses mismatched artifact hashes;
- split OSM outer ways form one closed footprint;
- invalid budget and partial lat/lon requests return validation errors.

Exit gate: all production baseline integrity gates pass. M1 may be developed in parallel only behind a disabled feature flag; it may not be released before M0.

### M1 — Versioned contracts and accounting

Tasks:

1. Add `GeneratedComponent`, `DetailCandidate`, cost, bounds, anchor, and provenance types.
2. Add component registry with a stable `library_version`.
3. Add observation and v2 spec schemas plus validators.
4. Implement `v1 -> normalized facade grammar` adapter without changing output.
5. Extend build results with:
   - base/detail triangle counts;
   - triangles by semantic category and LOD;
   - target/hard-limit utilization;
   - candidate selected/rejected reasons;
   - shared-mesh estimate;
   - texture pixels and material slots.
6. Add a disabled `detail_engine_v2` setting and persist it in the spec/build job.

Tests:

- schema fixtures for valid and invalid observations/specs;
- v1 specs round-trip through the adapter;
- registry IDs are unique and all declared generators exist;
- accounting sums equal final generated mesh counts before Blender export;
- enabling the flag with no v2 components preserves v1 geometry.

Exit gate: contracts are frozen for M2–M5; breaking changes require an explicit schema-version bump.

### M2 — Opening assembly vertical slice

Purpose: prove the complete architecture on the highest-value repeated element.

Tasks:

1. Implement rectangular and arched opening assemblies at LOD0–LOD2.
2. Add recess, jamb, sill, lintel, frame, mullion/transom and glass material slot.
3. Compile existing facade grids into repeated opening component instances.
4. Keep entrance spans and pilaster collision constraints from the current layout engine.
5. Support shared meshes for equal modules and per-instance transforms.
6. Add candidate upgrades and allocator selection for openings.
7. Render before/after close-up fixtures from oblique and frontal cameras.

Geometry requirements:

- no z-fighting or open front-facing seams;
- opening depth stays within wall thickness assumptions;
- arch tessellation scales by projected size and LOD;
- frame width is clamped to the clear opening;
- modules do not overlap pilasters, bands, entrance, floor boundaries, or adjacent bays;
- unknown material slots fall back with a blocking validation issue, not silently.

Exit gate: standard-profile fixtures show visible recess and frame depth; semantic overlaps are zero; the allocator reaches at least 60% target when supported upgrades exist.

### M3 — Facade grammar and observations

Tasks:

1. Persist camera-to-facade assignments and rectified quads.
2. Ask vision AI for floors, bays, element boxes, semantic classes, symmetry groups, and confidence.
3. Validate and merge observations deterministically.
4. Compile repeated zones, ground zones, cornice zones, and explicit overrides.
5. Add an A2 observation panel showing the rectified facade, grammar overlay, confidence, and provenance.
6. Allow user corrections to floor/bay lines and module selection without editing raw JSON.
7. Feed corrected observations forward and retain them across rebuilds.

Fallback:

- no accepted photos: compile v1 facade rules and mark them `default` or `source_data`;
- one oblique photo: infer only the visible facade and reuse grammar on hidden facades as `style_inferred`;
- conflicting photos: block automatic merge for the affected run and expose the conflict at A2.

Exit gate: at least 80% of visible repeated openings in benchmark close-ups align with the detected floor/bay grid; hidden unique features are not invented as observed.

### M4 — Articulation and roof library

Implement in this order, each as a vertical slice with schema, generator, allocator candidates, tests, and benchmark evidence:

1. plinth, string course, stepped cornice;
2. pilaster/column profiles and recessed colonnade/loggia;
3. balcony/balustrade and brise-soleil/screen arrays;
4. mansard/gambrel and dormers;
5. multi-tier pavilion, cupola/lantern and deep eaves;
6. chimney, ridge cap, roof trim and gutter;
7. lattice mast and bundled tube assembly;
8. clock/rose-window, sign/frieze and finial/pinnacle.

Exit gate for each slice: one synthetic fixture plus one development landmark improves its named deterministic metric without degrading plan, structure, silhouette, material count, or hard budgets. Run held-out evaluation only at the milestone boundary.

### M5 — PBR and baking

Tasks:

1. Add material slots for base color, normal, occlusion/roughness/metallic, and emissive.
2. Preserve procedural geometry under photo materials.
3. Register photo crops to facade grammar cells and unique components.
4. Add texel-density planning and atlas padding/mip safety.
5. Generate AO from geometry and support normal baking from high-detail modules to lower LOD.
6. Add KTX2/Basis as an optional post-process; retain a standards-compatible PNG/JPEG fallback.
7. Record every source contributing pixels to an exported texture.

Exit gate: exported GLB passes the validator; normal orientation is correct in the viewer; no atlas bleeding at standard camera distances; licence attribution maps to actually used pixels/components.

### M6 — Deterministic quality loop

Tasks:

1. Add fixed-camera plan IoU and silhouette IoU.
2. Add edge-distance metric on rectified facade comparisons.
3. Add semantic recall/precision for accepted observed elements.
4. Add facade coverage, texel density, utilization, material count, and draw-call estimates.
5. Store metric inputs and version with every result.
6. Use deterministic metrics as the primary keep/reject signal; AI judge explains differences and handles categories without a deterministic metric.
7. Prevent an overall score from hiding regression in a protected category.

Exit gate: repeated builds from the same inputs produce identical specs, allocation decisions, and metric values within documented render tolerances.

### M7 — Guided capture and photogrammetry adapter (optional product tier)

Tasks:

1. Add capture instructions and image-set readiness checks: overlap, blur, exposure, coverage and viewpoint diversity.
2. Define an external adapter contract for COLMAP/Meshroom outputs: cameras, sparse/dense points, mesh and texture evidence.
3. Align reconstruction to project coordinates using footprint and known heights.
4. Fit procedural planes, roof surfaces and component anchors to the reconstruction.
5. Use dense geometry as evidence and bake source, not default shipped geometry.

This milestone must remain optional. A machine without the external dependency must retain the standard procedural workflow.

### M8 — Generative unique-component adapter (experimental)

Tasks:

1. Define a provider-neutral adapter accepting an isolated image crop/mask and returning mesh, texture, confidence and licence metadata.
2. Limit use to unique components; whole-building replacement is out of scope.
3. Normalize scale/orientation, clean mesh, reduce topology, assign anchors, and run collision/budget validation.
4. Mark all unseen geometry `generated` and show it at A2/A3.
5. Require explicit settings opt-in and a reproducible provider/model/version record.

No experimental generated asset may enter the default production baseline until it passes the held-out benchmark and licence review.

## 11. Test strategy

### Unit tests

- every component generator across min/default/max parameters;
- finite vertices, valid face indices, consistent winding, nonzero area and bounds;
- LOD triangle count monotonicity;
- facade placement, collision, clamping and symmetry;
- allocator dependency, determinism, budget and rejection reasons;
- schema validation, observation merge and provenance propagation;
- atlas packing, padding, PBR channel conventions and licence mapping.

### Property tests

Generate random valid footprints, floor counts, bay counts and component parameters. Assert:

- no exceptions or non-finite coordinates;
- no hard-budget overrun;
- no opening/pilaster/entrance overlap;
- no component outside its facade placement envelope unless its type explicitly permits projection;
- allocating a larger budget never lowers a selected component's LOD without a changed constraint;
- identical inputs produce identical selected candidate IDs and mesh hashes.

### Golden visual fixtures

Maintain small synthetic fixtures independent of real landmark photos:

- classical symmetric facade;
- modern curtain wall;
- recessed colonnade and screen facade;
- Gothic tower/front;
- market hall with tiered roof;
- tapered tower with crown and mast.

Store camera/spec inputs and metric baselines. Store rendered images only where repository licensing and size rules permit.

### Integration tests

- v1 project -> v2 adapter -> build -> validated GLB;
- observations -> grammar -> allocation -> Blender job;
- manual A2 correction survives rebuild;
- texture permission revocation invalidates cache and artifact;
- profile changes reallocate detail and invalidate downstream approvals;
- release manifest hashes all effective inputs.

## 12. Agent execution protocol

For every implementation task, the agent must:

1. Read this document, the production baseline, `CLAUDE.md`, and the touched module tests.
2. Name the milestone and vertical slice being implemented.
3. State which data contract changes, or explicitly state `no contract change`.
4. Add tests before or with implementation; never rely only on a live landmark run.
5. Preserve unrelated working-tree changes.
6. Run the smallest relevant tests, then the full offline suite.
7. Run `git diff --check` and inspect generated file lists for photos, projects, credentials, e-mail addresses, and large binaries.
8. Record before/after metrics against a development fixture.
9. Run held-out evaluation only when the milestone exit gate calls for it; do not tune after seeing held-out results.
10. Update this plan only for an accepted architectural decision, not to weaken a gate after a failure.

An agent must stop and request a decision when:

- a required contract conflicts with existing persisted data and no backward-compatible migration is clear;
- a dependency introduces a licence incompatible with redistribution;
- satisfying one hard gate requires weakening another;
- a change needs per-landmark logic;
- benchmark inputs or licensed images would be committed;
- the implementation would silently infer unique hidden geometry.

## 13. Definition of done for Detail Engine v2

Detail Engine v2 is complete only when:

- M0–M6 exit gates pass;
- the standard profile passes every production baseline gate;
- all six golden fixtures and the frozen development benchmark build successfully;
- the held-out benchmark meets the baseline without per-landmark changes;
- at least three style families use opening LOD2 plus one articulation or roof family;
- supported projects use at least 60% of target complexity or report a valid no-candidate/other-budget reason;
- photo materials retain compatible semantic geometry;
- PBR maps and their source attribution survive GLB validation and viewer inspection;
- v1 projects remain buildable through the adapter;
- release artifacts are reproducible and hash-bound to all effective inputs;
- user documentation explains fidelity tiers and observed versus inferred detail.

## 14. Technical references

- Esri CityEngine CGA modeling: <https://doc.arcgis.com/en/cityengine/latest/help/help-cga-modeling-overview.htm>
- CityEngine advanced Facade Wizard and LOD: <https://doc.arcgis.com/en/cityengine/latest/tutorials/tutorial-13b-facade-wizard-advanced-facade-creation.htm>
- FaçAID neuro-symbolic facade reconstruction: <https://facaid.github.io/>
- Image-based procedural facade modeling: <https://peterwonka.net/Publications/pdfs/2007.SG.Mueller.ImagebasedProceduralModelingOfFacades.print.pdf>
- COLMAP SfM/MVS workflow: <https://github.com/colmap/colmap/blob/main/doc/tutorial.rst>
- glTF 2.0 material model: <https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html>
- Khronos KTX2 guidance: <https://github.com/KhronosGroup/3D-Formats-Guidelines/blob/main/KTXArtistGuide.md>


