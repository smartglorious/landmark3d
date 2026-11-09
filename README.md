# Landmark3D

Landmark name + location → research (structured sources first, then AI, then the user) → generic parametric massing → headless Blender build → validated GLB, with a local web app for the approval gates.

Design: `../BenThanhMarket3D/docs/TOOL_ARCHITECTURE.md`, input standard `../BenThanhMarket3D/docs/INPUT_DATA_STANDARD.md`.

## Install

Get the folder (git clone, or download the ZIP from GitHub), then run the installer once.

| | Install | Start |
|---|---|---|
| **Windows 10/11** | double-click `install.cmd` | double-click `Landmark3D.cmd` (or the desktop shortcut) |
| **macOS / Linux** | `./install.sh` | `./start.sh` |

The installer does the following:
1. Finds **Python 3.11+**. If it is missing, it offers to install it with winget (Windows), Homebrew (macOS) or apt/dnf (Linux).
2. Creates `.venv` and installs the Python packages at the exact versions in `requirements.lock`.
3. Finds **Blender 4.2+**, newest version first. If it is missing, it offers to install it with winget, Homebrew (`--cask`) or snap.
4. Runs `python -m landmark3d setup`:
   - downloads the 3D viewer's three.js r160 files (MIT) into `landmark3d/web/vendor/`, so the viewer works offline;
   - stores the Blender path;
   - runs the environment check.
5. Windows only: creates a desktop shortcut.

Nothing is installed system-wide without asking. `-Yes` / `--yes` answers yes for unattended installs. Other options: `-NoShortcut`, `-NoBlender`, `-Start` (Windows) and `--no-blender`, `--start` (macOS/Linux). Run the installer again at any time to repair or update an install.

**Optional, for AI research (no API key):** install Claude Code and run `claude auth login`, and/or Codex (`npm install -g @openai/codex`) and run `codex login`; then turn the provider on in the app.
**Optional, for GLB validation:** Node.js; the app's "Môi trường" page installs the glTF validator.

**Tested:** Windows 11 with Windows PowerShell 5.1, Python 3.13 and Blender 5.2: a fresh copy installs in about 30 s, passes the tests, builds a GLB, and the viewer runs fully offline. **Not tested yet:** macOS, Linux, a machine without Python or Blender (the winget / brew / apt branches), and Blender older than 5.2. Blender 4.2 is the minimum the environment check accepts.

## Start

Opens http://127.0.0.1:8770. First steps in the app:
1. **Cài đặt**: set your contact (website or e-mail). Wikidata and Wikimedia Commons reject requests without it.
2. **Kết nối AI**: the tool takes no API key. It shows how Claude and ChatGPT can connect on this machine, and you pick per provider:
   - **Đăng nhập web**: through the official CLIs, signed in with your account in the browser: Claude Code (`claude auth login`, Claude Pro/Max/Team account) and Codex (`codex login`, ChatGPT account). The "Đăng nhập bằng trình duyệt" button opens that sign-in. The CLIs run headless, with web search on, in a temporary folder; Codex runs in a read-only sandbox, and Claude Code gets no tools other than web search and reading the photos it is given.
   - **Biến môi trường**: credentials already set on the machine (`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` [+ `ANTHROPIC_BASE_URL`], `OPENAI_API_KEY` [+ `OPENAI_BASE_URL`], or `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`). The tool reads them from the process and the Windows user/machine environment, shows only their names, and never stores them. The CLIs also use the variables they support (e.g. `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX`).
   - **Tự chọn** (default): web login if the CLI is installed and signed in, else environment variables.
   Then test, choose models, and choose which provider does research / photo reading / spec.
3. **Môi trường**: check Blender, the Blender MCP add-on and client config, Node, and optionally install the glTF validator.
4. **Dự án**: enter a landmark name and location, optionally limits (triangles, GLB size, compression), and start.

CLI: `python -m landmark3d setup | doctor | new "<name>" "<location>" | run <id> | status <id> | approve <id> <phase>`.

## Phases and approvals

