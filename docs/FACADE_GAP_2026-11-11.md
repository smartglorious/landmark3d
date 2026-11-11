# Facade gap analysis: Đức Bà, photo vs model from the same viewpoints (2026-11-11)

**Method.** The current Đức Bà model (texture flow v23, the build that waits at A3) was rendered from the four distinct
viewpoints of the licensed photos: front (commons_08–12), the north-east side with the transept and a tower
(commons_07), and the apse (commons_02, commons_04). Photos 03, 05 and 06 show scaffolding; photo 01 is an old print.
The pairs are in the scratchpad (`cmp_front`, `cmp_side07`, `cmp_apse04`, `cmp_apse02`). They were not committed,
because report images stay out of git.

The textures now follow the model faithfully. What is missing is mostly **in the model the textures are drawn on**:
massing, where features are placed, and the facade grammar. A better image generator cannot fix these.

---

## 1. What is missing, by view

| View | Real building | Model |
|---|---|---|
| Front | Gable flush between the towers, rising almost to the belfry, with rose window, clock and gable cross; three portals with stone arches; towers with stone quoins, paired lancets, an oculus and a louvred belfry; spires of grey metal with lucarnes and corner pinnacles | Gable set back and narrower than it looks, its lower half covered by the portal block and its sides inside the towers; one portal; towers with a generic 3-floor window grid; plain grey pyramids for spires; **black needles floating beside the spires** |
| North-east side (07) | Transept gable on this side with a large rose window and an arcade; low aisle chapels, each bay with its own small gable, round-arched windows and an oculus; clerestory above with a lean-to roof; buttresses with stone bands; iron cresting on the ridges | Transept end shows a plain window grid (its rose window is on another face); the aisle is one long box with three rows of identical pointed windows; no small gables, no oculi, no clerestory step |
| Apse (02, 04) | Polygonal chevet (about 8 sides) in two levels with radiating chapels, each with a hipped polygonal tile roof; round-arched windows with stone surrounds and oculi | A round drum with a conical roof (it reads as a tower); radiating chapels missing; pointed windows on every level |
| All | Round-arched (Romanesque-revival) windows, single or paired, with stone surrounds; oculi; buttresses; terracotta tile roofs | Pointed (Gothic) lancets everywhere, repeated on every floor; roofs barely visible |

---

## 2. Causes, with evidence

**C1. Bug: point features keep their coordinates from before plan_fit.** Finials and masts use absolute `x, y`.
`plan_fit` moves, turns and scales the parts but not these points. Example: the north spire's centre is
(33.1, −19.1) while its finial and mast are at (25.8, −13.3), 9 m away. These are the floating needles. Generic and
deterministic.

**C2. Features are placed on faces nobody can see.** For each feature face, I measured the share of the face that lies
inside another part at 60 % of the wall height:

| Part, face | Features | Hidden |
|---|---|---|
| transept 0 and 2 (facing 319° and 139°) | rose window (disc 6.1 m), pediment | 84 % |
| east_facade_gable 0 (319°) | rose window, clock, arcade, pediment | 100 % at 60 % height (towers on its sides, portal block in front of its lower part) |
| nave 0, crossing_clerestory | panels | 95–100 % |

- The transept roses face east and west, into the nave and the apse. The real transept gables face north-east and
  south-west.
- Nothing checks this, so P4 spends Codex images on hidden faces, and the visible transept ends get the plain bay
  elevation.

**C3. Massing proportions and arrangement of the front.**
- The gable (19 m wide) overlaps both towers, whose centres are 17.6 m apart.
- The towers stand 1.8 m in front of the gable.
- A 9.2 m portal block stands in front of the gable's lower half.

Together these push the composition back. The structure checks pass (nothing floats), so nothing flags it. The
photo-review rounds compare silhouettes, and a recessed gable barely changes the silhouette.

**C4. The shape vocabulary cannot express the apse and the aisle chapels.**
- The polygonal chevet with radiating chapels, each with its own hipped polygonal roof, came out as a round drum
  with a cone: there is no "chevet" or "polygonal apse with radiating chapels" composition.
- The aisle bays with individual small gables would need repeated gable dormers along a wall, which the spec cannot
  describe today.

**C5. The facade grammar is too coarse for this style.**
- One opening type per floor, one window per bay.
- No coupled windows (two lights under one arch).
- No oculus row between floors.
- No buttress (a projecting pier with stone bands, as opposed to a flat pilaster).
- No distinction between round-arched and pointed (the AI chose `arch`, rendered pointed).
- Every floor of a part gets windows, so the aisle walls show three rows where the real ones have one row plus oculi.

**C6. The generated texture inherits C2–C5.** Codex draws exactly the layout it is given (bay phase 0.005).
- When the layout is wrong, the texture is confidently wrong.
- The A2 layouts and the A2.5 review now show this, but nothing compares a layout with the photo of the same face.

**C7. Spires and belfries are plain shapes.** A `pyramid` roof with no lucarnes, corner pinnacles or louvred belfry
openings. Their material is a pale grey that renders like glass, not metal.

---

## 3. Proposed changes (generic, ordered)

| # | Change | Fixes | Check |
|---|---|---|---|
| F1 | `plan_fit` moves `x, y` of point features (finial, mast) with their part's transform; a spec check flags a point feature outside its part | C1 | unit test; Đức Bà needles on the spires |
| F2 | **Feature visibility check**: a feature on a face more than 50 % hidden by other parts is an A2 warning. The facade AI is told which faces are exposed (per part: exposed faces and their compass direction). The generator skips hidden faces | C2 | deterministic test; Đức Bà transept rose on an exposed face |
| F3 | **Front-composition checks**: a part whose front face is mostly inside or behind its neighbours is flagged ("set back X m, Y % covered"); the massing review prompt gets the list | C3 | Đức Bà gable visible in the front render |
| F4 | **Per-face photo comparison at A2**: for each exposed face with a matched photo, the layout next to the photo crop of that face, with one AI pass that lists missing elements (window type and count per floor, oculi, coupled windows, buttresses, gables) and corrects the facade grammar; deterministic checks as now | C5, C6 | facade element recall on the dev set |
| F5 | **Grammar extensions**: `coupled` windows (n lights under one arch), `oculus` rows, `buttress` (projecting pier with bands), `round` vs `pointed` arches, floors without openings by default for high blank walls | C5 | geom tests; Đức Bà aisle: 1 row + oculi |
| F6 | **Compositions**: `chevet` (polygonal apse plus radiating chapels with polygonal hipped roofs); `gabled bays` (a dormer gable per bay along a wall) | C4 | geom tests; Đức Bà apse silhouette |
| F7 | **Spire and belfry**: a spire with lucarnes and corner pinnacles; a louvred belfry opening type; metal roof material checks (metallic, not glass-like) | C7 | Đức Bà, UBND clock tower |

F1 and F2 are small and deterministic; they come first. F3–F4 change what the AI is asked. F5–F7 extend the
geometry. Each must be measured on the dev set: silhouette and element recall per photo view, plus the T0 texture
metrics. Then a held-out run, as for the rest.
