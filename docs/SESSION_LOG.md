# Session log

Newest first. Each entry says what state the working tree and the machine were left in, and where to continue.

---

## 2026-11-11

### State left behind (end of day)
- **Still nothing committed.** New today:
  - `landmark3d/smarttex/` `quads.py`, `bake.py`, `masks.py`, `heights.py`;
  - editor helpers and AI auto-place in `smarttex/run.py`;
  - `landmark3d/plan_fit.py`;
  - the camera editor in `web/app.js` and `server.py` routes;
  - tests for each.
  - `autotex.py` texture key is `-v12`.
- **Tests:** 622 pass (about 3 min).
- **Web server** runs as the app-managed preview `landmark3d` (port 8770, `.claude/launch.json`), with all code
  changes loaded.
- **The four dev projects** (UBND, Bưu điện, Đức Bà, Bitexco):
  - their plans are fitted to the footprint; A2 was approved by the owner and they were rebuilt;
  - they wait at **A3**;
  - their earlier specs are in `spec/landmark_spec.before_refit_2026-11-11.json`;
  - earlier textures are in `_tex_before_2026-11-11/`, `_tex_v5/`, `_tex_v6/` and `_tex_v9/`.
- **No real photo has a passing camera.** Bưu điện's `input/cameras.json` holds the older failed T2 records.
- **`detail_engine_v2` is still ON** in this machine's settings.

**Where it stopped.** Camera solve, height fit and projection all work on synthetic data. On real photos they are
blocked by the precision of the corner points: the AI's are 2–3 % off, and about 1 % is needed. The next step is
either:
- the owner places 5–6 clear top corners on 2 photos in the "Camera ảnh" editor; or
- a model-guided corner detector.

### Done today
- Restarted the web server; it had stopped when the previous session ended.
- Reran Đức Bà's automatic textures with the old method: no better (phase error 0.45, seams worse).
- **Đức Bà case study:** deterministic fixes to the automatic textures (corner snapping, oblique crops, window
  centring, sky and roof checks, colour per part, wall swatches, AI answer cache). Checked on Bưu điện and UBND.
  All T0 metrics improved on all three. Details and the table: `docs/SMART_TEXTURE_PLAN.md` section 9.

- **Follow-up the same day:** the facade-grammar crop check was measured and not adopted (unreliable). The
  AI texture check now sees each crop outlined on its source photo, plus the part's height and floors
  (key `-v6`). It drops wrong-region crops; on Đức Bà every seam is now under ΔE 6. The `-v5` outputs are kept
  in each project's `_tex_v5/`. See the plan, section 9.1.

- **Swatches:** plainness score, flattened and periodic tiles, wall samples from the colour-corrected faces,
  and roof samples from above the wall top (key `-v9`). All three now have wall swatches; Đức Bà's brick is no
  longer blotchy. Roof swatches are still weak. See the plan, section 9.2. The `-v6` outputs are in each
  project's `_tex_v6/`.

- **Bays:** multi-bay tiles were measured and not adopted. The AI's "bay" is often 1.3–1.7 real bays, so a
  real-bay cut was added instead: the window period is found and exactly one bay is cut (key `-v10`). It was
  used on 3 of 25 tiles. The `-v9` outputs are in `_tex_v9/`. See the plan, section 9.3.

- **T3 camera editor built:** project page → "Camera ảnh". Click model corners onto the photo, then
  "Giải camera". Solved from hand-placed points (`camera.solve_manual`), saved as `manual` in
  `input/cameras.json`. API under `/api/projects/{id}/cameras`. Plan section 10. Tests: 592 pass.

- **T5 first slice built:** wall faces are textured by projection from solved or hand-placed cameras, with face-id
  visibility, sky mask and soft best-view blend (`smarttex/bake.py`). Faces covered at least 70 % replace the AI
  crops. It runs without AI too, and edits in the T3 editor rebuild it. Checked end to end through Blender on
  synthetic data. No real project has a passing camera yet. Plan section 11. Tests: 598 pass.

- **T4 masks built:** sky, foliage (thick green texture, not painted bands or shutters), and AI occluder boxes
  cut out with GrabCut (`smarttex/masks.py`, `input/texture_masks/`). The T5 projection skips masked pixels.
  Checked on 5 real photos (overlays in the session scratchpad only). Plan section 12. Tests: 603 pass.
  The server was restarted with all code changes.

