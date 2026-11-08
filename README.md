# Landmark3D

Landmark name + location → research (structured sources first, then AI, then the user) → generic parametric massing → headless Blender build → validated GLB, with a local web app for the approval gates.

Design: `../BenThanhMarket3D/docs/TOOL_ARCHITECTURE.md`, input standard `../BenThanhMarket3D/docs/INPUT_DATA_STANDARD.md`.

## Start

```powershell
.\Start-Landmark3D.ps1
```

Opens http://127.0.0.1:8770. First steps in the app:
1. **Cài đặt**: set your contact (website or e-mail). Wikidata and Wikimedia Commons reject requests without it.
2. **Kết nối AI**: the tool takes no API key. It shows how Claude and ChatGPT can connect on this machine, and you pick per provider:
   - **Đăng nhập web**: through the official CLIs, signed in with your account in the browser: Claude Code (`claude auth login`, Claude Pro/Max/Team account) and Codex (`codex login`, ChatGPT account). The "Đăng nhập bằng trình duyệt" button opens that sign-in. The CLIs run headless, with web search on, in a temporary folder; Codex runs in a read-only sandbox, and Claude Code gets no tools other than web search and reading the photos it is given.
   - **Biến môi trường**: credentials already set on the machine (`ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` [+ `ANTHROPIC_BASE_URL`], `OPENAI_API_KEY` [+ `OPENAI_BASE_URL`], or `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`). The tool reads them from the process and the Windows user/machine environment, shows only their names, and never stores them. The CLIs also use the variables they support (e.g. `CLAUDE_CODE_USE_BEDROCK` / `CLAUDE_CODE_USE_VERTEX`).
   - **Tự chọn** (default): web login if the CLI is installed and signed in, else environment variables.
   Then test, choose models, and choose which provider does research / photo reading / spec.
3. **Môi trường**: check Blender, the Blender MCP add-on and client config, Node, and optionally install the glTF validator.
4. **Dự án**: enter a landmark name and location, optionally limits (triangles, GLB size, compression), and start.

CLI: `python -m landmark3d doctor | new "<name>" "<location>" | run <id> | status <id> | approve <id> <phase>`.

## Phases and approvals

| Phase | What happens | Stops for |
|---|---|---|
| setup, doctor | folders; Blender headless + glTF/Draco, MCP, Node, AI keys | a missing required tool |
| research | Nominatim → OSM object footprint (OSM API) + `building:part`s (Overpass) → Wikidata (height, floors) → Commons photos with licences → AI web research with citations (optional cross-check by the second provider) | — |
| **A1** sources_review | facts with sources; photos with licence and allowed use; assign a licensed photo as a texture | approval |
| completion | input gate: location, footprint, a height, roof form, at least one photo | missing items → form in the app |
| spec | generic parts (main footprint, OSM parts, AI-proposed towers/domes/wings from photos + facts), roofs, facades, materials, provenance, list of module defaults | — |
| **A2** spec_review | plan drawing, parts table, editable JSON | approval / change request |
| build | Blender headless: parts, roofs (flat+parapet, gable, hip, pyramid, dome, shed), openings, materials/textures, Draco GLB (falls back to plain), .blend, 8 QA renders, budget gates | failed gate |
| **A3** build_review | 3D viewer (three.js + Draco), renders, numbers | approval / change request (back to spec) |
| release | checksums, release notes with approvals, texture attributions, module defaults | — |
| **A6** release_review | final approval | approval |

A change request returns to the phase that owns the problem (A1 → research, A2/A3 → spec, A6 → build); AI receives the note on the next spec run.

## Licences

Web images are used as reference and, when the licence allows (public domain, CC BY, CC BY-SA, or permission you record), as textures. Unknown licences stay reference-only. Attributions go into the release notes.

## Massing rules and shapes

