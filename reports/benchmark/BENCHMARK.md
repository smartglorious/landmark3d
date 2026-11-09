# Landmark3D benchmark, 2026-11-08

Three landmarks of different types, run fresh through the full pipeline:
- Research: OSM, Wikidata, Commons photos, the satellite view, and AI web research.
- A1 approval.
- AI massing (Claude Opus via Claude Code).
- A–H viewpoint matching, 3 photo-review rounds, and best-candidate selection.
- A2 approval, then build.

A1 and A2 were auto-approved for the benchmark only; A3 is left for the user. The comparison sheets (photo vs same-viewpoint render, and satellite vs top view at the same scale) are in this folder.

Scores (1 = wrong, 5 = matches the reference) are by eye from the comparison sheets:

| Landmark | Type | Plan vs satellite | Silhouette / massing | Facade character | Viewpoints matched | Overall |
|---|---|---|---|---|---|---|
| Diamond Plaza (after all fixes) | podium + curved slab + tower | 4 | 3 | 2 | 4 | 3 |
| Bưu điện Trung tâm | classical, pavilions, hip roofs | 4 | 4 | 2 | 3 | 3.5 |
| Nhà thờ Đức Bà | twin towers, nave, apse | 4 | 4 | 3 | 4 | 4 |
| Bitexco Financial Tower | tapered tower, cantilevered helipad | 3 | 2 | 2 | 3 | 2.5 |

Builds: Bưu điện 5,531 triangles; Đức Bà 3,797; Bitexco 5,960. All are far below the 50k budget.

## What generalises (seen on all three)
- **Satellite plan view:** the plan, the orientation and the main-frontage side were correct on all three. It also found the towers of Đức Bà at the south-east end, where the scaffolding is.
- **A–H viewpoint matching:** gave usable like-for-like comparisons, and the review rounds fixed concrete differences.
- **Per-floor facades, hip/gable/pyramid roofs, curtain walls, apse polygons:** used appropriately by the AI without landmark-specific code.
- **Structure gate:** no floating parts in any result.

## Problems found
1. **Renders are too far away.** Framing fits the bounding sphere, while photos are close-ups or low wide angles, so the model is small in the frame and details cannot be compared. (all)
2. **The review is too lenient.** Đức Bà was declared "match" after round 1, though photo 3 clearly shows taller spires: the model's top is about 45 m, the real one about 57 m.
3. **Unsuitable Commons photos are used.** An interior photo (Bitexco: lobby with a 2023 decoration) and a 19th-century photo from before the spires (Đức Bà) were compared like current exteriors.
4. **No taper / curved-profile primitive.** Bitexco's lotus-bud taper is approximated by stacked stadiums.
5. **Cantilever rule too strict for real cantilevers.** The rule needs at least 40% of the plan resting on the part below. Bitexco's helipad is a disc projecting mostly off the tower, so it came out as a tiny bar.
6. **Tall towers lean in satellite imagery.** Relief displacement makes the roof appear offset from the base. The AI partly corrects for it, but the plan check is less reliable for towers above about 100 m.
7. **Duplicated OSM parts.** Bưu điện had 6 OSM parts stacked on the AI's own parts. Fixed after the run (the AI's parts now replace the OSM parts, and the AI gets the OSM outlines); Bưu điện needs a spec rerun to benefit.
8. **AI failure fell back silently.** When the usage limit was hit, the tool used defaults and the plan could still be approved. Fixed: an `ai_failed` error now blocks A2.
9. **Height facts can be wrong.** For Bưu điện the researched total height was 13.5 m, against a model top of 23 m. Only a warning; the facts were not re-checked against the photos.

## Cost
About 6–8 minutes of Opus per landmark for spec and review. The first benchmark attempt hit the 5-hour plan limit.

---

# Run 2 (after fixing problems 1–3), 2026-11-08 15:26–15:47

Changes since run 1:
- Photo-viewpoint renders fill about 88% of the frame. Street-level shots use a camera at 1.7 m looking up.
- The review scores plan, silhouette, heights, facade and materials from 1 to 5. "match" is accepted only when every score is ≥ 4.
- An AI triage of all candidate photos (one numbered contact sheet) keeps only current exterior and aerial shots. User photos are always kept.

Photo triage removed:
- Bưu điện: 1 interior and 1 "other" photo.
- Đức Bà: 4 historic photos (before the spires).
- Bitexco: 1 interior (the lobby) and 1 "other" photo.

Scores by eye (same method as run 1):