- **AI auto-place** in the T3 editor (button "AI tự đặt điểm"): rough corners, then zoomed-crop refinement, then a
  solve with leave-one-out removal of wrong AI points. Tests pass (606). On real Bưu điện photos it found no
  camera: the model's corners have no counterpart in the photos because the massing is off. Bưu điện's
  cameras.json was restored. Plan section 13.

- **Massing fixed at the root:** `plan_fit` fits the AI's volumes to the OSM footprint (whole model, then each
  ground volume) inside `build_spec`. Refitting the four dev specs gave plan IoU UBND 0.45→0.82, Bưu điện
  0.79→0.97, Đức Bà 0.71→0.96, Bitexco 0.76→0.92. **All four are back at A2 (pending)**: run, then
  approve A2, then build. Earlier specs are kept as `spec/landmark_spec.before_refit_2026-11-11.json`. See
  `docs/M0_M8_STATUS.md`, last section.

- **A2 approved and rebuilt** (owner's request) for the four refitted projects. Built plan IoU: UBND 0.81, Bưu
  điện 0.965 (passes the M6 gate), Đức Bà 0.949 (just under 0.95), Bitexco 0.916. All four wait at A3.
  Đức Bà's walls lost the wall swatch on this run: the AI swatch failed the colour check and the AI check
  dropped 21 crops, so walls show the plain brick colour.

- **Auto-place retried on the refitted Bưu điện: still no camera.** The model's elevations (part heights, pavilion
  width) differ from the building, so even plausible AI points do not fit one view. The zoomed refinement
  answered the crop centre. cameras.json was restored. Plan section 13.1.

- **Part heights from photos** (`smarttex/heights.py`): joint fit of cameras and part heights with the plan fixed.
  Recovers heights on synthetic data when the points are within about 1 %. It refuses at 2–3 % noise, and it
  refused Bưu điện's real AI points (too few, too coarse). No spec changed. Plan section 13.2.