| Phase | What happens | Stops for |
|---|---|---|
| setup, doctor | folders; Blender headless + glTF/Draco, MCP, Node, AI keys | a missing required tool |
| research | Nominatim → OSM object footprint (OSM API) + `building:part`s (Overpass) → Esri satellite view → Wikidata (height, floors) → Commons photos with licences → AI web research with citations (optional cross-check) → **AI footprint finder** when OSM has no matching outline | — |
| **A1** sources_review | facts with sources; photos with licence and allowed use; assign a licensed photo as a texture | approval |
| completion | input gate: location, footprint, a height, roof form, at least one photo | missing items → form in the app |
| spec | generic parts (main footprint, OSM parts, AI-proposed towers/domes/wings from photos + facts), roofs, facades, materials, provenance, list of module defaults | — |
| **A2** spec_review | plan drawing, parts table, editable JSON | approval / change request |
| build | **automatic textures from the photos**, then Blender headless: parts, roofs (flat+parapet, gable, hip, pyramid, dome, shed), facades on a bay grid, features, materials/textures, Draco GLB (falls back to plain), .blend, QA renders incl. one per photo viewpoint, budget gates | failed gate |
| **A3** build_review | 3D viewer (three.js + Draco), renders, numbers | approval / change request (back to spec) |
| release | checksums, release notes with approvals, texture attributions, module defaults | — |
| **A6** release_review | final approval | approval |

A change request returns to the phase that owns the problem (A1 → research, A2/A3 → spec, A6 → build); AI receives the note on the next spec run.

**Go back to a stage:** the **Quay lại** button next to A1, A2, A3 or A6 reopens that stage once it has been reached. Its results are kept and only the later phases return to pending, so reopening A2 lets you edit the plan and rebuild without running the massing AI again.

**Restart from the beginning:** **Chạy lại từ đầu** takes a note for the AI and, under *Nâng cao*, an advanced prompt and the starting point (research, or massing with the research kept). The note and prompt replace the earlier ones and go to the research, massing, photo review and facade AI steps; the independent judge does not see them. Earlier change requests stop applying. Files you uploaded are kept.

Product and publisher details on the **Giới thiệu** page come from `landmark3d/web/about.json`.

**Facade detail:** besides punched openings, a part's `features` can hold details with real depth (`column`, `colonnade`, `balcony`, `balustrade`, `pediment`, `finial`, `cresting`), and `facade.sides` / `segments` give a wall or a stretch of wall its own bays. Each judge score is the mean of `judge_samples` readings (default 3). A facade round that scores lower is not used as a whole, but the parts it improved can be kept if the result passes the same test.

**AI call log:** each project's `logs/ai_calls.jsonl` has one line per AI attempt: step, attempt number, outcome (`ok`, `rejected`, `invalid_json`, `error`), seconds, rejection reasons, and for Claude Code the turns and tokens. Prompts and replies are not stored. Each phase adds an `ai usage` summary to the project log.

## Licences

Web images are used as reference and, when the licence allows (public domain, CC BY, CC BY-SA, or permission you record), as textures. Unknown licences stay reference-only. Attributions go into the release notes.

## Massing rules and shapes

- **Plan shapes** per part: `main` (the OSM outline), `rect`, `rounded_rect` (corner radius `r`), `stadium` (fully rounded short ends), `circle`, `polygon`.
- **Facades**:
  - `opening_type`: `rect`, `arch` or `curtain_wall`. A curtain wall uses the wall material as glass, with mullions every `bay_spacing` and a spandrel band per floor.
  - `cornice` (metres), `bands` (string courses per floor).
  - `ground` / `floors`: per-floor openings (`storefront`, `rect`, `arch`, `square`, `none`), pediments, awnings; `entrance` (a large arched portal), `pilasters`.
  - `features` (per part): `disc` (clock faces, rose windows), `panel` (signs, friezes, balconies, canopies), `mast` (flagpoles, antennas, crosses).
