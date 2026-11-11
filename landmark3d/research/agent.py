"""Research phase: structured sources first, then AI web research, then (later) the user.

Writes into the project:
  input/geo/candidates.json, input/geo/footprint.geojson, input/geo/parts.geojson
  input/research.json        merged facts with provenance (value, source, method, confidence)
  input/sources.json         every source/file with licence and allowed use
  input/photos/*.jpg         downloaded reference images (licence recorded)
"""
from __future__ import annotations

import json
import math

from .. import geom
from ..project import Project
from . import ai, sources

PHOTO_LIMIT = 12
TEXTURE_OK = {"public_domain", "cc_by", "cc_by_sa"}          # cc_by_nc only for non-commercial projects

FACTS_SYSTEM = (
    "You are a meticulous architectural researcher. Use web search. Report only values you found in sources; "
    "never invent numbers. Every value needs a confidence (high: official/measured source, medium: one reliable "
    "article, low: rough or indirect) and the source URL. Reply with one JSON object only."
)

FACTS_PROMPT = """Landmark: {name}
Location: {location} (lat {lat}, lon {lon})
Already known from OpenStreetMap/Wikidata: {known}

Find the facts needed to build a simple but recognisable 3D massing model. Return JSON:
{{
  "values": {{
    "total_height_m": {{"value": number, "unit": "m", "confidence": "high|medium|low", "source": "url"}},
    "main_wall_height_m": {{...}},
    "length_m": {{...}}, "width_m": {{...}}, "levels": {{...}},
    "roof_form": {{"value": "flat|gable|hip|pyramid|dome|shed|mansard|gambrel|tiered|mixed: describe (tiers, eaves, upturned corners)", ...}},
    "roof_colour": {{"value": "#rrggbb or name", ...}},
    "wall_colour": {{"value": "#rrggbb or name", ...}},
    "facade_bay_spacing_m": {{...}}, "opening_type": {{"value": "rect|arch", ...}},
    "notable_elements": {{"value": [{{"type": "tower|dome|spire|wing|portal", "position": "e.g. centre of the south facade", "height_m": number, "plan_size_m": [w, d]}}], ...}},
    "plan_organisation": {{"value": "text: halls, wings, courtyards, entrances", ...}}
  }},
  "description": "2-4 sentences on the form",
  "sources": [{{"url": "...", "title": "..."}}]
}}
Omit any value you cannot source."""

MASSING_SYSTEM = (
    "You are an architectural modeller. From researched facts and reference photos you build a parametric massing "
    "model whose silhouette matches the photos: every distinct volume you can see (podium, mid-rise block, tower, "
    "crown) is its own part, with the plan shape and facade type it really has. "
    "Coordinates are metres in a local frame: x east, y north, origin at the footprint centroid. "
    "Roof types: flat, gable, hip, pyramid, dome, shed, mansard (steep lower slope on every side, shallow hip above), gambrel (two-slope gable, barn-like), none; any of them may have projecting eaves (overhang), "
    "upturned corners (upturn) and lower tiers (skirts). Reply with one JSON object only."
)

