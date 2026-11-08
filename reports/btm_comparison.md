# Ben Thanh: tool output vs the manual r5b model

Date: 2026-11-07. Tool v0.1 (no AI provider connected). Reference: `BenThanhMarket3D/output/BenThanhMarket.glb` (r5b, built by hand over many owner iterations).

## Runs

| Run | Input | How |
|---|---|---|
| **r5b** | manual project (data pack, photos, reference GLB sections, ~30 owner corrections) | reference |
| **Tool A** | name "Chợ Bến Thành", location "Quận 1, Thành phố Hồ Chí Minh, Việt Nam"; the tool asked only for wall height (7.0 m from `dimensions.json`) and roof form (hip); the 9 photos of `data/images/raw` uploaded with their licences | fully automatic, all approvals accepted |
| **Tool B** | same project, but at A2 the spec was replaced by 26 generic parts converted from the r5b volumes (hall, 2 cross halls, 12 strips, tower, gates, portal body, North-wing shops, passage) | tests the engine's capacity when the spec is right (what AI massing should produce) |

## Scores (same frame, orthographic silhouettes, IoU against r5b)

| View | Tool A | Tool B |
|---|---:|---:|
| plan (top) | 0.891 | 0.853 |
| south elevation | 0.736 | 0.977 |
| north elevation | 0.737 | 0.979 |
| east elevation | 0.797 | 0.876 |
| west elevation | 0.797 | 0.878 |
| **mean** | **0.792** | **0.913** |

| Metric | r5b | Tool A | Tool B |
|---|---:|---:|---:|
| footprint | OSM way/39514795 | same way | same way |
| top height | 21.74 m | 11.9 m | 20.6 m |
| triangles | 13,535 | 326 | 1,561 |
| GLB bytes | 1,988,600 | 4,940 | 44,708 |
| materials / textures | 10, owner textures + source atlas | 3, colours only | 3, colours only |

Side-by-side views: `btm_comparison_sheet.png`. Scripts to reproduce: `btm_comparison_scripts/`.

## Reading

- **Tool A is not like r5b.** It gets the footprint and wall height right, then puts one hip roof over the whole market: no clock tower, gates, cross halls, 12 roof strips, North wing, canopy or detail. Without AI there is no source for those parts. The plan IoU (0.89) looks high only because both models cover the same footprint.
- **Tool B is close in massing (mean 0.91, south/north elevations 0.98)** once the spec describes the right parts. Remaining gaps are things the generic engine cannot build yet: the arched canopy belt with vaults, the apron (lowers plan IoU), gate and tower ornament, the photo-textured tower/East gate/reliefs, roof details (caps, monitors, seams), and roof profiles such as the unified cross shell with valleys.
- **Conclusion:** the tool does not reproduce the manual result today. The gap from A to B is the massing knowledge (AI reading photos + facts, or the user editing the spec); the gap from B to r5b is missing modules and texture application.

## Bugs found by this test (fixed)

| Bug | Effect | Fix |
|---|---|---|
| Geocoding took the first Nominatim hit; with "Chợ Bến Thành, Quận 1" that was an office node 300 m away, and the "largest building nearby" rule then picked Saigon Centre (193 m) | silent wrong landmark | Wikidata first (coordinates + aliases), ranked Nominatim hits by name match and category, footprint must match the name; weak matches go to the input gate for user confirmation; tests added |
| Default hip roof rise = 15 % of the short side | 20.7 m roof on a 7 m wall | capped at 0.7 × wall height and flagged as a module default |
| Windows file lock between the run thread and API reads of `project.json` | HTTP 500 during runs | per-project re-entrant lock + retry; concurrency test added |
| Project names with extra words ("… (spec từ dữ liệu BTM)") are used as the search text | no location found (handled correctly: the gate asked for coordinates and footprint) | open: a separate "search name" field would help |

## What closes the gap (in order of effect)

1. **AI massing from photos + facts** (connect Claude/ChatGPT): should produce Tool-B-like parts automatically; must be measured with this same comparison once keys are available.
2. **Facade/edge modules**: arcade canopy (flat or vaulted edge), apron, gate compositions, tower stages and clock faces.
3. **Texture application** in the build: owner textures and the packed source atlas (as in r5b), driven by sources marked "texture for".
4. **Roof profiles** beyond single shapes: cross-hall shell with valleys, strips with caps/monitors, courts/secondary wings from the plan.