| Landmark | Run 1 | Run 2 | What changed |
|---|---|---|---|
| Nhà thờ Đức Bà | 4 | **4.5** | Spires now clearly taller. The close framing shows the twin towers, nave gables and apse ring like the photos. Historic photos no longer used. |
| Bưu điện Trung tâm | 3.5 | **4** | No duplicated OSM parts (6 parts instead of 16). The frontal view reads like the photo: tall centre pavilion with a large arched portal, two-storey wings of arched windows. |
| Bitexco Financial Tower | 2.5 | **3** | The lobby photo is dropped. The tapered silhouette and podium read better at the right framing. The helipad is still a small stub, and the taper is stepped. |

The AI's own final review scores were stricter, with no "match" in any run:
- Bưu điện: plan 4, silhouette 3, heights 4, facade 3, materials 3.
- Đức Bà: 4 / 3 / 4 / 3 / 4.
- Bitexco: 3 / 3 / 4 / 3 / 4.

Triangles: Bưu điện 3,682, Đức Bà 3,553, Bitexco 5,768. Each spec took about 5–8 minutes with Opus.

Still open: no taper primitive (problem 4); the cantilever rule blocks a real helipad deck (5); satellite lean on very tall towers (6); facade ornament and textures are not generated (sculpture, clock, rose window, signage).

---

# Run 3: Bitexco after taper and deck support, 2026-11-08 15:57–16:04

New in the tool:
- `taper` on any part: `top_scale`, profile `linear | ease_in | ease_out`, and `top_shift`. Curtain walls, bands, cornices and windows follow it. The structure check uses the narrowed outline at each height.
- Thin decks (height ≤ 4 m) declared `cantilever_of` a tower may project up to 25 m with ≥ 8% overlap.
- Prompt guidance: one tapered part instead of stacked boxes; place volumes by their base in satellite views (tall roofs lean).

Bitexco result: 4 parts instead of 7.
- Tower body: one stadium, `ease_in` taper to 0.42, 242 m.
- Crown: a slanted (shed) top on its own small taper.
- Helipad: a 25 m deck at 191 m, `cantilever_of` the tower.
- Podium.

The silhouette now reads as the lotus-bud tower with its side deck from all three photo viewpoints. Its 8,502 triangles are well under budget.

| Landmark | Run 1 | Run 2 | Run 3 |
|---|---|---|---|
| Bitexco Financial Tower | 2.5 | 3 | **4** |

The AI's own final scores: plan 3, silhouette 3, heights 4, facade 4, materials 4. It remains strict about the podium and the plan; the satellite lean of a 262 m tower makes the plan check hard.

Benchmark status after runs 2 and 3: Đức Bà 4.5, Bưu điện 4, Bitexco 4, Diamond Plaza 3 (not rerun since the fixes).

---

# Held-out test, run once, 2026-11-08 20:11–20:50

The tool was frozen before the run: `heldout_tool_freeze.txt`, sha256 1932158537c0…, 22 files. Nothing was changed during or after the run.
- **Projects:** four landmarks never used while developing the tool, created fresh.
- **Inputs:** automatic research only: OSM, Wikidata, Commons photos, satellite and AI web research. No user photos, no spec edits.
- **Approvals:** only A1/A2 were auto-approved; A3 is left for the user.

New in the frozen tool, compared with the dev runs above:
- **Independent AI judge:** a separate call that cannot edit; it scores 5 aspects from 1 to 5, plus an overall score out of 10.
- **AI facade pass:** per-floor facades, plus disc / panel / mast detail features. It is kept only if the judge score does not drop.
- **Material budget:** enforced in the AI validation.
- **Detail features:** clamped to their facade.

| Landmark | Type | Result | Judge (overall /10; plan, silhouette, heights, facade, materials /5) | By eye /5 |
|---|---|---|---|---|
| Dinh Độc Lập | modernist palace, long facade, brise-soleil, roof pavilion | built: 13 parts, 18 features, 3,680 triangles | **6**; 4, 3, 4, 3, 3 | 4 |
| Nhà hát Thành phố | Belle Époque theatre, big arched portal, mansard roofs | built: 7 parts, 36 features, 2,086 triangles | **5**; 3, 2, 3, 2, 3 | 3.5 |
| Landmark 81 | supertall in a mall complex | **stopped at the input gate**: the OSM object at the point is "Vincom Center" (the podium mall), name match 0, so the tool asks the user to confirm the footprint | – | – |
| Chợ Bình Tây | traditional market, courtyard, clock tower | **stopped at the input gate**: no OSM footprint found near the point, so the tool asks for the OSM id | – | – |

Findings:
- **Built landmarks are recognisable at the first, untouched attempt.**
  - Dinh Độc Lập has the long white facade with its central screen of vertical fins, the rooftop pavilion and the flag mast. Its plan matches the satellite view.
  - Nhà hát has the large central arch, the two side pavilions, the steps and the pale palette.