MASSING_PROMPT = """Landmark: {name} ({location})
Site outline (local metres, CCW, from OpenStreetMap; it may be the whole complex, not one volume): {footprint}
Outline size: about {length:.1f} m x {width:.1f} m, long axis at {axis:.0f} deg from east.
Outline edge directions (deg from east, share of the outline; the first is the main street frontage): {directions}
OSM building:part outlines (local metres; reuse a polygon when it matches the photos, otherwise model the volume yourself - your parts replace these): {parts}
Researched facts: {facts}
Reviewer feedback on EARLIER versions of this model (problems the reviewer saw and wants fixed - never features to reproduce): {notes}

Photos are attached in this order: photo 1, photo 2, ...{satellite_note} Look at them first. Make an INVENTORY of
every volume you can see in any photo: every piece of the building with its own roof or its own wall plane - each
wing, nave and lower aisles, side chapels, transept arms, clerestory, annexes and sacristies, porches, apse and apse
chapels, towers, belfries, spires, domes, pavilions, gatehouses. A building seen from several sides usually has many
volumes (a cathedral 15-30, a large public building 8-20); do not merge different roofs or wall planes into one box.
For each volume note its plan shape (sharp corners, rounded corners, fully rounded ends, circular, polygonal), its
storeys, which street facade it is parallel to, and its facade floor by floor. Every volume gets its own part.
Then return
{{"volumes": [{{"name": "...", "part": "<part id>", "seen_in": [photo numbers]}}, ...],
  "parts": [...], "materials": {{...}}, "views": [...], "notes": [...]}}.

Each part:
{{"id": "podium" | "tower_1" | ..., "kind": "hall|tower|wing|dome|portal|podium|crown",
 "footprint": "main" (= the whole site outline; use it only if the volume really fills the outline)
   | {{"rect": {{"cx","cy","w","d","rot_deg"}}}}
   | {{"rounded_rect": {{"cx","cy","w","d","rot_deg","r"}}}}      (r = corner radius in m)
   | {{"stadium": {{"cx","cy","w","d","rot_deg"}}}}               (both short ends fully rounded)
   | {{"circle": {{"cx","cy","r"}}}}
   | {{"ngon": {{"cx","cy","r","n","arc_deg","rot_deg"}}}}   (regular polygon of n sides; with arc_deg < 360 only that
        part of it, closed by a straight side: an apse is n 8, arc_deg 180, its round side facing rot_deg)
   | {{"polygon": [[x,y],...]}},
 "base_z": m, "height": m (wall top above base_z),
 "cantilever_of": "<id of the host part>" (upper floors projecting past the part below, or a deck fixed to a tower),
 "taper": {{"top_scale": s, "profile": "linear|ease_in|ease_out", "top_shift": [dx, dy]}}
          (walls that narrow or widen upwards: the top outline = base outline scaled by s about its centre and
           shifted by dx, dy m; ease_in = stays wide then narrows near the top like a bud),
 "roof": {{"type": "...", "height": m, "ridge_axis": "long|short", "parapet": m,
          "overhang": m (eaves projecting past the walls), "upturn": m (eave corners curling up),
          "skirts": [{{"depth": m, "rise": m, "gap": m, "overhang": m}}, ...] (lower roof tiers, bottom first)}},
 "facade": {{
   "opening_type": "rect|arch|square|oculus|storefront|none|curtain_wall", "level_height": m, "bay_spacing": m,
   "opening_width": m, "opening_height": m, "pediment": true|false,
   "arch_shape": "round|pointed|segmental" (arch heads: semicircular, Gothic pointed, or shallow),
   "lights": 1-3 (coupled windows: n narrow lights side by side in each bay), "oculus": m (a round window of this
   diameter above each window, in the wall before the next floor),
   "floors": [{{"from": 0, "to": 0, "opening_type": "...", "bay_spacing": m, "opening_width": m,
               "opening_height": m, "pediment": true|false, "awning": true|false, "arch_shape", "lights",
               "oculus", "openings": false (a blank floor: no windows)}}, ...]   (floor 0 = ground; "openings":
               false goes INSIDE a floor entry for a blank floor - never on the whole facade of a wall with windows),
   "entrance": {{"width": m, "height": m, "facing_deg": deg}}   (a large arched portal, e.g. a main entrance),
   "pilasters": {{"every": n_bays, "width": m, "depth": m, "style": "pilaster|buttress"}},   (flat columns, or
                buttresses: deep stepped piers, between bays)
   "cornice": m (0 = none), "bands": true|false}},
 "material": {{"wall": "<material>", "roof": "<material>", "trim": "<material>", "accent": "<material>"}}}}
materials: {{"<name>": {{"srgb": [r,g,b], "roughness": 0..1, "metallic": 0..1}}}} - sRGB 0-255 sampled from the photos.
views: for each photo, where it was taken from: {{"photo": n, "azimuth_deg": direction from the building to the
camera (0 = east, 90 = north), "elevation_deg": camera height angle (5-15 for street level)}}.

Modelling guidance:
- Glass: a glass material (colour as seen, roughness 0.05-0.1, metallic 0.85-0.95) as the wall,
  opening_type "curtain_wall", bay_spacing = mullion spacing (1.2-1.8 m), trim = mullion/spandrel colour.
- Classical / masonry: describe EVERY floor in facade.floors (e.g. ground arched shop windows, floor 1 square
  windows, floor 2 arched windows, floor 3 rectangular windows with pediments), the central entrance portal,
  pilasters between bays, cornice on top, parapet as a balustrade. Count the bays in the photo for bay_spacing.
- Count the window ROWS of each wall in the photos: a wall with one row of windows and a blank band above gets
  "openings": false on the blank floors, not a window on every floor. Say whether arches are round, pointed or
  segmental (arch_shape), whether windows are paired (lights: 2) and whether round windows (oculus) sit above them.
- A podium is usually a separate rectangular volume along the street, not the whole site outline.
- Apses and chevets are POLYGONAL unless clearly round: an "ngon" with arc_deg 180 (n 8 or 10) and a "hip" roof;
  radiating chapels are small ngon volumes around it, each with its own hip roof. Do not use a circle with a cone.
- Spires: roof "spire" (an octagonal spire with broaches, corner pinnacles and gabled lucarnes) on the tower top;
  "pyramid" only for plain pyramid roofs. A belfry is the top storey of the tower with tall louvred openings.
- Aisles or wings whose bays each carry a small gable: feature "gables" on that wall (see the facade step).
- Tapering towers (spires, bud / bullet shapes, pyramids of glass): ONE part with "taper" instead of a stack of
  ever smaller boxes; a slanted top is roof "shed". Several tapered parts may be stacked (top outline of one =
  base outline of the next).
- Decks (helipads, viewing platforms, canopies): a thin part (height <= 4 m) with "cantilever_of" the tower it is
  fixed to; it may project up to 25 m as long as a little of it (8 %) overlaps the tower outline at that height.
- Roofs as seen in the photos: tiled / tropical roofs project past the walls ("overhang" 0.6-2 m; look at the
  shadow line under the eaves); East Asian roofs (temples, pagodas, communal houses, Sino-French markets) curl up
  at the corners ("upturn" 0.2-1 m). Use "dome" ONLY for a real hemispherical / onion dome - a small pavilion or
  kiosk with a pointed tiled roof is "pyramid" or "hip" with overhang and upturn.
- Tiered roofs (a lower roof band around the building, then a raised upper wall - clerestory, ventilation louvres,
  an upper storey - then the main roof; market halls, temples, basilica aisles, pagoda storeys): ONE part with
  roof "skirts" instead of a wing part plus an "upper" part. Each skirt slopes inwards by "depth" m while rising
  "rise" m; the upper wall of "gap" m stands on that inset outline; the roof "type" / "height" crowns the last tier.
  A storey above (a clock storey on a gate tower) is its own part with base_z = wall top + the sum of rise + gap,
  its plan inside the inset outline. Count the tiers in the photos: every visible roof band is one skirt.
- Satellite views of tall buildings lean: roofs appear shifted away from the base. Place every volume by its BASE
  (where it meets the ground / the site outline), not by where its roof appears.
- An undercut / overhang (upper floors projecting past lower ones, a curved recess at the bottom of a block):
  split the block into a lower part with the smaller plan and upper part(s) with the larger plan and
  "cantilever_of": "<lower part id>". Several upper parts with growing plans give a curved, stepped recess.

Hard rules (the answer is rejected otherwise):
- Every part with base_z > 0 rests on the parts below it: its plan inside a part whose top equals its base_z
  (0.5 m tolerance; a tapered part offers its narrower outline at that height), or - when declared with
  cantilever_of - at least 40 % resting and projecting at most 9 m (thin decks: 8 % and 25 m).
- Ground parts stand where the satellite view shows the building (the OSM outline may cover only part of the
  complex); without a satellite view, inside the site outline (porches/gates may project slightly).
- Heights agree with the facts (storeys x level_height); the highest top matches the total height.
- Every material a part uses is defined in "materials", and every material you define is used by a part.
- rot_deg of rect / rounded_rect / stadium parts is one of the outline edge directions above (or +90). Use the
  photos to decide which street facade each volume is parallel to.
- At most 14 parts."""