- **Plan shapes** per part: `main` (the OSM outline), `rect`, `rounded_rect` (corner radius `r`), `stadium` (fully rounded short ends), `circle`, `polygon`.
- **Facades**:
  - `opening_type`: `rect`, `arch` or `curtain_wall`. A curtain wall uses the wall material as glass, with mullions every `bay_spacing` and a spandrel band per floor.
  - `cornice` (metres), `bands` (string courses per floor).
  - `ground`: the ground floor's own openings (`storefront`, `rect` or `arch`), optionally with `awning` in the part's `accent` material.
  - Windows run along curved outlines too.
- **Tapers and decks**:
  - `taper` on a part: `top_scale`, profile `linear | ease_in | ease_out`, and `top_shift`, for spires, bud or bullet towers and glass pyramids. Facades follow it.
  - Thin decks (≤ 4 m) declared `cantilever_of` a tower may project up to 25 m with ≥ 8% overlap. Use them for helipads, viewing decks and canopies.
- **Structure gate**: a part above the ground must stand on parts below it. If more than 5% of its plan, or any point more than 1.5 m out, has nothing underneath, that is an error. Errors are sent back to the AI to fix, and they block A2/A3 approval until the spec is fixed. These are only warnings:
  - a ground part sticking out of the OSM outline (porches, gates);
  - a rotation that follows no footprint edge direction;
  - a total height that differs from the researched one.
- **AI massing**: the prompt lists the footprint's edge directions with their share (main street frontage first). Reviewer notes are passed as problems to fix in earlier versions. Materials must be the ones the parts use. Curtain-wall glass is normalised to metallic ≥ 0.85 and roughness ≤ 0.1.
- **QA renders**: neutral sky (dark ground, bright horizon, blue zenith) so glass reflects something; cameras frame the whole building.

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

## Textures

The **Texture** card on a project page adds textures (upload a file, or pick a research photo whose licence allows texture use):

- **Repeat** (lặp): tiles the image over a spec material (`wall`, `roof`, `trim`, …) at N metres per repeat, with an optional tint `#rrggbb` that is multiplied in and exported as glTF `baseColorFactor`.
- **Panel** (dán ảnh): places the photo (optionally cropped `x0,y0,x1,y1`, 0–1 from top-left) on the face of a part that looks north/south/east/west (or all large faces), between heights z0–z1. All panels are packed into one atlas (`output/panel_atlas.jpg`, sized to fit), so they cost one material.

After adding textures, press **Build lại với texture** (needs A2 approved). It re-runs build and the phases after it, so A3 is asked again. API: `GET/POST /api/projects/{id}/textures`, `DELETE /api/projects/{id}/textures/{tid}`, `POST /api/projects/{id}/rebuild`. Licences `owned`, `permission_granted`, `public_domain`, `cc_by`, `cc_by_sa` and `cc_by_nc` (non-commercial only) are accepted; texture files and authors go into the release attributions.

## Tests

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests
```

## Known limits (v0.1)

- AI over web login was tested live here (Claude Code on a Claude Pro account, Codex on a ChatGPT account, web search on). The environment-variable route uses the official SDKs and is tested only with fakes (no such variables on this machine).
- CLI calls take 10–40 s each and count against the signed-in plan's usage limits.
- `cli_path` (a non-standard CLI location) can only be set in `~/.landmark3d/settings.json`, not from the web page.
- Without AI, the massing is what OSM provides (outline or `building:part`s) plus user answers; towers/domes not mapped in OSM need AI or a spec edit.
- Facades are simple (rectangular or arched openings per level); landmark-specific ornament (clock faces, reliefs) can be added as photo panels, but is not modelled in 3D; vaulted canopies as in the Ben Thanh sample are not generated yet.
- A panel covers a whole face of a part (from the part's outline); it does not follow arches or setbacks inside that face.
- Photo-based measurement analyzers and photo-match acceptance (docs P5–P6) are not built yet.
- Overpass can be slow; the footprint comes from the OSM object Nominatim matched, so research still works when Overpass times out.