- **Judge scores on the held-out set (6 and 5) are in the same range as the dev set** (Bưu điện 5 with the same tool). There is no sign that the gains were specific to the landmarks the tool was tuned on. But the sample is small.
- **Main weakness: research, not modelling.**
  - Half the held-out landmarks need one answer from the user before modelling: confirm a footprint, or give an OSM id.
  - The gate behaves as designed (it does not guess), but it means "no manual input" is not achievable for every landmark yet.
- **Modelling gaps named by the judge:**
  - **Nhà hát:** mansard roofs, a portal arch rising above the cornice, and sculpted elements.
  - **Dinh Độc Lập:** the overhanging pavilion roof, and a set-back colonnaded ground floor with a canopy.
  - Both point to missing primitives: mansard roof, roof overhang, recessed / colonnaded ground floor.

---

# AI footprint finder, 2026-11-08 21:05–21:28

New in research: when OSM has no outline for the landmark, or only one chosen by position (name does not match), the tool proceeds as follows.
1. Draw every building outline around the point, numbered, on the satellite view. Overpass is used, with the OSM API as fallback.
2. Ask the AI, with the photos and the researched description, which outline(s) are the landmark itself, or to trace the footprint on the metre grid when none fits.
3. A high or medium confidence answer is used; a low one still goes to the input gate.

The two held-out landmarks that stopped at the gate were rerun from research. They had already been seen, so this run is a feature check, not a held-out score.

| Landmark | Footprint before | Footprint now | Judge /10 (plan, silhouette, heights, facade, materials) | By eye /5 |
|---|---|---|---|---|
| Landmark 81 | gate: "Vincom Center", name match 0 | AI pick, medium: the plot's only outline. The AI explained the tower stands in it, with the podium and mall to the south-east. | 5 (3, 3, 4, 2, 3) | 3.5 |
| Chợ Bình Tây | gate: no OSM outline | **AI trace**, medium: the hall around the central courtyard with its pavilion and two inner sheds. | 5 (4, 2, 3, 2, 3) | 3.5 |

Both now run end to end with no user input:
- **Chợ Bình Tây:** the traced plan matches the satellite view closely.
- **Landmark 81:** the model is a symmetric stepped tower with a spire. The judge notes the real tower is a bundle of square tubes ending at staggered heights, topped by a slender lattice mast.
- **Remaining gaps are modelling primitives:**
  - Chợ Bình Tây needs stepped/skirt roofs, deep eaves and a multi-tier pavilion.
  - Landmark 81 needs bundled tubes and a lattice mast.

---

# Facade logic and automatic textures, 2026-11-09

**Facade grid.** Windows, pilasters and the entrance used to be laid out independently. Each had its own spacing along the whole perimeter, so on Bưu điện the ground-floor windows (3.6 m) landed on the pilasters (2.8 m).
- Now every facade run has ONE symmetric bay grid shared by all elements, with clamped widths and heights.
- `check_facade` verifies there are no overlaps; the AI's facade answers are validated with it.
- Tests: `tests/test_facade_grid.py`, which uses the exact Bưu điện wing spec that failed.

**Judge-gated facade rounds (Bưu điện, Opus).**

| Facade round | Result | Judge overall /10 | Facade /5 |
|---|---|---|---|
| 1 | kept | 5 → 6 | 2 → 3 |
| 2 (from the judge's errors) | kept | 6 → 6 | 3 → 3 |
| 3 | **discarded** | 6 → 5 | 3 → 2 |

`facade_issues` reported 0 conflicts on the final spec.

**Automatic textures.** All research photos were already licence-allowed (CC BY / BY-SA / PD, 1280–1440 px). The tool simply never used them as textures.

| | v1: whole faces rectified | v2: clean bay repeated on the grid, unique faces, QA, harmonise |
|---|---|---|
| Bưu điện | 2 faces | 11 (10 bay, 1 unique), 1 dropped by QA |
| Nhà hát | 3 | 14 (10 bay, 4 unique) |
| Đức Bà | 4 | 21 (17 bay, 4 unique) + roof swatch, 4 dropped |
| Dinh Độc Lập | 1 | 13 (12 bay, 1 unique), 1 dropped |
| Chợ Bình Tây | 2 | 3 (unique) + swatch, 16 bay faces dropped by QA |

What v1 got wrong:
- Whole curved fronts were stretched onto flat faces, and the portal was duplicated.
- Trees were baked into the textures.
- Photo faces sat next to generated stripes.

v2 is consistent per part and free of trees on the bay-tiled faces. Still open:
- Parts not photographed keep plain materials.
- Colour is balanced for brightness only.
- Chợ Bình Tây's bays are mostly rejected by QA.
- No judge scores were run for the v2 texture builds (visual check only).