MATCH_PROMPT = """Landmark: {name}. The first {n} attached images are reference photos (photo 1 .. photo {n}, in
that order). The LAST attached image is a contact sheet: the same simplified 3D model of the building seen from
8 directions around it, labelled {labels}. Camera direction of each label (from the building towards the camera):
{label_dirs}.{satellite_note} The model may still be wrong in details - match on the overall
arrangement: which volume is in front, where the tall tower stands relative to the lower blocks, which long or
short side faces the camera, and what is on the left and right.

For each photo, pick the label whose view shows the building from the same side. Also say how high the camera
was: "street" (normal street-level photo), "raised" (from an upper floor / slightly above) or "aerial".
And say what the photo is framed on: "whole" when it shows most of the building, or the id of the ONE part a
close-up centres on (e.g. a photo of just the clock tower or the main portal), so that part is rendered up close.
Parts of the model: {parts}.
Reply with one JSON object:
{{"matches": [{{"photo": 1, "label": "A".."H", "elevation": "street|raised|aerial", "subject": "whole|<part id>",
  "why": "..."}}, ...]}}"""

TRIAGE_PROMPT = """Landmark: {name} ({location}). The attached contact sheet shows {n} candidate reference photos,
numbered 1 .. {n}. They will be used to model the building's CURRENT exterior massing (volumes, roofs, facades).

For each photo say:
- kind: "exterior" (the building from outside), "aerial" (from above / a high viewpoint), "interior",
  "detail" (a close-up of a small part), "other" (another building, a map, a drawing, people ...)
- current: false when it shows an older state that differs from today (e.g. a historic photo taken before parts
  were added or changed, scaffolding hiding most of it), true otherwise
- score: 0-3, how useful it is for modelling the current exterior (3 = whole building clearly visible)
Reply with one JSON object: {{"photos": [{{"n": 1, "kind": "...", "current": true, "score": 0-3, "note": "..."}}, ...]}}"""