- **Facade grid** (`geom.facade_layout`): every facade (a run of the outline without a sharp turn; a curved front is one facade) gets ONE bay grid, whole bays, symmetric about its centre.
  - Pilasters stand on the bay lines, windows sit in the bay centres, and the entrance takes the central bay(s). All floors share the grid; a floor may use every 2nd or 3rd bay.
  - Window, pediment and awning widths are clamped so they never touch a pilaster. Heights stay clear of string courses and the cornice.
  - `geom.check_facade` verifies that no window overlaps a pilaster, the entrance or another window. `pipeline.facade_issues` sends such problems back to the AI as validation errors.
  - The layout engine's own corrections, like a window narrowed to fit its bay, are only notes and do not fail an AI pass.
  - Before the answer is checked, the facade pass removes materials no part uses and gives thin parts (< 2 m) no openings and a storey height of at least 1.6 m.
  - Close-up cameras fall back to the whole building when other volumes hide more than half of the focused part.
- **Roofs** (`flat`, `gable`, `hip`, `pyramid`, `dome`, `shed`). Every roof can also take:
  - `overhang`: the eaves project this many metres past the walls, with a soffit underneath. The ridge height stays the same.
  - `upturn`: the convex eave corners curl up by this many metres, for East Asian roofs (temples, pagodas, communal houses, Sino-French markets).
  - `skirts`: lower roof tiers, bottom first, as `[{depth, rise, gap, overhang?}]`, up to 3. Each skirt slopes inwards by `depth` while rising `rise`. Then an upper wall of `gap` m (clerestory or ventilation louvres) stands on the inset outline, and the roof `type` crowns the last tier. This covers tiered market halls, basilica aisles and pagoda storeys in ONE part.
  - A part may stand on a skirt tier (for example a clock storey): base_z = wall top + Σ(rise + gap), with its plan inside the inset outline. The structure gate accepts it, and the total height counts the tiers.
  - The AI is told to use `dome` only for real domes; a small pavilion with a pointed tiled roof is `pyramid` or `hip` with overhang and upturn.
- **Tapers and decks**:
  - `taper` on a part: `top_scale`, profile `linear | ease_in | ease_out`, and `top_shift`, for spires, bud or bullet towers and glass pyramids. Facades follow it.
  - Thin decks (≤ 4 m) declared `cantilever_of` a tower may project up to 25 m with ≥ 8% overlap. Use them for helipads, viewing decks and canopies.
- **Structure gate**: a part above the ground must stand on parts below it. If more than 5% of its plan, or any point more than 1.5 m out, has nothing underneath, that is an error. Errors are sent back to the AI to fix, and they block A2/A3 approval until the spec is fixed. These are only warnings:
  - a ground part sticking out of the OSM outline (porches, gates);
  - a rotation that follows no footprint edge direction;
  - a total height that differs from the researched one.
- **AI massing**: the prompt lists the footprint's edge directions with their share (main street frontage first). Reviewer notes are passed as problems to fix in earlier versions. Materials must be the ones the parts use. Curtain-wall glass is normalised to metallic ≥ 0.85 and roughness ≤ 0.1.
- **QA renders**: neutral sky (dark ground, bright horizon, blue zenith) so glass reflects something. Cameras frame the whole building. When viewpoint matching finds that a photo is a close-up of one part (a tower or a portal), it records `"focus": "<part id>"`. That photo's render then frames just that part, from street height. The review rounds and the judge compare like for like, and the review may change the focus.

## Quality loop (AI, measured)

After the massing, the spec phase runs these steps, all with the same AI provider:
1. **Photo triage**: one numbered contact sheet of all candidate photos. Interiors, details and historic photos that differ from today are dropped (at most 6 kept; your own photos are always kept).
2. **Viewpoint matching** and **photo review rounds** (see below).
3. **Best candidate**: when the rounds never reach "match", every candidate is rendered and the AI picks the best. The last revision is not assumed to be best.
4. **Independent judge**: a separate call that cannot edit. It scores plan, silhouette, heights, facade and materials (1–5), gives an overall score out of 10 and lists the 3 biggest errors. The result goes in `spec.quality` and is shown on A2.
5. **Facade pass + review rounds** (`facade_pass`, `facade_review_rounds`, default 2):
   - The facade pass writes per-floor facades and features without touching the geometry.
   - Later rounds get the judge's errors and photo | model sheets.
   - A round is kept only if the judge's overall score rises, or stays equal with a facade score at least as high. Otherwise the previous facades stay and the loop stops.
