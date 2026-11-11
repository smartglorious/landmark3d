# Business-flow audit and restructuring plan (2026-11-11)

**Why now.** Generated facade textures (Codex image generation, `smarttex/genfacade.py`) turned out to work better
than photo reuse:
- clean, even walls on every face;
- windows on the model's own grid;
- relief kept when skinned onto the real mesh;
- no camera needed.

The pipeline was designed around photo reuse, so several steps are now in the wrong place, cost more than they
return, or are not reviewed. This audit maps the current flow, lists what is wrong with evidence, and proposes a
target flow with an ordered plan. Nothing here is implemented yet.

---

## 1. Current flow

```
setup → doctor → research → A1 sources & licences
  → completion (missing data)
  → spec:  AI massing → photo review rounds (3) → select best → plan_fit
           → judge → judge-gated facade rounds (2–3) → [observations, off]
  → A2 plan & massing
  → build: autotex.run_v2 [AI crop plan → crops → auto QA → AI QA → projection (T5) → Codex generation
           → swatches] → Blender build (panels / skins) → renders → M6 quality
  → A3 model review → release → A6
```

**AI cost per project, measured** (`ai usage` events; the Codex image calls are not even counted):

| Step | Bưu điện | Đức Bà | UBND |
|---|---|---|---|
| photo_review (massing rounds) | 3 calls, 297 s | 6 calls, 855 s | 13 calls, 1939 s |
| facade_pass (judge-gated) | 4 calls, 449 s | 4 calls, 643 s | 6 calls, 1576 s |
| judge (3 readings each) | 15 calls, 187 s | 15 calls, 185 s | 27 calls, 365 s |
| autotex crop plan and AI QA | 8 calls, 234 s | 10 calls, 390 s | 12 calls, 588 s |
| Codex generation (Bưu điện, 8 parts) | about 16 min, **not logged** | – | – |

---

## 2. Findings

**F1. Texture generation runs inside the build, unreviewed.**
- `autotex.run_v2` runs at the start of `build`. Every build therefore repeats or revalidates the AI crop plan,
  the AI check and, if their cache keys changed, Codex generation. The Bưu điện build took 18 minutes.
- A generated texture is new visual content, but it is seen only inside the finished model at A3. There is no
  step where the owner looks at the elevations, compares them with the photos, or regenerates one part.

**F2. The photo-crop path is now mostly wasted work.** On Bưu điện:
- 2 AI calls (crop plan and AI check) produced 20 crop items, and all were replaced by generated faces
  (`bay_faces: 0` after generation).
- The crop path still matters only as a fallback when Codex is unavailable, and for faces generation cannot draw
  yet (see F4).

**F3. The facade grammar has become the critical input.** The generated texture is drawn from the model's facade
layout: bays, floors, opening sizes and arch or flat heads. Its accuracy now decides how true the texture is.
- The grammar comes from the massing AI plus judge-gated facade rounds. The judge's noise (±1.67 on identical
  images, 2026-11-10) makes those rounds close to random.
- The deterministic, photo-based grammar step (M3 observations, `facade_observations`) is **off**.
- Facade rounds plus judge cost 10–30 minutes per project.

**F4. Generation covers only repeated bays.**
- Each part gets one n-bay elevation, tiled over all its faces.
- Unique compositions are not drawn: the Đức Bà gable with its rose window, three portals, tower stages, the
  Bưu điện clock pavilion. The spec already describes them as features (`disc`, `panel`, `balustrade`,
  entrance), but the layout ignores them.
- A side face with a different design gets the same tile as the front.

**F5. Relief needs `detail_engine_v2`, which is still "off by default" in code.** Skins keep the modelled openings
only because this machine has the flag on. With the default settings, a skinned wall has no opening geometry
under its painted windows.

**F6. Texture resolution drops with the number of faces.** All panels and skins share one atlas of at most 2048
px. Bưu điện's 43 faces each get a small cell, while the Codex images are about 1200 px per part. Each part's
image is repeated in the atlas once per face, although the faces share the same image.

**F7. Camera-based steps (T2, T3, T4, T5, heights) block on point precision and are visible to the owner.**
- Real photos never produced a passing camera.
- The camera editor is on every project page and was found hard to use.
- These steps are valuable only as an optional "photo-exact" mode.

**F8. Roofs and plain walls still come from unreliable photo boxes.** The AI swatch boxes miss (sky, wrong
surface); plain and roof materials often stay flat colour. Codex can draw seamless material tiles directly.

**F9. Observability and policy gaps for generation.**
- Codex image calls bypass `ai.ask_json`, so they are missing from usage statistics and from the per-step time
  report.
- A failed generation falls back silently to other textures. The failure is logged in `reports/autotex/notes.json`
  but not shown at any approval.
- Release notes list generated textures, but there is no "generated content" summary at A3 or A6.

**F10. The cache scope is too broad.** `autotex.texture_key` covers the whole spec, so changing one part
invalidates the texture step of every part. The per-part generation cache softens this, but the AI crop plan and
the AI check are rerun.

**F11. Curtain-wall towers are left out** (Bitexco: 0 generated faces). Glass facades need a generated
curtain-wall pattern (mullion grid and reflections) or a parametric material, not a bay elevation.