FACADE_PROMPT = """Landmark: {name} ({location}). This is the massing model (volumes are final - do NOT change
footprints, heights, positions or roofs):
{massing}

The attached photos show the real building. Your only task is the FACADES and the visible DETAILS of each part,
so the model reads like the photos up close. For every part, return its complete "facade" and "features":
- facade: opening_type, level_height, bay_spacing (count the bays in the photos), opening sizes, "floors"
  (one entry per floor band that differs: ground, piano nobile, attic ...; "openings": false for a blank floor),
  "entrance" (main portal), "pilasters" (with "style": "buttress" for deep stepped piers), "cornice", "bands";
  glass parts keep "curtain_wall" (mullion spacing, band per floor).
  Window shape, per facade or floor: "arch_shape": "round" | "pointed" | "segmental" (look at the heads: round
  Romanesque or classical arches are NOT pointed Gothic ones); "lights": 2 or 3 for coupled windows; "oculus":
  diameter of a round window above each window; opening_type "oculus" for a row of round windows only.
  Count the window ROWS per wall in the photos: put windows only on the floors that have them.
- features (relative to the part; z = height of the centre above the part's base; facing_deg = direction the
  facade faces, 0 = east, 90 = north; offset = metres along that facade from its middle):
  {{"type": "disc", "facing_deg", "z", "diameter", "offset", "material"}}   clock faces, rose windows, oculi
  {{"type": "panel", "facing_deg", "z", "width", "height", "offset", "depth", "material"}}   name signs, friezes,
      reliefs, balcony slabs, canopies over entrances (depth up to 3 m)
  {{"type": "mast", "x", "y", "height", "radius", "material"}}   flagpoles, antennas, crosses on top of the part
  Details with real depth; use these rather than flat panels wherever the photos show them. Here z = the BOTTOM of the
  detail above the part's base:
  {{"type": "column", "facing_deg", "z", "height", "diameter", "offset", "count", "spacing", "pair_gap", "material"}}
      free-standing round columns; count + spacing make a row in one entry; pair_gap makes paired columns
  {{"type": "colonnade", "facing_deg", "z", "width", "height", "depth", "bays", "column_diameter", "pair_gap", "offset",
      "material"}}   a loggia: dark recess behind a row of columns with a beam and floor (depth up to 3 m)
  {{"type": "balcony", "facing_deg", "z", "width", "depth", "offset", "rail_height", "material"}}   slab with railing
  {{"type": "balustrade", "facing_deg", "z", "width", "height", "offset", "material"}}   along one wall, or
  {{"type": "balustrade", "around": true, "z", "height", "material"}}   around the whole part (roof parapets)
  {{"type": "pediment", "facing_deg", "z", "width", "height", "depth", "style": "triangle|segmental|broken", "gap",
      "offset", "material"}}   over doors, windows, gables; z = its base line
  {{"type": "finial", "x", "y", "z", "height", "radius", "style": "spike|ball", "material"}}   roof ornaments; x, y in
      model coordinates; z = where it stands above the part's base, default the wall top
  {{"type": "cresting", "z", "height", "spacing", "material"}}   iron railing on a roof ridge or flat roof top
  {{"type": "gables", "facing_deg", "spacing", "width", "height", "depth", "count", "offset"}}   a row of small
      gables standing on the wall top, one per bay (aisle chapels, dormers), each with its own pitched roof
  Only details clearly visible in the photos; at most 30 per part. A flat panel remains right for signs, friezes and
  low reliefs. Statues cannot be modelled: leave them out rather than standing a board in their place.
- Walls that differ: "sides": [{{"facing_deg", any of opening_type, bay_spacing, opening_width, opening_height, sill,
  pediment, pilasters, floors, ground, "segments"}}] overrides the walls facing that direction. "segments":
  [{{"length": metres, same keys}}, ...] left to right seen from outside gives stretches of one wall their own bays,
  e.g. wider gates near the ends and a tighter rhythm between them. level_height stays the same across the part.
- "cornice_tiers": 1-4 makes the cornice a stepped moulding.
- materials: add any new material you use ({{"srgb": [r,g,b], "roughness", "metallic"}}), colours from the photos.
Reply with one JSON object: {{"parts": [{{"id": "...", "facade": {{...}}, "features": [...]}}, ...],
"materials": {{...}}, "notes": ["..."]}}"""

JUDGE_PROMPT = """You are an independent reviewer. Each attached image shows a reference PHOTO (or the satellite
plan view) of {name} on the left and a render of a 3D model on the right, from about the same viewpoint
(the last image: satellite left, model from straight above right, same scale). The renders show geometry and material
colours only: photo textures are added later, so judge shapes and architectural detail, not surface pattern.

Score how well the model matches the real building, 1-5 each (5 = matches the photo, ornament included,
3 = recognisable with clear errors or missing detail, 1 = wrong):
plan (positions, plan shapes, orientation), silhouette (outline, proportions), heights, facade (window types,
bays, entrances, and architectural detail such as columns, balconies, balustrades, pediments, mouldings and roof
ornaments: real detail in the right place scores higher than a plain wall, wrong or misplaced detail lower),
materials (colours). Then give overall 1-10 and the three biggest remaining errors.
Reply with one JSON object:
{{"scores": {{"plan": n, "silhouette": n, "heights": n, "facade": n, "materials": n}}, "overall": n,
  "errors": ["...", "...", "..."]}}"""

FACADE_PARTS_PROMPT = """Landmark: {name}. A pass over the architectural detail of a 3D model changed these parts: {parts}.
The first {n} attached images compare the reference (left) with the model BEFORE the pass (right). The next {n} show the
same views AFTER it. Decide part by part, against the reference photos, where the detail got closer to the real building.
Reply with one JSON object: {{"keep": ["ids of the parts whose AFTER version is better"], "why": "..."}}"""

SELECT_PROMPT = """Landmark: {name}. Several candidate massing models were produced in successive review rounds.
The first attached image(s) are the references: {refs}. Then come {n} candidate images, labelled CANDIDATE 1 ..
CANDIDATE {n}; each shows the candidate from the viewpoint of photo 1 (left) and from straight above at the
satellite view's extent and scale (right).

Pick the candidate that matches the real building best overall: volumes and their positions, plan shapes and
orientation (check against the satellite view), proportions and heights, facade character. Later is not
automatically better. Reply with one JSON object: {{"best": <candidate number>, "why": "..."}}"""