6. **Structure, material and facade checks** run on every AI answer (invalid answers are retried, then rejected). An AI failure (quota, sign-in) is an `ai_failed` error that blocks A2.

## AI footprint finder

When OSM has no outline for the landmark, or only one chosen by position (name does not match), research proceeds as follows.
1. Draw every building outline around the point, numbered, on the satellite view. Overpass is used, with the OSM API map call as fallback.
2. Ask the AI, with the photos and the researched description, which outline(s) are the landmark itself, or to **trace** the footprint on the metre grid.
3. High or medium confidence is used; low confidence still goes to the input gate. A traced footprint is labelled "AI traced on Esri imagery", not OSM data.

## Satellite plan view and photo review

- **Satellite image** (`research/satellite.py`): research saves a 240 × 160 m north-up view around the site from **Esri World Imagery**, with the OSM outline (yellow), the spec's metre grid, a north arrow, a scale bar and the attribution drawn on (`input/geo/satellite_annotated.jpg`).
  - It is reference only (never a texture), and it is credited in the release notes.
  - Esri's imagery is subject to Esri's terms (ArcGIS use, attribution); check they fit your use.
  - Turn it off with `"satellite_provider": "none"` in `~/.landmark3d/settings.json`.
- **Google Maps** imagery is not downloaded automatically, because Google's terms forbid it. You can upload your own screenshot (north up) in the "Ảnh vệ tinh" card; it is stored as reference only.
- **AI massing** sees the photos, then the satellite view(s). The OSM outline may cover only part of a complex: Diamond Plaza's outline misses the podium, and the satellite view shows it.
- **Photo review** (`photo_review_rounds`, default 3), after the first AI massing:
  1. Render the model from 8 sides (A–H, 45° apart from the main frontage). The AI picks the side each photo was taken from, using the satellite view and the compass direction of each label.
  2. Per round, the AI sees photo | model pairs from those viewpoints, plus satellite | model-from-above at the same extent and scale. It lists the differences and returns a corrected model. Structure errors are fixed within the round. Viewpoints can be corrected when the first match was wrong.
  3. Comparison sheets are shown on the A2 page. The viewpoints are stored in the spec, so the build also renders `photo1.png`…; these are the like-for-like views for A3.
- **Model**: spec and review work best with the strongest model (Kết nối AI → Claude → model `opus`). Each run takes about 6–8 minutes and counts against the plan's usage.

## Automatic textures (from the reference photos)

At build time (`"auto_textures": true`, `autotex.run_v2`), the model is textured from the research photos whose licence allows texture use: CC BY, CC BY-SA, public domain, or your own. Each one is credited in the release notes.
1. **Plan** (AI): the photos carry a 0–1 grid, and the AI is told which faces each photo shows (from the matched camera directions). Per part it chooses:
   - `bays`: ONE clean, unobstructed bay (bay line to bay line, ground to cornice). The tool rectifies it (perspective) and repeats it along the **same bay grid as the geometry** on every face of the part, sides and back included, so photo windows line up with the model's grid.
   - `unique`: a one-off face (an entrance pavilion), rectified whole.
   - `none`: the part keeps its plain material.
   - Roof swatches.
2. **QA** (AI): all face textures go on one numbered contact sheet.
   - Bad ones (wrong region, distorted, mostly hidden) are dropped.
   - Occluded spans on unique faces are repaired from the mirrored symmetric half.
   - When only a horizontal band is clean (usually the upper wall above stalls, crowds, vehicles or signboards), the AI gives `clean_rows`. The face then keeps just that band: the panel covers that height only, and the generated openings below stay.
3. **Harmonise**: all photo textures are brought to the same brightness.
4. **Place**: each face texture is a panel on the exact face, 8 cm in front of the wall. Generated windows, pilasters and features on that face are dropped, because the photo shows them.
   - Swatches must match their material's colour (mean sRGB distance ≤ 70) or are dropped; kept ones are colour-balanced and made seamless.

