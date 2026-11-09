# Landmark3D: project memory for Claude Code

Landmark name + location → researched data pack → AI massing with approval gates → headless Blender build → GLB.
User docs: `README.md`. Evaluation: `reports/benchmark/BENCHMARK.md`.

## Map
- `landmark3d/research/`:
  - `agent.py`: research steps and AI prompts (massing, review, facade, judge, triage, select).
  - `sources.py`: OSM, Nominatim, Wikidata, Commons.
  - `satellite.py`: Esri plan view.
  - `footprint_ai.py`: AI footprint pick or trace.
  - `ai.py`: Claude Code / Codex CLI and env-var providers.
- `landmark3d/pipeline.py`:
  - Phases and approvals.
  - `build_spec`: AI massing → photo triage → viewpoint matching → review rounds → best candidate → judge → judge-gated facade rounds.
  - Checks: `material_errors`, `orientation_errors`, `facade_issues`.
- `landmark3d/geom.py`:
  - Pure geometry, shared with Blender: footprints, tapers, roofs.
  - Facades: `facade_layout`, one bay grid per facade run; `check_facade`.
  - `curtain_wall`, `features`.
- `landmark3d/spec.py`: spec schema, `check_structure` (support, cantilevers, decks, orientation, height).
- `landmark3d/autotex.py`: automatic textures. `run_v2` does bay tiles on the grid, unique faces, QA, mirror repair, harmonise, swatches.
- `landmark3d/engine/`:
  - `blender_build.py`: runs inside Blender (photo panels drop generated detail on their face).
  - `runner.py`: build and preview jobs.
- `landmark3d/server.py` + `web/`: local web app on port 8770. `tests/`: pytest; tests never touch the network (see `tests/conftest.py`).

## Rules
- **Never commit** `projects/` (licensed photos, the owner's photos, Esri imagery, .blend/.glb) or images under `reports/` (made from them). Scan staged files for keys and emails before pushing.
- **No API keys** in the tool or repo. AI goes through the Claude Code / Codex CLIs signed in on the web, or env variables already on the machine.
- **Google imagery** is never downloaded automatically. Esri World Imagery is reference only, with attribution.
- **Improve generically:**
  - No per-landmark hand edits to make a result look better.
  - Gate AI improvement steps by the independent judge (keep only if the score does not drop).
  - Back geometric rules with deterministic checks and tests.
  - Measure on a held-out set run once with the tool frozen.
- **AI failures** (quota, sign-in) must not be silently replaced by defaults: `ai_failed` blocks A2.
- **Restart the server after code changes.** The web server does not reload; the Blender engine is a fresh subprocess each build.

## Commands
```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m landmark3d serve --port 8770
.\.venv\Scripts\python.exe reports\benchmark\run_benchmark.py --reset "<name filter>"   # sequential, stops on quota
.\.venv\Scripts\python.exe reports\benchmark\compare_sheets.py <project-id> ...
```