REVIEW_PROMPT = """You produced this massing model of {name} ({location}):
{massing}

Site outline (local metres): {footprint}
Outline edge directions: {directions}

Each attached image shows a reference PHOTO on the left and a render of YOUR MODEL on the right, from about the
same viewpoint (images in the same order as the views you gave).{satellite_note} Compare them carefully, volume by volume:
plan shapes (rounded vs sharp), which street each volume is parallel to, relative positions and setbacks,
heights and storey counts, undercuts / overhangs, the roofs (number of roof tiers / skirts, raised clerestory or
upper walls between them, projecting eaves, upturned corners, real domes vs pointed pavilion roofs), the facade
floor by floor (window shapes, bays, pilasters, entrance, cornice), and colours.

Measure, do not just glance: compare proportions in the images (e.g. tower height to width, spire height to tower
height, podium height to total height, how many storeys / bays) and name the numbers you see.
Score each aspect 1-5 (5 = indistinguishable at this level of detail): plan (positions, plan shapes, orientation),
silhouette (outline and proportions), heights, facade (window types, bays, pilasters, entrance), materials (colours).
Reply with one JSON object:
{{"verdict": "match" | "revise", "scores": {{"plan": n, "silhouette": n, "heights": n, "facade": n, "materials": n}},
  "differences": ["..."], "parts": [...], "materials": {{...}}, "views": [...], "notes": [...]}}
"match" is only allowed when EVERY score is 4 or 5; otherwise the verdict is "revise" with the corrected model.
When verdict is "revise", give the COMPLETE corrected model (same schema and hard rules as before). Fix the
largest differences first; keep what already matches. Use "match" only when no clear difference is left.
Before replying, check your corrected model against the hard rules yourself (every upper part rests on the part
below or is a declared cantilever within limits; materials defined and used) - a reply that breaks them is rejected.
A part with "footprint": "main" takes the whole OSM site outline; if the photos show that volume with a
different plan (e.g. a sharp-cornered rectangle instead of rounded ends), replace it with the right shape.
If a MODEL render does not show the building from the same side and angle as its PHOTO, correct that photo's
"views" entry (azimuth_deg = direction from the building to the camera, 0 = east, 90 = north) so the next
comparison is like for like - say so in "differences". A close-up photo (one tower, one portal) is rendered
framed on one part: keep or set "focus": "<part id>" in its views entry ("whole" for the full building)."""


def _facts_from_osm(tags: dict) -> dict:
    out = {}
    h = sources.parse_height(tags.get("height"))
    if h:
        out["total_height_m"] = {"value": h, "unit": "m", "confidence": "medium", "source": "osm:height", "method": "osm_tag"}
    lv = sources.parse_height(tags.get("building:levels"))
    if lv:
        out["levels"] = {"value": lv, "confidence": "medium", "source": "osm:building:levels", "method": "osm_tag"}
    if tags.get("roof:shape"):
        out["roof_form"] = {"value": tags["roof:shape"], "confidence": "medium", "source": "osm:roof:shape", "method": "osm_tag"}
    if tags.get("roof:height"):
        out["roof_height_m"] = {"value": sources.parse_height(tags["roof:height"]), "unit": "m", "confidence": "medium",
                                "source": "osm:roof:height", "method": "osm_tag"}
    for k, name in (("building:colour", "wall_colour"), ("roof:colour", "roof_colour")):
        if tags.get(k):
            out[name] = {"value": tags[k], "confidence": "medium", "source": f"osm:{k}", "method": "osm_tag"}
    return out


def user_directive(st: dict, notes: bool = True) -> str:
    """The instructions the person running the tool gave (note and advanced prompt), to append to an AI prompt.
    Empty when there are none. notes=False leaves out the note, for a prompt that already carries it."""
    given = [str(st.get(k) or "").strip() for k in (("notes", "advanced_prompt") if notes else ("advanced_prompt",))]
    given = [g for g in given if g]
    if not given:
        return ""
    return ("\n\nINSTRUCTIONS FROM THE PERSON RUNNING THIS TOOL (follow them, except where they break the required "
            "output format or contradict what the photos and measurements clearly show):\n" + "\n".join(given))