**F12. The judge is the gate for AI improvement steps, but it is too noisy to gate anything** (2026-11-10
same-spec A/B). The deterministic M6 and T0 metrics exist and should carry the gating, with the judge as a
secondary report.

**F13. A large body of work is uncommitted:** 64 changed or new paths since 2026-11-10 (M0–M8, Smart Texture,
plan_fit, generation, editor). The risk of loss or of mixing unrelated changes in one commit grows every day.

---

## 3. Target flow

```
setup → doctor → research → A1
  → completion
  → spec:      AI massing → plan_fit → facade grammar from photos (observations, on)
               → one judge reading for the record (no judge-gated loops)
  → A2 plan, massing and FACADE LAYOUTS
      (each part's elevation layout and features, as Codex will receive them)
  → textures:  per part: Codex elevation (bays + unique faces with features)
               + roof and wall material tiles (Codex, seamless)
               + deterministic checks, one retry; per-part cache; fallback = photo crops
  → A2.5 TEXTURE REVIEW
      (elevations next to the reference photos; accept / regenerate / use colour, per part)
  → build:     no AI calls; skins on the real mesh (relief), per-part textures, renders incl. front views
  → A3 model review (+ generated-content summary) → release → A6
Optional "photo-exact" mode (off): cameras (T2/T3), masks (T4), projection (T5), heights
```

Effects:
- Builds become fast and deterministic: no AI, about 1–2 minutes.
- Every generated texture is reviewed before it is used.
- The facade grammar, the input that now decides quality, gets a real photo-based step and an approval.
- The judge stops driving loops it cannot measure.

---

## 4. Plan, in order

| # | Change | Fixes | Done when |
|---|---|---|---|
| P1 | **Commit the work so far** in logical commits (M0–M8; Smart Texture T0–T5 and editor; plan_fit; genfacade and skins; docs). No push without approval. | F13 | clean `git status`, tests green |
| P2 | **New `textures` phase and A2.5 "Texture review"** between A2 and build. Move `autotex.run_v2` there; build only reads `input/textures.json`. Web panel: per part, the elevation next to its reference photos and layout, with Accept / Regenerate (optional note to the generator) / Use colour. | F1, F9 | build makes no AI call; owner can regenerate one part without a rebuild |
| P3 | **Generation first, crops as fallback.** Skip the AI crop plan and AI check when Codex is available; keep them for the no-Codex case and for faces generation refuses. | F2, F10 | Bưu điện texture step: 0 Claude calls, ~8 Codex calls |
| P4 | **Unique faces in the layout.** Draw spec features (`disc` rose or clock, `panel`, `balustrade`, entrance and portals, tower stages) into the per-face layout. Generate a face-specific elevation for faces with features or a distinct design, using the photo that shows that face. | F4 | Đức Bà gable with rose window and three portals; Bưu điện clock pavilion |
| P5 | **Facade grammar from photos.** Turn on observations (`facade_observations`) for the parts that get generated textures. Show the layouts at A2. Replace the judge-gated facade rounds with one AI facade pass plus deterministic checks (`facade_issues`, grid alignment). Keep one judge reading as a report. | F3, F12 | spec phase time halved; layouts visible at A2 |
| P6 | **Make relief independent of the flag.** Enable the opening assemblies used by skins by default (or make `detail_engine_v2` default on after its gates), and document it. | F5 | default settings build keeps window relief under skins |
| P7 | **Per-part texture files instead of one shared atlas.** One image per part, UV per face into it, size from texel density. | F6 | no face below 24 px/m on the dev set |
| P8 | **Generated material tiles** for roofs and plain walls (seamless, Codex), checked by the plainness and colour tests that already exist. | F8 | roofs textured on all dev projects |
| P9 | **Curtain-wall generation** (mullion grid and reflection pattern from the photos) for glass towers. | F11 | Bitexco textured |
| P10 | **Photo-exact mode behind a setting** (off). Hide the camera editor unless it is on; keep T2–T5, masks and heights for owners who place points. | F7 | default project page has no camera card |
| P11 | **Observability.** Log Codex image calls in AI usage (count, seconds); show the generated-content summary and failures at A2.5, A3 and A6. | F9 | usage report includes generation |
| P12 | **Benchmark.** Dev set rerun with the new flow; T0 and M6 metrics plus one judge reading; then a new held-out set, run once. | – | BENCHMARK.md entry; held-out result |

**Order and dependencies.**
- P1 first.
- P2 and P3 together: they are the core restructuring.
- Then P4 and P5, which give the biggest visual gain.
- P6 and P7 are small and independent.
- P8 and P9 extend coverage.
- P10 and P11 are cleanup.
- P12 last.

---

## 5. Decisions for the owner

1. **New approval A2.5 "Texture review"** (recommended), or fold texture review into A3.
2. **Default the flag:** make `detail_engine_v2` (or just opening assemblies) on by default, so relief works out of
   the box.
3. **Photo-exact mode off by default** and hidden from the project page.
4. **Codex quota per project:** about 10–20 image generations (2 min each) on the first texture run; cached after.
   Acceptable as default, or only on request?
5. **Commit now** (P1): may I commit the current work in logical commits (no push)?