Auto textures are regenerated when the spec changes. Textures you add by hand win for the same material or face. Files: `input/textures/auto_*.jpg`; the gridded photos and the QA sheet are in `reports/autotex/`.

## Textures

The **Texture** card on a project page adds textures (upload a file, or pick a research photo whose licence allows texture use):

- **Repeat** (lặp): tiles the image over a spec material (`wall`, `roof`, `trim`, …) at N metres per repeat, with an optional tint `#rrggbb` that is multiplied in and exported as glTF `baseColorFactor`.
- **Panel** (dán ảnh): places the photo (optionally cropped `x0,y0,x1,y1`, 0–1 from top-left) on the face of a part that looks north/south/east/west (or all large faces), between heights z0–z1. All panels are packed into one atlas (`output/panel_atlas.jpg`, sized to fit), so they cost one material.

After adding textures, press **Build lại với texture** (needs A2 approved). It re-runs build and the phases after it, so A3 is asked again. API: `GET/POST /api/projects/{id}/textures`, `DELETE /api/projects/{id}/textures/{tid}`, `POST /api/projects/{id}/rebuild`. Licences `owned`, `permission_granted`, `public_domain`, `cc_by`, `cc_by_sa` and `cc_by_nc` (non-commercial only) are accepted; texture files and authors go into the release attributions.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```

## CI/CD (GitHub Actions)

`.github/workflows/ci.yml` runs on every push to `main`, on every pull request, and when started by hand. It has three jobs:
- **tests:** pytest on Windows, macOS and Linux, with Python 3.11 and 3.13, using the pinned `requirements.lock`.
- **installer:** runs the real `install.ps1` / `install.sh` on a clean Windows, macOS and Ubuntu runner (Blender skipped). It then checks that the viewer files were downloaded, that the tests pass in the installed `.venv`, and that the web app serves three.js from local files.
- **blender build:** downloads Blender 4.2 LTS (the oldest supported version) and Blender 5.2 on Ubuntu. Each one builds a synthetic hall headless with `scripts_dev/ci_build_smoke.py` (no network, no AI) and checks the GLB. The GLB and renders are uploaded as a run artifact.

`.github/workflows/release.yml` publishes a GitHub Release when you push a tag that matches the version in `pyproject.toml`. It tests first, then attaches:
- `landmark3d-X.Y.Z.zip`: the app folder with the viewer files included;
- the wheel and sdist;
- `SHA256SUMS.txt`.

```bash
git tag v0.1.0 && git push origin v0.1.0
```

## Known limits

- AI over web login was tested live here (Claude Code on a Claude Pro account, Codex on a ChatGPT account, web search on). The environment-variable route uses the official SDKs and is tested only with fakes (no such variables on this machine).
- CLI calls take 10–40 s each and count against the signed-in plan's usage limits.
- `cli_path` (a non-standard CLI location) can only be set in `~/.landmark3d/settings.json`, not from the web page.
- Without AI, the massing is what OSM provides (outline or `building:part`s) plus user answers; towers/domes not mapped in OSM need AI or a spec edit.
- **Geometry vocabulary:** mansard / multi-tier roofs, deep eaves, balustrades, bundled tubes and lattice masts are not primitives yet. Ornament comes from photo textures, not 3D geometry. Vaulted canopies as in the Ben Thanh sample are not generated.
- **Photo textures:**
  - They cover what the photos show. Parts never photographed (attics, backs of towers) keep plain materials.
  - Colours are balanced for brightness only.
  - The texture QA can drop most bays on busy sites (Chợ Bình Tây).
- **Judge scores** are AI judgements and vary between runs; see `reports/benchmark/BENCHMARK.md` for the dev / held-out results.
- A panel covers a whole face of a part (from the part's outline); it does not follow arches or setbacks inside that face.
- Overpass can be slow; the footprint comes from the OSM object Nominatim matched, so research still works when Overpass times out.