def run(prj: Project) -> dict:
    st = prj.state()
    name, location = st["name"], st["location"]
    report = {"steps": [], "warnings": []}
    srcs = prj.read_json("input/sources.json", {"sources": []})
    srcs["sources"] = [s for s in srcs["sources"] if s.get("origin") == "user"]   # keep user files on re-run

    def add_source(**kw):
        kw.setdefault("id", f"SRC-{len(srcs['sources']) + 1:03d}")
        srcs["sources"].append(kw)
        return kw["id"]

    # 1) location: Wikidata entity (coordinates + other-language labels), then ranked Nominatim hits.
    #    A hit must match the landmark name; the first hit is never taken blindly.
    lat, lon = st.get("lat"), st.get("lon")
    wd0, aliases = None, []
    try:
        wd0 = sources.wikidata_entity(name, lat, lon)
        if wd0 and sources.name_score(name, [wd0.get("label") or ""] + wd0.get("aliases", [])) < 0.5:
            wd0 = None
    except Exception as exc:
        report["warnings"].append(f"Wikidata lookup: {exc}")
    if wd0:
        aliases = [a for a in [wd0.get("label")] + wd0.get("aliases", []) if a and a != name][:4]
    loc_method = "user" if lat is not None else None
    if lat is None or lon is None:
        try:
            hits = sources.geocode_ranked(name, location, aliases)
        except Exception as exc:
            hits = []
            report["warnings"].append(f"geocoding failed: {exc}")
        prj.write_json("input/geo/geocode.json", hits)
        best = hits[0] if hits and hits[0]["match_score"] >= 0.5 else None
        wd_coord = (wd0["lat"], wd0["lon"]) if wd0 and wd0.get("lat") is not None else None
        if best and wd_coord:
            d = math.dist((float(best["lat"]), float(best["lon"])), wd_coord) * 111000
            if d > 800:
                report["warnings"].append(f"Nominatim best hit is {d:.0f} m from the Wikidata coordinates; using Wikidata")
                best = None
        if best:
            lat, lon, loc_method = float(best["lat"]), float(best["lon"]), f"nominatim (match {best['match_score']})"
        elif wd_coord:
            lat, lon, loc_method = wd_coord[0], wd_coord[1], "wikidata"
        if lat is not None:
            add_source(kind="geocode", path="input/geo/geocode.json", url="https://nominatim.openstreetmap.org",
                       licence="odbl", use="data", origin="research")
            prj.update_state(lambda s: s.update(lat=lat, lon=lon))
    report["steps"].append({"step": "location", "ok": lat is not None, "lat": lat, "lon": lon, "method": loc_method,
                            "aliases": aliases})

    facts = {}
    main = None
    if lat is not None:
        # 2) footprint: the OSM object Nominatim matched (OSM API, fast), else Overpass around the point;
        #    Overpass is also used for building:parts when it responds.
        geo_hits = prj.read_json("input/geo/geocode.json", []) or []
        if not geo_hits:
            try:
                rv = sources.reverse_geocode(lat, lon)
                geo_hits = [rv] if rv else []
            except Exception as exc:
                report["warnings"].append(f"reverse geocoding: {exc}")
        cands = []
        for hit in geo_hits[:3]:
            if hit.get("osm_type") in ("way", "relation") and hit.get("match_score", 1.0) >= 0.5:
                try:
                    obj = sources.footprint_from_osm_object(hit["osm_type"], int(hit["osm_id"]))
                except Exception as exc:
                    report["warnings"].append(f"OSM API {hit['osm_type']}/{hit['osm_id']}: {exc}")
                    obj = None
                if obj and len(obj["ring_lonlat"]) >= 4 and (
                        "building" in obj["tags"] or hit.get("category") in ("building", "amenity", "tourism", "historic")):
                    score = max([sources.name_score(name, sources.tag_names(obj["tags"]))] +
                                [sources.name_score(a, sources.tag_names(obj["tags"])) for a in aliases])
                    if score >= 0.5:
                        obj["tags"].setdefault("building", "yes")
                        obj["match"] = {"method": "geocoded_object", "score": score}
                        main = obj
                        break
        try:
            cands = sources.buildings_near(lat, lon)
            prj.write_json("input/geo/candidates.json", cands)
        except Exception as exc:
            report["warnings"].append(f"Overpass (building parts): {exc}")
        try:
            if main is None:
                main = sources.pick_main_building(cands, lat, lon, name, aliases)
            if main:
                match = main.get("match", {"method": "unknown", "score": 0.0})
                prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [{
                    "type": "Feature", "properties": dict(main["tags"], osm_type=main["osm_type"], osm_id=main["osm_id"],
                                                          l3d_match_method=match["method"],
                                                          l3d_match_score=match["score"]),
                    "geometry": {"type": "Polygon", "coordinates": [main["ring_lonlat"]]}}]})
                sid = add_source(kind="footprint", path="input/geo/footprint.geojson",
                                 url=f"https://www.openstreetmap.org/{main['osm_type']}/{main['osm_id']}",
                                 licence="odbl", use="data", origin="research")
                for k, v in _facts_from_osm(main["tags"]).items():
                    facts[k] = dict(v, source_id=sid)
                fp_name = main["tags"].get("name") or main["tags"].get("name:en") or "(unnamed building)"
                facts["footprint"] = {"value": f"{fp_name} - {main['osm_type']}/{main['osm_id']}",
                                      "confidence": "high" if match["score"] >= 0.5 else "low",
                                      "source": f"https://www.openstreetmap.org/{main['osm_type']}/{main['osm_id']}",
                                      "method": f"{match['method']} (name match {match['score']})", "source_id": sid}
                parts = sources.parts_inside(cands, main, lat, lon) if cands else []
                prj.write_json("input/geo/parts.geojson", {"type": "FeatureCollection", "features": [{
                    "type": "Feature", "properties": dict(p["tags"], osm_id=p["osm_id"]),
                    "geometry": {"type": "Polygon", "coordinates": [p["ring_lonlat"]]}} for p in parts]})
                report["steps"].append({"step": "footprint", "ok": True, "osm": f"{main['osm_type']}/{main['osm_id']}",
                                        "parts": len(parts)})
                # satellite plan view with the outline and the spec grid (reference only)
                try:
                    from . import satellite
                    loc, org = load_footprint(prj)
                    sat = satellite.ensure(prj, loc, org, force=True)
                    report["steps"].append({"step": "satellite", "ok": bool(sat)})
                except Exception as exc:
                    report["warnings"].append(f"satellite view: {exc}"[:200])
            else:
                report["steps"].append({"step": "footprint", "ok": False})
        except Exception as exc:
            report["warnings"].append(f"OSM footprint: {exc}")
            report["steps"].append({"step": "footprint", "ok": False, "error": str(exc)[:200]})

        # 3) Wikidata (already looked up with the location; re-check against the final coordinates)
        wd = wd0
        if wd is None:
            try:
                wd = sources.wikidata_entity(name, lat, lon)
                if wd and sources.name_score(name, [wd.get("label") or ""] + wd.get("aliases", [])) < 0.5:
                    wd = None
            except Exception as exc:
                report["warnings"].append(f"Wikidata: {exc}")
        if wd:
            prj.write_json("input/wikidata.json", wd)
            sid = add_source(kind="document", path="input/wikidata.json", url=wd["url"], licence="public_domain",
                             use="data", origin="research")
            if wd.get("height_m"):
                facts["total_height_m"] = {"value": wd["height_m"], "unit": "m", "confidence": "high",
                                           "source": wd["url"], "method": "wikidata", "source_id": sid}
            if wd.get("floors") and "levels" not in facts:
                facts["levels"] = {"value": wd["floors"], "confidence": "medium", "source": wd["url"],
                                   "method": "wikidata", "source_id": sid}
        report["steps"].append({"step": "wikidata", "ok": bool(wd), "qid": wd and wd["qid"]})

        # 4) Commons photos (licence recorded; texture use only when the licence allows it)
        try:
            imgs = sources.commons_images(lat, lon, wd and wd.get("commons_category"))
        except Exception as exc:
            imgs = []
            report["warnings"].append(f"Commons: {exc}")
        imgs.sort(key=lambda i: (i["licence"] == "unknown", -(i.get("width") or 0)))
        got = 0
        for im in imgs:
            if got >= PHOTO_LIMIT or not im.get("url"):
                continue
            fname = f"commons_{got + 1:02d}.jpg"
            try:
                size = sources.download(im["url"], prj.path("input", "photos", fname))
            except Exception as exc:
                report["warnings"].append(f"download {im['title']}: {exc}")
                continue
            add_source(kind="photo", path=f"input/photos/{fname}", url=im["page"], title=im["title"],
                       author=im["author"], licence=im["licence"], licence_short=im["licence_short"],
                       use="reference_and_texture" if im["licence"] in TEXTURE_OK else "reference_only",
                       description=im["description"], bytes=size, origin="research")
            got += 1
        report["steps"].append({"step": "commons_photos", "ok": got > 0, "count": got})

    # 5) AI web research (optional; first available provider, optional cross-check)
    provider = ai.get("research")
    ai_facts = None
    if provider is not None:
        known = {k: v["value"] for k, v in facts.items()}
        prompt = FACTS_PROMPT.format(name=name, location=location, lat=lat, lon=lon, known=json.dumps(known))
        prompt += user_directive(st)
        try:
            ai_facts, res = ai.ask_json(provider, FACTS_SYSTEM, prompt, validator=_validate_facts, web_search=True)
            prj.write_json("input/ai_facts.json", {"provider": res.provider, "model": res.model, "facts": ai_facts,
                                                   "citations": res.citations})
            for k, v in ai_facts["values"].items():
                if k not in facts or _rank(v["confidence"]) > _rank(facts[k]["confidence"]):
                    facts[k] = dict(v, method=f"ai_research:{res.provider}")
            for s in ai_facts.get("sources", []):
                add_source(kind="web", url=s["url"], title=s.get("title", ""), licence="unknown", use="data",
                           origin="ai_research")
            report["steps"].append({"step": "ai_research", "ok": True, "provider": res.provider, "model": res.model})
        except Exception as exc:
            report["warnings"].append(f"AI research: {exc}")
            report["steps"].append({"step": "ai_research", "ok": False, "error": str(exc)[:300]})
        _cross_check(prj, facts, ai_facts, provider.name, name, location, lat, lon, report)
    else:
        report["steps"].append({"step": "ai_research", "ok": False, "skipped": "no AI provider connected"})

    # 6) AI footprint finder: OSM had no outline, or only one chosen by position (name does not match)
    if provider is not None and lat is not None and lon is not None:
        fc = prj.read_json("input/geo/footprint.geojson") or {}
        props = (fc.get("features") or [{}])[0].get("properties", {}) if fc else {}
        if not fc or float(props.get("l3d_match_score", 0.0)) < 0.5:
            _ai_footprint(prj, provider, name, location, lat, lon, srcs, facts, add_source, report,
                          (ai_facts or {}).get("description", ""))

    prj.write_json("input/research.json", {"facts": facts, "report": report})
    prj.write_json("input/sources.json", srcs)
    return report