- **Generated facade textures** (owner's idea): Codex CLI image generation, signed in on the web with no key,
  draws each part's elevation in the building's style, on the model's bay grid. `smarttex/genfacade.py`,
  setting `generated_textures`. Bưu điện rebuilt: 8 parts, all accepted, 43 faces; the model now reads like the
  real building. Trade-off: panels make the walls flat (3,675 triangles). Plan section 14. Tests 621.

- **Generated textures on the real mesh:** skins instead of panels (`apply_skins`). Bưu điện rebuilt: 19,321
  triangles, relief kept, windows on the painted windows. Plan section 14.1. Tests 622.

### Where to continue
1. Done: A2 approved and built for the four refitted projects. Then retry "AI tự đặt điểm" in the camera
   editor: model corners should now exist in the photos.
2. **Next texture fixes:** periods on ornate facades (use edges or pilaster lines, not darkness), and roof
   swatches that are really roof. No reliable test for roofs yet: consider the T3 editor or owner photos of
   the roofs.
3. **Owner step:** place 4–6 corners on each dev photo in the T3 editor (golden set). Then rebuild. T5
   projects those photos onto the model automatically. Next code: exposure matching, textures on the real meshes
   (UV atlas instead of panels), and roofs.
4. The options from 2026-11-10 (below) still apply.

---

## 2026-11-10 (end of day)

### State left behind
- **Nothing is committed or pushed.** The working tree holds every change listed below. `git status` shows about 30
  modified files, plus these new ones:
  - `landmark3d/smarttex/`
  - `docs/M0_M8_STATUS.md`
  - `docs/SMART_TEXTURE_PLAN.md`
  - `docs/SESSION_LOG.md`
  - `landmark3d/schemas/cameras.schema.json`
  - `tests/test_m*_*.py` (new milestone tests)
  - `tests/test_smarttex_*.py`
  - `tests/test_opencv_dependency.py`
  - `scripts_dev/*.py` (diagnostics)
- **`detail_engine_v2` is ON in this machine's settings** (`C:\Users\Admin\.landmark3d\settings.json`). The code
  default is still off.
- **The web server may still be running** on port 8770, started by the previous session. Restart it after code
  changes.
- **Four projects wait at A3** (owner decision): UBND, Bưu điện, Đức Bà and Bitexco. With the flag on, the M6 gate
  blocks all four on plan IoU.
- **Flag-off copies** of the four projects: `projects/_baseline_flag_off_2026-11-10/`. These are not committed.
- **Tests:** 572 pass in about 2 minutes. `git diff --check` is clean.

### Done today
1. **M0–M8 implementation pass.** See `docs/M0_M8_STATUS.md`.
2. **Flag-on AI benchmark**, with UBND rerun from research. See `reports/benchmark/BENCHMARK.md` and
   `docs/M0_M8_STATUS.md`.
   - Judge scores fell, but that comes from the newly generated AI specs: plan IoU dropped on all four. It does not
     come from the flag.
   - **Same-spec A/B:** the flag's effect is within judge noise. Bitexco's renders were pixel-identical, yet the
     scores were 6.67 and 5.00.
   - No AI spec requested an M4 articulation component.
3. **Smart Texture plan:** `docs/SMART_TEXTURE_PLAN.md`. OpenCV 5.0.0.93 was added (pinned in
   `requirements.lock`).
4. **Smart Texture T0 and T1 done; T2 built.** Details in section 8 of the plan.
   - T0 baseline: sky in textures, windows off the bay grid, colour jumps at corners. The numbers are in the plan
     and in BENCHMARK.
   - T1: lens check and undistortion; panoramas are excluded from projection.
   - T2 camera solve:
     - focal sweep;
     - rotation from vanishing points;
     - corner snapping;
     - facade-grid lines;
     - sky-outline check;
     - position search;
     - gate on corner support, agreement with the given points, and silhouette.
     
     It passes on synthetic renders. **No real Bưu điện photo passes yet**, and the gate correctly rejects them.
     The causes are the coarse model, only 3–5 AI points per photo, and trees cutting the skyline.

### Where to continue (pick one)
- **A. T3 camera editor (recommended for Smart Texture).**
  - Plan: `docs/SMART_TEXTURE_PLAN.md`, T3.
  - In the web app, show each photo with the projected wireframe. The owner drags 4–6 keypoints, `camera.solve`
    re-runs, and the gate result shows.
  - Manual points are saved with `source: "manual"` in `input/cameras.json`.
  - This also builds the golden set that the T2 exit gate needs.
  - Restart the server afterwards.
- **B. More T2 work without the owner:**
  - better focal estimation (EXIF is missing on these Commons photos; the vanishing-point focal is unreliable);
  - vectorise `translation_search` (about 3 min per photo now).
- **C. Benchmark findings, outside Smart Texture:**
  - AI massing regressed in plan, so make plan IoU part of the massing step;
  - offer M4 components in the massing and facade prompts;
  - reduce judge noise: more readings, or a paired comparison.
- **D. Housekeeping:**
  - decide whether to commit this work (and how to split it);
  - decide whether to turn the flag back off;
  - approve or reject the four A3 projects.

### Useful commands
```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m landmark3d texture-metrics buu-ien-trung-tam-sai-gon     # T0 metrics
.\.venv\Scripts\python.exe -m landmark3d cameras buu-ien-trung-tam-sai-gon --photo 2   # T1+T2 (AI answers cached)
.\.venv\Scripts\python.exe scripts_dev\sweep_t2.py 0.03          # synthetic camera-solve sweep (noise share)
.\.venv\Scripts\python.exe scripts_dev\sweep_grid.py 0.03 3      # banded facades, 3 points: grid on/off
.\.venv\Scripts\python.exe scripts_dev\compare_runs.py projects  # flag-off baseline vs current projects
.\.venv\Scripts\python.exe scripts_dev\judge_off_on.py           # same-spec judge A/B (costs AI quota)
```

### Where the evidence is (not committed)
- **Camera overlays, sketches and cached AI keypoints:**
  `projects/buu-ien-trung-tam-sai-gon/reports/texture_review/`. The overlays show:
  - cyan: model wireframe;
  - green: snapped points;
  - orange: coarse points;
  - magenta: AI points.
- **T0 reports:** `projects/<id>/reports/texture_quality.json`.
- **Judge A/B sheets:** `projects/<id>/reports/judge_flag_off|on/`.