def _ai_footprint(prj, provider, name, location, lat, lon, srcs, facts, add_source, report, description):
    from . import footprint_ai, satellite
    photos = [prj.root / s["path"] for s in srcs["sources"]
              if s.get("kind") == "photo" and s.get("path") and (prj.root / s["path"]).is_file()][:3]
    cands = prj.read_json("input/geo/candidates.json", []) or []
    try:
        found = footprint_ai.resolve(prj, provider, name, location, lat, lon, cands, photos, description)
    except Exception as exc:
        report["warnings"].append(f"AI footprint: {exc}"[:200])
        found = None
    if not found or found["confidence"] == "low":
        report["steps"].append({"step": "footprint_ai", "ok": False,
                                "confidence": found and found["confidence"]})
        return
    main = found["main"]
    traced = main["osm_type"] == "ai_traced"
    prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": dict(main["tags"], osm_type=main["osm_type"], osm_id=main["osm_id"],
                                              l3d_match_method=main["match"]["method"],
                                              l3d_match_score=main["match"]["score"], l3d_ai_why=main.get("why", "")),
        "geometry": {"type": "Polygon", "coordinates": [main["ring_lonlat"]]}}]})
    prj.write_json("input/geo/parts.geojson", {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": dict(c["tags"], osm_id=c["osm_id"], **{"building:part": "yes"}),
        "geometry": {"type": "Polygon", "coordinates": [c["ring_lonlat"]]}} for c in found["extra"]]})
    url = None if traced else f"https://www.openstreetmap.org/{main['osm_type']}/{main['osm_id']}"
    sid = add_source(kind="footprint", path="input/geo/footprint.geojson", url=url,
                     licence="ai_traced_on_esri_imagery" if traced else "odbl", use="data", origin="ai_footprint",
                     note=main.get("why", ""))
    add_source(kind="satellite", path="input/geo/footprint_candidates.jpg",
               title="Footprint candidates on the satellite view (numbered OSM outlines)",
               author=satellite.ESRI_ATTRIBUTION, licence="esri_world_imagery", use="reference_only",
               origin="ai_footprint")
    label = "traced by AI on the satellite view" if traced else         f"{main['tags'].get('name') or '(unnamed)'} - {main['osm_type']}/{main['osm_id']}"
    facts["footprint"] = {"value": label, "confidence": found["confidence"], "source": url or "satellite view",
                          "method": main["match"]["method"], "source_id": sid}
    report["steps"].append({"step": "footprint_ai", "ok": True, "choice": "traced" if traced else "osm",
                            "confidence": found["confidence"], "extra_parts": len(found["extra"])})
    try:
        loc, org = load_footprint(prj)
        satellite.ensure(prj, loc, org, force=True)
    except Exception as exc:
        report["warnings"].append(f"satellite view: {exc}"[:200])


def _validate_facts(obj):
    from ..spec import validate_facts
    return validate_facts(obj)


def _rank(c):
    return {"low": 0, "medium": 1, "high": 2, "verified": 3}.get(c, 0)


def _cross_check(prj, facts, ai_facts, used, name, location, lat, lon, report):
    from .. import settings as settings_mod
    if not settings_mod.load().get("cross_check") or ai_facts is None:
        return
    others = [p for p in ai.available() if p != used]
    if not others:
        return
    try:
        other = ai.make(others[0])
        prompt = FACTS_PROMPT.format(name=name, location=location, lat=lat, lon=lon, known="{}")
        second, res = ai.ask_json(other, FACTS_SYSTEM, prompt, validator=_validate_facts, web_search=True)
    except Exception as exc:
        report["warnings"].append(f"cross-check: {exc}")
        return
    disagreements = []
    for k, v in second["values"].items():
        a = ai_facts["values"].get(k, {}).get("value")
        b = v.get("value")
        if isinstance(a, (int, float)) and isinstance(b, (int, float)) and max(abs(a), abs(b)) > 0:
            if abs(a - b) / max(abs(a), abs(b)) > 0.15:
                disagreements.append({"key": k, used: a, res.provider: b})
    prj.write_json("input/ai_crosscheck.json", {"provider": res.provider, "facts": second,
                                                "disagreements": disagreements})
    for d in disagreements:
        if d["key"] in facts:
            facts[d["key"]]["confidence"] = "low"
            facts[d["key"]]["disagreement"] = d
    report["steps"].append({"step": "ai_cross_check", "ok": True, "disagreements": len(disagreements)})


# ----------------------------------------------------------------------------- footprint helpers
def load_footprint(prj: Project):
    fc = prj.read_json("input/geo/footprint.geojson")
    if not fc or not fc.get("features"):
        return None, None
    ring = fc["features"][0]["geometry"]["coordinates"][0]
    lon0 = sum(p[0] for p in ring) / len(ring)
    lat0 = sum(p[1] for p in ring) / len(ring)
    local = geom.ensure_ccw([geom.lonlat_to_local(x, y, lon0, lat0) for x, y in ring])
    c = geom.centroid(local)
    # re-centre on the true centroid
    lon_c = lon0 + math.degrees(c[0] / (geom.R_EARTH * math.cos(math.radians(lat0))))
    lat_c = lat0 + math.degrees(c[1] / geom.R_EARTH)
    local = [(x - c[0], y - c[1]) for x, y in local]
    return local, (lat_c, lon_c)


def load_parts(prj: Project, origin):
    fc = prj.read_json("input/geo/parts.geojson", {"features": []})
    out = []
    for f in fc.get("features", []):
        ring = f["geometry"]["coordinates"][0]
        poly = geom.ensure_ccw([geom.lonlat_to_local(x, y, origin[1], origin[0]) for x, y in ring])
        if len(poly) >= 3:
            out.append({"tags": f["properties"], "polygon": poly})
    return out
