"""Phase functions, input gate, spec builder and the run-until-blocked runner."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
import traceback

from . import doctor, geom, settings as settings_mod, spec as spec_mod
from .detail import engine as detail_engine, registry as detail_registry
from .engine import runner as engine_runner
from .project import PHASES, Project
from .research import agent, ai

# Which phase a change request on an approval sends the project back to.
REWIND = {"sources_review": "research", "spec_review": "spec", "texture_review": "textures", "build_review": "spec",
          "release_review": "build"}

COLOR_NAMES = {"red": (170, 50, 40), "dark red": (120, 30, 25), "brown": (120, 80, 50), "yellow": (232, 196, 104),
               "cream": (236, 226, 196), "white": (238, 236, 230), "grey": (150, 150, 150), "gray": (150, 150, 150),
               "black": (40, 40, 40), "green": (70, 120, 80), "blue": (70, 100, 150), "beige": (220, 205, 170),
               "orange": (210, 120, 60), "terracotta": (190, 90, 60), "gold": (200, 160, 60), "pink": (220, 160, 160)}


def parse_colour(v):
    if isinstance(v, list) and len(v) == 3:
        return [int(x) for x in v]
    s = str(v or "").strip().lower()
    m = re.match(r"#?([0-9a-f]{6})$", s)
    if m:
        h = m.group(1)
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    for k in sorted(COLOR_NAMES, key=len, reverse=True):
        if k in s:
            return list(COLOR_NAMES[k])
    return None


# ----------------------------------------------------------------------------- facts helpers
def merged_facts(prj: Project) -> dict:
    facts = dict(prj.read_json("input/research.json", {}).get("facts", {}))
    for k, v in prj.state().get("user_inputs", {}).items():
        if v not in (None, ""):
            facts[k] = {"value": v, "confidence": "high", "source": "user", "method": "user_input"}
    return facts


def fval(facts, key, cast=float):
    v = facts.get(key, {}).get("value")
    if v is None:
        return None
    try:
        return cast(v)
    except (TypeError, ValueError):
        return None


# ----------------------------------------------------------------------------- input gate
def missing_items(prj: Project) -> list[dict]:
    st = prj.state()
    facts = merged_facts(prj)
    items = []
    if st.get("lat") is None:
        items.append({"id": "latlon", "kind": "latlon", "label": "Location (latitude, longitude)",
                      "help": "Geocoding found nothing. Enter coordinates, e.g. 10.7725, 106.6980."})
    fp = prj.read_json("input/geo/footprint.geojson")
    if fp is None:
        items.append({"id": "footprint", "kind": "footprint", "label": "Building footprint",
                      "help": "No OSM building whose name matches the landmark was found. Enter its OSM way/relation "
                              "id (e.g. way/39514795)."})
    else:
        props = fp["features"][0]["properties"]
        score = float(props.get("l3d_match_score", 1.0))
        if score < 0.5 and st.get("user_inputs", {}).get("footprint_confirmed") != "yes":
            nm = props.get("name") or "(unnamed building)"
            items.append({"id": "footprint_confirmed", "kind": "select", "options": ["yes", "no"], "default": "no",
                          "label": f"Is OSM {props.get('osm_type')}/{props.get('osm_id')} \"{nm}\" the landmark?",
                          "help": "It was chosen by position only (its name does not match). Answer 'no' to enter "
                                  "the correct OSM id."})
    has_height = any(fval(facts, k) for k in ("main_wall_height_m", "levels", "total_height_m"))
    if not has_height:
        items.append({"id": "main_wall_height_m", "kind": "number", "unit": "m",
                      "label": "Main wall height (eaves) in metres",
                      "help": "No height found in OSM, Wikidata or web research."})
    if not facts.get("roof_form"):
        items.append({"id": "roof_form", "kind": "select", "options": spec_mod.ROOF_TYPES, "default": "hip",
                      "label": "Main roof form", "help": "Not found in sources. Pick the closest form."})
    photos = [s for s in prj.read_json("input/sources.json", {"sources": []})["sources"] if s.get("kind") == "photo"]
    if not photos:
        items.append({"id": "photos", "kind": "files", "label": "Reference photos",
                      "help": "No licensed photos found. Upload at least one photo (front view preferred) and state "
                              "the licence or your permission."})
    return items


# ----------------------------------------------------------------------------- spec builder
def build_spec(prj: Project) -> dict:
    st = prj.state()
    main, origin = agent.load_footprint(prj)
    if main is None:
        raise RuntimeError("no footprint")
    facts = merged_facts(prj)
    prov, defaults_used = {}, []

    def note_default(key, value):
        defaults_used.append({"key": key, "value": value})
        prov[key] = {"method": "module_default", "confidence": "low"}
        return value

    length, width, axis = spec_mod.footprint_dims(main)
    levels = fval(facts, "levels")
    wall_h = fval(facts, "main_wall_height_m")
    if wall_h:
        prov["main.height"] = {"method": facts["main_wall_height_m"].get("method"), "confidence": facts["main_wall_height_m"]["confidence"]}
    elif levels:
        wall_h = levels * 3.5
        prov["main.height"] = {"method": "levels x 3.5 m", "confidence": "medium"}
    elif fval(facts, "total_height_m"):
        wall_h = fval(facts, "total_height_m") * 0.75
        prov["main.height"] = {"method": "0.75 x total height", "confidence": "low"}
    else:
        wall_h = note_default("main.height", 8.0)
    roof_form = str(facts.get("roof_form", {}).get("value", "") or "").lower()
    roof_type = next((v for k, v in spec_mod.OSM_ROOF.items() if k in roof_form), None) or \
        next((t for t in spec_mod.ROOF_TYPES if t in roof_form), None)
    if roof_type is None:
        roof_type = note_default("main.roof.type", "hip")
    roof_h = fval(facts, "roof_height_m")
    if not roof_h:
        # default rise: 15 % of the short side, capped at 0.7 x wall height (large halls do not get 20 m roofs)
        roof_h = note_default("main.roof.height", round(max(1.0, min(min(length, width) * 0.15, wall_h * 0.7)), 2))
    bay = fval(facts, "facade_bay_spacing_m") or note_default("facade.bay_spacing", 4.0)
    opening = str(facts.get("opening_type", {}).get("value", "rect")).lower()
    opening = "arch" if "arch" in opening else "rect"

    materials = {k: dict(v) for k, v in spec_mod.DEFAULT_MATERIALS.items()}
    for key, fk in (("wall", "wall_colour"), ("roof", "roof_colour")):
        c = parse_colour(facts.get(fk, {}).get("value"))
        if c:
            materials[key]["srgb"] = c
            prov[f"materials.{key}"] = {"method": facts[fk].get("method"), "confidence": facts[fk]["confidence"]}
        else:
            defaults_used.append({"key": f"materials.{key}", "value": materials[key]["srgb"]})

    parts = [{"id": "main", "kind": "hall", "footprint": "main", "base_z": 0.0, "height": round(wall_h, 2),
              "roof": {"type": roof_type, "height": round(roof_h, 2), "parapet": 0.8 if roof_type == "flat" else 0.0},
              "facade": {"openings": True, "bay_spacing": bay, "opening_type": opening, "level_height": 3.5},
              "material": {"wall": "wall", "roof": "roof", "trim": "trim"}}]
    for i, p in enumerate(agent.load_parts(prj, origin)):
        tags = p["tags"]
        h = spec_mod_parse(tags.get("height"))
        mh = spec_mod_parse(tags.get("min_height")) or 0.0
        lv = spec_mod_parse(tags.get("building:levels"))
        if not h and lv:
            h = lv * 3.5 + mh
        if not h:
            continue
        rt = spec_mod.OSM_ROOF.get(tags.get("roof:shape", ""), "flat")
        rh = spec_mod_parse(tags.get("roof:height")) or (0.0 if rt == "flat" else 2.0)
        local = [(x - 0, y - 0) for x, y in p["polygon"]]
        parts.append({"id": f"osm_part_{i + 1}", "kind": tags.get("building:part", "part"),
                      "footprint": {"polygon": [list(q) for q in local]}, "base_z": mh,
                      "height": round(max(0.5, h - mh - (rh if rt != "flat" else 0.0)), 2),
                      "roof": {"type": rt, "height": rh},
                      "facade": {"openings": True, "bay_spacing": bay, "opening_type": opening, "level_height": 3.5},
                      "material": {"wall": "wall", "roof": "roof", "trim": "trim"}})
        prov[f"part.osm_part_{i + 1}"] = {"method": "osm_building_part", "confidence": "medium"}
    if len(parts) > 1:
        # OSM 3D convention: when building:parts exist they describe the volume; the outline is not drawn.
        parts[0]["enabled"] = False
        parts[0]["note"] = "outline replaced by OSM building:parts"

    ai_notes = []
    review_log = []
    photo_views_out = []
    photos_used = []
    ai_error = None
    quality = None
    fit_report = None
    provider = ai.get("spec")
    if provider is not None:
        photos, seen = [], set()
        for s_ in prj.read_json("input/sources.json", {"sources": []})["sources"]:
            f = prj.root / s_.get("path", "")
            if s_.get("kind") != "photo" or s_.get("use") == "excluded" or not f.is_file():
                continue
            h = hashlib.sha256(f.read_bytes()).hexdigest()
            if h not in seen:                      # the same photo uploaded twice counts once
                seen.add(h)
                photos.append(f)
        triage_log = []
        if len(photos) > 2:
            photos, triage_log = triage_photos(prj, provider, photos, st)
        photos = photos[:6]
        photos_used = [f.relative_to(prj.root).as_posix() for f in photos]
        from .research import satellite
        satellite.ensure(prj, main, origin)
        sat_imgs = satellite.images_for_ai(prj)
        sat_note = ("" if not sat_imgs else
                    f" After the photos come {len(sat_imgs)} satellite plan view(s), north up: the first has the "
                    "OSM site outline in yellow and the spec grid labelled in metres (x east, y north). Use it for "
                    "positions, sizes and orientations of every volume - it is the most reliable plan information.")
        notes = change_requests(st)
        prompt = agent.MASSING_PROMPT.format(
            name=st["name"], location=st["location"],
            footprint=json.dumps([[round(x, 1), round(y, 1)] for x, y in main]),
            length=length, width=width, axis=axis,
            directions=", ".join(f"{d:g} ({s:.0%})" for d, s in geom.edge_direction_shares(main)) or "none",
            parts=json.dumps([{**{k: v for k, v in p.items() if k not in ("footprint", "facade", "material")},
                               "footprint": {"polygon": [[round(x, 1), round(y, 1)] for x, y in
                                                         p["footprint"]["polygon"]]}}
                              for p in parts[1:] if isinstance(p.get("footprint"), dict) and "polygon" in p["footprint"]]),
            facts=json.dumps({k: v.get("value") for k, v in facts.items()}, ensure_ascii=False),
            notes=json.dumps(notes + [st.get("notes", "")], ensure_ascii=False), satellite_note=sat_note)
        prompt += agent.user_directive(st, notes=False)
        try:
            def massing_check(obj):
                errs = spec_mod.validate_massing(obj)
                if errs:
                    return errs
                if not obj["parts"]:
                    return ["parts must not be empty"]
                from . import exposure
                return (material_errors(obj, int(st["budgets"].get("materials_max", 10)))
                        + orientation_errors(obj["parts"], main)
                        + spec_mod.structure_errors(obj["parts"] + parts[1:], main, facts)
                        + exposure.feature_issues(obj["parts"], main))     # features on buried walls

            massing, res = ai.ask_json(provider, agent.MASSING_SYSTEM, prompt, validator=massing_check,
                                       images=photos + sat_imgs, retries=2, accept_invalid=True, repair=repair_answer)
            if getattr(res, "validation_errors", None):
                ai_notes_extra = [f"AI could not fix: {e}" for e in res.validation_errors[:6]]
            else:
                ai_notes_extra = []
            normalise_glass(massing)
            rounds = int(settings_mod.load().get("photo_review_rounds", 2) or 0)
            if rounds > 0 and photos:
                massing, review_log = photo_review(prj, provider, massing, photos, massing_check, main, materials,
                                                   st, rounds, sat_imgs)
                normalise_glass(massing)
            # The AI reads positions and sizes off a satellite image; its plan is fitted to the measured footprint
            # (whole model, then each ground volume), kept only where plan IoU improves without new structure errors
            from . import plan_fit
            fitted, fit_report = plan_fit.fit(massing["parts"], main,
                                              lambda ps: spec_mod.structure_errors(ps + parts[1:], main, facts))
            if fit_report["iou_after"] > fit_report["iou_before"]:
                massing = dict(massing, parts=fitted)
            prj.log("plan fitted to the footprint", fit_report)
            use_judge = bool(photos) and settings_mod.load().get("judge", True)
            # The facade grammar decides the generated textures. The judge is too noisy to gate it (2026-11-10 A/B),
            # so by default one facade pass is accepted on the deterministic checks of its validator (facade_issues,
            # check_facade) and the judge reads the final model once, for the record. "facade_gate": "judge" keeps
            # the earlier judge-gated rounds for comparison.
            judge_gated = settings_mod.load().get("facade_gate", "checks") == "judge"
            if photos and settings_mod.load().get("facade_pass", True) and not judge_gated:
                refined, facade_log = facade_pass(prj, provider, massing, photos, massing_check, st, main=main)
                facade_log["round"] = 1
                if refined is not massing:
                    facade_log["result"] = "kept: passed the deterministic facade checks"
                    massing = refined
                review_log.append(facade_log)
            if use_judge and not judge_gated:
                quality = judge(prj, provider, massing, photos, sat_imgs, main, materials, st)
            if use_judge and judge_gated:
                quality = judge(prj, provider, massing, photos, sat_imgs, main, materials, st)
            if photos and settings_mod.load().get("facade_pass", True) and judge_gated:
                rounds_f = 1 + max(0, int(settings_mod.load().get("facade_review_rounds", 2) or 0))
                rejected_notes = []
                for fr in range(rounds_f):
                    q_ok = use_judge and (quality or {}).get("overall") is not None
                    refined, facade_log = facade_pass(
                        prj, provider, massing, photos, massing_check, st, main=main,
                        feedback=list((quality or {}).get("errors") or []) + rejected_notes if fr > 0 and q_ok else None,
                        sheets=[prj.root / i for i in (quality or {}).get("images", [])] if fr > 0 and q_ok else None)
                    facade_log["round"] = fr + 1
                    if refined is massing:
                        review_log.append(facade_log)
                        break
                    if not q_ok:
                        massing = refined
                        review_log.append(facade_log)
                        break
                    # A facade round is kept only if the independent judge scores it at least as well (overall, then
                    # the facade aspect). If the whole round is worse, the parts that did improve can still be kept,
                    # under the same test; a discarded round feeds the next one instead of ending the pass.
                    q2 = judge(prj, provider, refined, photos, sat_imgs, main, materials, st, out_name=f"judge_f{fr + 1}")
                    b_all, b_fac = quality.get("overall"), (quality.get("scores") or {}).get("facade", 0)
                    a_all, a_fac = score_pair(q2)
                    if not not_worse(quality, q2):
                        mixed, kept = keep_better_parts(prj, provider, massing, refined, quality, q2, st)
                        if mixed is not None:
                            q3 = judge(prj, provider, mixed, photos, sat_imgs, main, materials, st,
                                       out_name=f"judge_f{fr + 1}p")
                            if not_worse(quality, q3):
                                refined, q2, facade_log["parts_kept"] = mixed, q3, kept
                                a_all, a_fac = score_pair(q3)
                                facade_log["verdict"] = (facade_log.get("verdict") or "") + f" ({len(kept)} parts kept)"
                    if not_worse(quality, q2):
                        massing, quality = refined, q2
                        facade_log["result"] = f"kept: judge {b_all} -> {a_all}, facade {b_fac} -> {a_fac}"
                        review_log.append(facade_log)
                    else:
                        facade_log["result"] = (f"discarded: judge {b_all} -> {a_all}, facade {b_fac} -> {a_fac}; "
                                                f"previous facades kept")
                        facade_log["verdict"] = (facade_log.get("verdict") or "") + " (discarded)"
                        review_log.append(facade_log)
                        rejected_notes = ["The last facade attempt scored lower, so it was not used: "
                                          + str(e) for e in ((q2 or {}).get("errors") or [])[:3]]
                        continue
                    if a_fac is not None and a_fac >= 4:
                        break
            ai_parts = massing["parts"]
            photo_views_out = massing.get("views") or []
            if ai_parts:
                # The AI model replaces the OSM parts (it received their outlines and may reuse them); adding both
                # stacked duplicate volumes on top of each other.
                parts = list(ai_parts)
                for p in ai_parts:
                    prov[f"part.{p['id']}"] = {"method": f"ai_massing:{res.provider}", "confidence": "medium"}
                for k, v in (massing.get("materials") or {}).items():
                    materials.setdefault(k, {})
                    materials[k] = {"srgb": v["srgb"], "roughness": v.get("roughness", 0.75), **({"metallic": v["metallic"]} if "metallic" in v else {})}
                    prov[f"materials.{k}"] = {"method": f"ai_massing:{res.provider}", "confidence": "medium"}
                defaults_used = [d for d in defaults_used if not d["key"].startswith(("materials", "main."))]
                ai_notes = massing.get("notes", []) + ai_notes_extra
        except Exception as exc:
            ai_notes = [f"AI massing failed, using sources/defaults: {exc}"[:300]]
            ai_error = str(exc)[:300]

    observation_log = []
    if provider is not None and settings_mod.load().get("facade_observations", False) and photos_used:
        from . import observe
        sources_map = {s["id"]: s for s in prj.read_json("input/sources.json", {"sources": []})["sources"]}
        observation_log, _obs_errors = observe.run_for_photos(prj, provider, st["name"], photos_used, sources_map,
                                                              {p["id"] for p in parts})

    spec = {
        "spec_version": 1,
        "id": st["id"], "name": st["name"], "location": st["location"],
        "origin": {"lat": origin[0], "lon": origin[1]},
        "footprint": {"local": [[round(x, 3), round(y, 3)] for x, y in main],
                      "length_m": round(length, 2), "width_m": round(width, 2), "axis_deg": round(axis, 1)},
        "parts": parts,
        "materials": materials,
        "budgets": st["budgets"],
        "provenance": prov,
        "defaults_used": defaults_used,
        "notes": ai_notes,
        "photo_review": review_log,
        "plan_fit": fit_report,
        "quality": quality,
        "photo_triage": triage_log if provider is not None else [],
        "photos": photos_used,                      # "photo n" in views / renders = photos[n-1]
        "views": photo_views_out,
        "observations": observation_log,            # empty unless the facade_observations setting is on
    }
    if settings_mod.load().get("facade_observations", False):
        # M3: the observed floor and bay grid, with the person's corrections, feeds the facade of each part.
        from . import facade_grid, corrections as corr_mod
        from . import observe as observe_mod
        saved_obs = (observe_mod.load(prj) or {}).get("observations", [])
        spec["observed_grid"] = facade_grid.apply(spec["parts"], main, saved_obs, corr_mod.load(prj))
    errs = spec_mod.validate_massing({"parts": spec["parts"], "materials": spec["materials"]})
    if errs:
        raise RuntimeError("spec invalid: " + "; ".join(errs[:5]))
    spec["estimated_triangles"] = spec_mod.estimate_triangles(spec)
    spec["issues"] = spec_mod.check_structure(spec["parts"], main, facts) + review_issues(spec)
    if ai_error:
        # An AI is configured but did not answer (quota, sign-in, network): the plan is only the fallback. Block A2
        # so a degraded model is never approved by accident; rerun the spec later, or turn the AI off to go on.
        spec["issues"].insert(0, {"severity": "error", "part": None, "code": "ai_failed",
                                  "message": f"AI massing failed ({ai_error}); this plan is the no-AI fallback. Run the "
                                             f"spec again when the AI is available, or turn the AI off in Connections "
                                             f"to continue without it."})
    if observation_log:
        from . import observe as observe_mod
        from . import observation_grammar as obs_grammar
        saved = observe_mod.load(prj) or {"observations": []}
        for part in parts:
            for msg in obs_grammar.structure_conflicts(saved["observations"], part["id"]):
                spec["issues"].append({"severity": "warning", "part": part["id"], "code": "observation_conflict",
                                       "message": msg + " - review at A2 before the photos are used for structure"})
    for e in observation_log:
        if str(e.get("result", "")).startswith("failed"):
            spec["issues"].append({"severity": "warning", "part": None, "code": "observation_failed",
                                   "message": f"facade observation failed for {e.get('photo')}: {e['result']}"[:300]})
    for e in review_log:
        if "limit" in str(e.get("result", "")).lower() or "failed" in str(e.get("result", "")).lower():
            spec["issues"].append({"severity": "warning", "part": None, "code": "photo_review_incomplete",
                                   "message": f"photo review step did not finish: {e['result']}"[:300]})
            break
    prj.write_json("spec/landmark_spec.json", spec)
    return spec


def _side_by_side(photo: Path, render: Path, out: Path, label: str) -> Path:
    from PIL import Image, ImageDraw
    a = Image.open(photo).convert("RGB")
    b = Image.open(render).convert("RGB")
    h = 640
    a = a.resize((max(1, int(a.width * h / a.height)), h))
    b = b.resize((max(1, int(b.width * h / b.height)), h))
    sheet = Image.new("RGB", (a.width + b.width + 12, h + 34), "white")
    sheet.paste(a, (0, 34))
    sheet.paste(b, (a.width + 12, 34))
    d = ImageDraw.Draw(sheet)
    d.text((8, 10), f"PHOTO ({label})", fill="black")
    d.text((a.width + 20, 10), "MODEL (same viewpoint)", fill="black")
    sheet.thumbnail((1800, 1800))
    sheet.save(out, "JPEG", quality=85)
    return out


VIEW_LABELS = "ABCDEFGH"
REVIEW_ASPECTS = ("plan", "silhouette", "heights", "facade", "materials")
ELEVATIONS = {"street": 8.0, "raised": 25.0, "aerial": 50.0}


def _contact_sheet(renders: list, labels: str, out: Path) -> Path:
    from PIL import Image, ImageDraw, ImageFont
    cells = [Image.open(p).convert("RGB") for p in renders]
    w, h = 480, 320
    sheet = Image.new("RGB", (w * 4, h * 2), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 56)
    except Exception:
        font = ImageFont.load_default()
    for i, im in enumerate(cells):
        im = im.resize((w, h))
        d = ImageDraw.Draw(im)
        d.rectangle((0, 0, 70, 70), fill="white")
        d.text((14, 4), labels[i], fill="black", font=font)
        sheet.paste(im, ((i % 4) * w, (i // 4) * h))
    sheet.save(out, "JPEG", quality=85)
    return out


def triage_photos(prj: Project, provider, photos: list, st: dict, max_keep: int = 6):
    """Keep the photos that show the CURRENT EXTERIOR: one AI look at a numbered contact sheet of all candidates.
    Photos the user uploaded are always kept (first); on any failure the original order is used."""
    from PIL import Image, ImageDraw, ImageFont
    cands = photos[:16]
    out_root = prj.root / "reports" / "photo_review"
    out_root.mkdir(parents=True, exist_ok=True)
    w, h, cols = 360, 270, 4
    rows = (len(cands) + cols - 1) // cols
    sheet = Image.new("RGB", (w * cols, h * rows), "white")
    try:
        font = ImageFont.truetype("arial.ttf", 44)
    except Exception:
        font = ImageFont.load_default()
    for i, f in enumerate(cands):
        try:
            im = Image.open(f).convert("RGB")
        except Exception:
            continue
        im.thumbnail((w - 6, h - 6))
        cell = Image.new("RGB", (w, h), (235, 235, 235))
        cell.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
        d = ImageDraw.Draw(cell)
        d.rectangle((0, 0, 64, 56), fill="white")
        d.text((8, 4), str(i + 1), fill="black", font=font)
        sheet.paste(cell, ((i % cols) * w, (i // cols) * h))
    sheet_path = out_root / "triage.jpg"
    sheet.save(sheet_path, "JPEG", quality=85)
    prompt = agent.TRIAGE_PROMPT.format(name=st["name"], location=st["location"], n=len(cands))

    def check(o):
        ps = o.get("photos")
        if not isinstance(ps, list) or not ps:
            return ["photos must be a non-empty list"]
        return [f"bad entry {p}" for p in ps if not (isinstance(p.get("n"), int) and 1 <= p["n"] <= len(cands))]
    try:
        obj, _ = ai.ask_json(provider, agent.MASSING_SYSTEM, prompt, validator=check, images=[sheet_path], retries=1)
    except Exception as exc:
        return photos, [{"result": f"photo triage failed, original order kept: {exc}"[:300]}]
    user_paths = {str(prj.root / s["path"]) for s in prj.read_json("input/sources.json", {"sources": []})["sources"]
                  if s.get("origin") == "user" and s.get("path")}
    info = {p["n"]: p for p in obj["photos"]}
    rank = []
    for i, f in enumerate(cands, start=1):
        p = info.get(i, {})
        good = p.get("kind") in ("exterior", "aerial") and p.get("current", True) and float(p.get("score", 0)) >= 2
        is_user = str(f) in user_paths
        rank.append((0 if is_user else 1, -float(p.get("score", 0)), i, f, good or is_user))
    kept = [f for _, _, _, f, ok in sorted(rank) if ok][:max_keep]
    if len(kept) < 2:                              # too strict for this landmark: fall back to the best scored
        kept = [f for _, _, _, f, _ in sorted(rank)][:max_keep]
    log = [{"n": i, "file": f.name, "kind": info.get(i, {}).get("kind"), "current": info.get(i, {}).get("current"),
            "score": info.get(i, {}).get("score"), "kept": f in kept, "note": str(info.get(i, {}).get("note", ""))[:160]}
           for i, f in enumerate(cands, start=1)]
    prj.log("photo triage", {"kept": len(kept), "of": len(cands)})
    return kept, log


def cam_of(v: dict, parts=None, default_el: float = 10.0) -> dict:
    """Render camera for a photo view: direction, height angle and, for close-ups, the part the photo centres on
    (the camera then frames that part instead of the whole building)."""
    c = {"azimuth_deg": float(v.get("azimuth_deg", 270)), "elevation_deg": float(v.get("elevation_deg", default_el))}
    f = v.get("focus")
    if f and f != "whole" and (parts is None or any(p.get("id") == f for p in parts)):
        c["focus"] = str(f)
    return c


def _compass(az: float) -> str:
    names = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]
    return names[int(((az % 360) + 22.5) // 45) % 8]


def match_viewpoints(prj: Project, provider, massing: dict, photos: list, main, base_materials: dict, st: dict,
                     out_root: Path, sat_imgs: list | None = None):
    """Which side was each photo taken from? Render the model from 8 directions (every 45 degrees from the main
    frontage) and let the AI pick a label per photo - recognising a side is far more reliable than guessing a
    compass angle. Returns (views, log entry); views = [] when it could not be done."""
    from .engine import runner as engine_runner
    dirs = geom.edge_direction_shares(main)
    d0 = dirs[0][0] if dirs else 0.0
    cand_az = {VIEW_LABELS[k]: (d0 - 90.0 + 45.0 * k) % 360.0 for k in range(8)}
    mats = {k: dict(v) for k, v in base_materials.items()}
    mats.update(massing.get("materials") or {})
    cand = {"footprint": {"local": [list(p) for p in main]}, "parts": massing["parts"], "materials": mats,
            "budgets": st["budgets"]}
    views = {f"side_{lab}": {"azimuth_deg": az, "elevation_deg": 12.0} for lab, az in cand_az.items()}
    renders = engine_runner.preview(prj, cand, views, out_root / "sides")
    entry = {"round": 0, "step": "viewpoints"}
    if len(renders) < 8:
        entry["result"] = "could not render the 8 sides - viewpoint matching skipped"
        return [], entry
    by_name = {p.stem: p for p in renders}
    sheet = _contact_sheet([by_name[f"side_{lab}"] for lab in VIEW_LABELS], VIEW_LABELS, out_root / "sides_AH.jpg")
    use = photos[:4]
    sat_imgs = list(sat_imgs or [])[:1]
    prompt = agent.MATCH_PROMPT.format(
        name=st["name"], n=len(use), labels=", ".join(VIEW_LABELS),
        parts=", ".join(f"{p['id']} ({p.get('kind', 'part')})" for p in massing["parts"]),
        label_dirs="; ".join(f"{lab} = {_compass(az)} ({az:.0f} deg)" for lab, az in cand_az.items()),
        satellite_note=("" if not sat_imgs else " Between the photos and the contact sheet there is a satellite plan "
                        "view (north up, site outline in yellow): use the streets and the surroundings in it to "
                        "decide from which side each photo was taken."))

    def check(o):
        ms = o.get("matches")
        if not isinstance(ms, list) or not ms:
            return ["matches must be a non-empty list"]
        return [f"bad label in {m}" for m in ms if str(m.get("label", "")).upper() not in VIEW_LABELS]
    try:
        obj, _ = ai.ask_json(provider, agent.MASSING_SYSTEM, prompt, validator=check,
                             images=list(use) + sat_imgs + [sheet], retries=1)
    except Exception as exc:
        entry["result"] = f"viewpoint matching failed: {exc}"[:300]
        return [], entry
    out = []
    for m in obj["matches"]:
        try:
            n = int(m.get("photo"))
        except (TypeError, ValueError):
            continue
        if 1 <= n <= len(use) and all(v["photo"] != n for v in out):
            lab = str(m["label"]).upper()
            ent = {"photo": n, "azimuth_deg": round(cand_az[lab], 1),
                   "elevation_deg": ELEVATIONS.get(str(m.get("elevation", "street")), 8.0), "label": lab}
            subject = str(m.get("subject") or "whole")
            if subject != "whole" and any(p.get("id") == subject for p in massing["parts"]):
                ent["focus"] = subject                    # a close-up: frame that part, not the whole building
            out.append(ent)
    entry.update({"images": [sheet.relative_to(prj.root).as_posix()],
                  "differences": [f"photo {m.get('photo')} = side {str(m.get('label')).upper()} "
                                  f"({m.get('elevation', 'street')}): {m.get('why', '')}"[:240] for m in obj["matches"]],
                  "verdict": "viewpoints matched"})
    prj.log("photo viewpoints matched", {"views": out})
    return out, entry


def photo_review(prj: Project, provider, massing: dict, photos: list, check, main, base_materials: dict, st: dict,
                 rounds: int, sat_imgs: list | None = None):
    """Render the candidate model from each photo's viewpoint, show the AI photo | render pairs, let it revise.

    Stops at the first "match", when the AI cannot produce a valid revision, or after `rounds` rounds.
    Returns (massing, log); comparison sheets are kept in reports/photo_review/ for the A2 page."""
    import shutil
    from .engine import runner as engine_runner
    out_root = prj.root / "reports" / "photo_review"
    if out_root.exists():
        shutil.rmtree(out_root, ignore_errors=True)
    out_root.mkdir(parents=True, exist_ok=True)
    log = []
    dirs = geom.edge_direction_shares(main)
    sat = next((p for p in (sat_imgs or []) if p.name == "satellite_annotated.jpg"), None)
    matched, entry0 = match_viewpoints(prj, provider, massing, photos, main, base_materials, st, out_root, sat_imgs)
    log.append(entry0)
    if matched:
        massing = dict(massing, views=matched)
    candidates = [massing]
    for r in range(1, rounds + 1):
        views = {}
        pairs = []
        for v in (massing.get("views") or [])[:4]:
            try:
                idx = int(v.get("photo", 0)) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= idx < len(photos) and f"photo{idx + 1}" not in views:
                views[f"photo{idx + 1}"] = cam_of(v, massing["parts"])
                pairs.append((photos[idx], f"photo{idx + 1}"))
        if not pairs:                       # no viewpoints given: look at the main frontage from both sides
            d0 = dirs[0][0] if dirs else 0.0
            for k, az in enumerate((d0 - 90, d0 + 90)[:len(photos)]):
                views[f"photo{k + 1}"] = {"azimuth_deg": az, "elevation_deg": 10}
                pairs.append((photos[k], f"photo{k + 1}"))
        mats = {k: dict(v) for k, v in base_materials.items()}
        mats.update(massing.get("materials") or {})
        cand = {"footprint": {"local": [list(p) for p in main]}, "parts": massing["parts"], "materials": mats,
                "budgets": st["budgets"]}
        rdir = out_root / f"round{r}"
        if sat is not None:
            from .research import satellite
            views["satplan"] = {"ortho_extent": list(satellite.EXTENT_M)}
        renders = {p.stem: p for p in engine_runner.preview(prj, cand, views, rdir)}
        sheets = [_side_by_side(ph, renders[name], out_root / f"round{r}_{name}.jpg", name)
                  for ph, name in pairs if name in renders]
        if sat is not None and "satplan" in renders:
            sheets.append(_side_by_side(sat, renders["satplan"], out_root / f"round{r}_satplan.jpg", "satellite"))
        entry = {"round": r, "images": [s.relative_to(prj.root).as_posix() for s in sheets]}
        if not sheets:
            entry["result"] = "no preview renders (Blender missing?) - review skipped"
            log.append(entry)
            break
        prompt = agent.REVIEW_PROMPT.format(
            name=st["name"], location=st["location"],
            massing=json.dumps({k: massing.get(k) for k in ("parts", "materials", "views")}, ensure_ascii=False),
            footprint=json.dumps([[round(x, 1), round(y, 1)] for x, y in main]),
            directions=", ".join(f"{d:g} ({s:.0%})" for d, s in dirs) or "none",
            satellite_note=("" if sat is None else " The LAST image compares the SATELLITE plan view (left, north up, "
                            "site outline in yellow, grid in metres) with YOUR MODEL seen from straight above "
                            "(right) at the same extent and scale: check positions, plan shapes, sizes and "
                            "orientations of every volume against it."))
        prompt += agent.user_directive(st)

        def review_check(o):
            scores = o.get("scores")
            if not isinstance(scores, dict) or not all(isinstance(scores.get(k), (int, float))
                                                       for k in REVIEW_ASPECTS):
                return [f'"scores" must give 1-5 for {", ".join(REVIEW_ASPECTS)}']
            low = [k for k in REVIEW_ASPECTS if scores[k] < 4]
            if o.get("verdict") == "match":
                return ([f'"match" is not allowed with scores below 4 ({", ".join(low)}): answer "revise" with the '
                         f'corrected model'] if low else [])
            if "parts" not in o:
                return ['verdict "revise" needs the complete corrected "parts"']
            return check(o)
        try:
            # structure / material errors are fixed inside the same round (retries), so every round is a real
            # photo comparison rather than a repair of the previous answer
            obj, res = ai.ask_json(provider, agent.MASSING_SYSTEM, prompt, validator=review_check,
                                   images=sheets, retries=3, repair=repair_answer)
        except Exception as exc:
            entry["result"] = f"review failed, kept the previous model: {exc}"[:400]
            log.append(entry)
            break
        entry["verdict"] = obj.get("verdict")
        entry["scores"] = {k: obj.get("scores", {}).get(k) for k in REVIEW_ASPECTS}
        entry["differences"] = obj.get("differences", [])[:12]
        log.append(entry)
        prj.log("photo review", {"round": r, "verdict": entry["verdict"], "differences": len(entry["differences"])})
        if obj.get("verdict") == "match":
            break
        # A-H matching is the starting point; a review that has seen photo | render side by side may correct a view
        # (the first draft can be wrong enough that two opposite sides look alike). Invalid views keep the old ones.
        new_views = []
        for v in obj.get("views") or []:
            try:
                nv = {"photo": int(v["photo"]), "azimuth_deg": float(v["azimuth_deg"]) % 360.0,
                      "elevation_deg": float(v.get("elevation_deg", 8.0))}
                old = next((m for m in (massing.get("views") or []) if m.get("photo") == nv["photo"]), {})
                if v.get("focus") or old.get("focus"):    # a close-up stays a close-up unless the review says so
                    nv["focus"] = v.get("focus") or old.get("focus")
                new_views.append(nv)
            except (TypeError, ValueError, KeyError):
                pass
        obj["views"] = new_views or massing.get("views") or matched
        keys = ("photo", "azimuth_deg", "elevation_deg")
        if new_views and matched and [{k: v[k] for k in keys} for v in new_views] != [{k: m[k] for k in keys}
                                                                                     for m in matched]:
            entry["views_changed"] = True
        massing = obj
        candidates.append(massing)
    if len(candidates) > 1 and not (log and log[-1].get("verdict") == "match"):
        massing, pick = select_best(prj, provider, candidates, photos, sat, main, base_materials, st, out_root)
        log.append(pick)
    return massing, log


def select_best(prj: Project, provider, candidates: list, photos: list, sat, main, base_materials: dict, st: dict,
                out_root: Path):
    """The last revision is not always the best and was never rendered: render every candidate (photo-1 view and
    top view) and let the AI choose. Falls back to the last candidate when anything fails."""
    from PIL import Image, ImageDraw
    from .engine import runner as engine_runner
    entry = {"round": len(candidates), "step": "select"}
    cands = candidates[-4:]                                # first draft may drop out when there are many rounds
    offset = len(candidates) - len(cands)
    sheets = []
    for k, m in enumerate(cands, start=1):
        v1 = next((v for v in (m.get("views") or []) if int(v.get("photo", 0)) == 1), None) or \
            {"azimuth_deg": 270.0, "elevation_deg": 10.0}
        views = {"photo1": cam_of(v1, m["parts"])}
        if sat is not None:
            from .research import satellite
            views["satplan"] = {"ortho_extent": list(satellite.EXTENT_M)}
        mats = {kk: dict(vv) for kk, vv in base_materials.items()}
        mats.update(m.get("materials") or {})
        cand = {"footprint": {"local": [list(p) for p in main]}, "parts": m["parts"], "materials": mats,
                "budgets": st["budgets"]}
        renders = {p.stem: p for p in engine_runner.preview(prj, cand, views, out_root / f"cand{k}")}
        if "photo1" not in renders:
            continue
        tiles = [Image.open(renders["photo1"]).convert("RGB")]
        if "satplan" in renders:
            tiles.append(Image.open(renders["satplan"]).convert("RGB"))
        h = 400
        tiles = [t.resize((int(t.width * h / t.height), h)) for t in tiles]
        sheet = Image.new("RGB", (sum(t.width for t in tiles) + 10 * (len(tiles) - 1), h + 40), "white")
        x = 0
        for t in tiles:
            sheet.paste(t, (x, 40))
            x += t.width + 10
        ImageDraw.Draw(sheet).text((10, 12), f"CANDIDATE {k}", fill="black")
        path = out_root / f"candidate{k}.jpg"
        sheet.save(path, "JPEG", quality=85)
        sheets.append((k, path))
    if len(sheets) < 2:
        entry["result"] = "not enough candidate renders; kept the last model"
        return candidates[-1], entry
    refs = [photos[0]] + ([sat] if sat is not None else [])
    prompt = agent.SELECT_PROMPT.format(name=st["name"], n=len(sheets),
                                        refs="photo 1" + (" and the satellite plan view" if sat is not None else ""))
    labels = {k for k, _ in sheets}
    try:
        obj, _ = ai.ask_json(provider, agent.MASSING_SYSTEM, prompt,
                             validator=lambda o: [] if int(o.get("best", -1)) in labels else ["best must be one of the candidates"],
                             images=refs + [p for _, p in sheets], retries=1)
    except Exception as exc:
        entry["result"] = f"selection failed, kept the last model: {exc}"[:300]
        return candidates[-1], entry
    best = int(obj["best"])
    entry.update({"verdict": f"chose candidate {best} of {len(sheets)}",
                  "differences": [f"why: {obj.get('why', '')}"[:400],
                                  f"candidate 1 = {'first draft' if offset == 0 else f'round {offset}'}; "
                                  f"candidate {len(cands)} = last revision"],
                  "images": [p.relative_to(prj.root).as_posix() for _, p in sheets]})
    prj.log("photo review: best candidate", {"best": best, "of": len(sheets)})
    return cands[best - 1], entry


def orientation_errors(parts, main) -> list[str]:
    dirs = geom.edge_directions(main)
    out = []
    for p in parts:
        fp = p.get("footprint")
        key = next((k for k in ("rect", "rounded_rect", "stadium") if isinstance(fp, dict) and k in fp), None)
        if key and dirs:
            rot = float(fp[key].get("rot_deg", 0.0))
            if min(geom.angle_gap(rot, d) for d in dirs) > 8.0:
                out.append(f"part {p.get('id')}: rot_deg {rot:g} follows no footprint edge; use one of "
                           f"{', '.join(f'{d:g}' for d in dirs)} (or +90)")
    return out


def normalise_glass(massing: dict) -> None:
    """Curtain-wall glass must reflect: metallic >= 0.85 and roughness <= 0.1 (photo colour kept)."""
    mats = massing.get("materials") or {}
    for p in massing.get("parts", []):
        if (p.get("facade") or {}).get("opening_type") == "curtain_wall":
            m = mats.get((p.get("material") or {}).get("wall", "wall"))
            if m is not None:
                m["metallic"] = max(float(m.get("metallic", 0.0) or 0.0), 0.85)
                m["roughness"] = min(float(m.get("roughness", 0.1) or 0.1), 0.1)


def facade_issues(massing: dict, main, notes: list | None = None) -> list[str]:
    """Deterministic facade check of every punched facade: any overlap between windows, pilasters and the entrance.
    Empty = consistent. The layout engine's own corrections (a window wider than its bay is narrowed to fit) are not
    errors - the built geometry is already right - and go to `notes` when given."""
    out = []
    for p in massing.get("parts", []):
        fac = p.get("facade") or {}
        if not fac or fac.get("opening_type") == "curtain_wall" or fac.get("openings") is False \
                or geom.is_tapered(p.get("taper")):
            continue
        try:
            poly = geom.footprint_polygon(p["footprint"], main)
        except Exception:
            continue
        lay = geom.facade_layout(poly, float(p["base_z"]), float(p["base_z"]) + float(p["height"]), fac)
        out += [f"part {p['id']}: {m}" for m in geom.check_facade(lay)]
        if notes is not None:
            notes += [f"part {p['id']}: {m}" for m in lay["issues"]]
    return out


def used_materials(massing: dict) -> set:
    slots = {"wall": "wall", "roof": "roof", "trim": "trim"}      # a part without a mapping uses these names
    used = {m for p in massing.get("parts", []) for m in {**slots, **(p.get("material") or {})}.values()}
    used |= {f.get("material", "trim") for p in massing.get("parts", []) for f in (p.get("features") or [])}
    return used


MIN_LEVEL_M = 1.6
# Words an AI uses for an opening the engine draws as one of its own types (it has no louver or shutter geometry).
OPENING_ALIASES = {"louver": "rect", "louvre": "rect", "louvers": "rect", "louvres": "rect", "louvered": "rect",
                   "louvred": "rect", "vent": "rect", "ventilation": "rect", "shutter": "rect", "shutters": "rect",
                   "jalousie": "rect", "grille": "rect", "screen": "rect", "window": "rect", "door": "rect",
                   "rectangular": "rect", "rect": "rect", "square": "square", "arch": "arch", "arched": "arch",
                   "arcade": "arch", "storefront": "storefront", "shopfront": "storefront", "blank": "none"}
ALIAS_PRIORITY = ("storefront", "arch", "square", "rect", "none")   # "arched_window" is an arch, not a plain window


def _opening_alias(rule: dict) -> dict:
    """Map an opening word by the words inside it, so variants ("horizontal_louver") need no entry of their own."""
    kind = rule.get("opening_type")
    if not isinstance(kind, str) or kind in ("curtain_wall", *spec_mod.OPENING_TYPES):
        return rule
    found = {OPENING_ALIASES[w] for w in re.split(r"[^a-z]+", kind.lower()) if w in OPENING_ALIASES}
    alias = next((t for t in ALIAS_PRIORITY if t in found), None)
    return dict(rule, opening_type=alias) if alias else rule


def _alias_wall(fac: dict) -> None:
    if isinstance(fac.get("ground"), dict):
        fac["ground"] = _opening_alias(fac["ground"])
    if isinstance(fac.get("floors"), list):
        fac["floors"] = [_opening_alias(r) if isinstance(r, dict) else r for r in fac["floors"]]


def _alias_side(side: dict) -> dict:
    side = dict(_opening_alias(side))
    _alias_wall(side)
    if isinstance(side.get("segments"), list):
        side["segments"] = [_alias_side(s) if isinstance(s, dict) else s for s in side["segments"]]
    return side


def sanitize_facades(massing: dict) -> dict:
    """Auto-correct facade values that only break the schema: opening words the engine has no geometry for become its
    nearest type (see OPENING_ALIASES), and on thin parts (a 0.6 m eave deck, a fountain basin) there are no openings
    below 2 m of wall and a storey is never lower than 1.6 m."""
    parts = []
    for p in massing.get("parts", []):
        fac = p.get("facade")
        if isinstance(fac, dict):
            fac = dict(_opening_alias(fac))
            _alias_wall(fac)
            if isinstance(fac.get("sides"), list):
                fac["sides"] = [_alias_side(s) if isinstance(s, dict) else s for s in fac["sides"]]
            try:
                lh = float(fac.get("level_height")) if fac.get("level_height") is not None else None
            except (TypeError, ValueError):
                lh = None
            if lh is not None and lh < MIN_LEVEL_M:
                fac["level_height"] = MIN_LEVEL_M
            if float(p.get("height", 0) or 0) < 2.0:
                fac["openings"] = False
            p = dict(p, facade=fac)
        parts.append(p)
    return dict(massing, parts=parts)


def repair_answer(answer: dict) -> dict:
    """Facade values the tool corrects itself in an AI answer, before it is checked: one thin part with a storey below
    the minimum would otherwise send the whole model back to the AI. An answer of the wrong shape is left to the check."""
    parts = answer.get("parts") if isinstance(answer, dict) else None
    if not isinstance(parts, list) or not all(isinstance(p, dict) for p in parts):
        return answer
    try:
        return sanitize_facades(answer)
    except (TypeError, ValueError):
        return answer


def prune_materials(massing: dict) -> dict:
    """Drop materials no part or feature uses (harmless leftovers of an AI answer, e.g. a colour noted for openings)."""
    used = used_materials(massing) | {"opening"}
    mats = {k: v for k, v in (massing.get("materials") or {}).items() if k in used}
    return dict(massing, materials=mats)


def facade_pass(prj: Project, provider, massing: dict, photos: list, check, st: dict, feedback=None, sheets=None,
                main=None):
    """A facade-only AI pass: per-floor facades and visible details (features) for every part, from the photos
    (and, in review rounds, from photo | model comparison sheets plus the judge's list of errors). Geometry
    (footprints, heights, roofs) is kept exactly; an invalid or inconsistent answer leaves the model unchanged."""
    entry = {"step": "facade", "round": None}
    prompt = agent.FACADE_PROMPT.format(
        name=st["name"], location=st["location"],
        massing=json.dumps({"parts": massing["parts"], "materials": massing.get("materials", {})}, ensure_ascii=False))
    if feedback:
        prompt += ("\n\nREVIEW ROUND. The first attached images compare the photos (left) with the current model "
                   "(right) from the same viewpoints. An independent reviewer found these errors - fix the facade "
                   "ones; keep what already matches:\n- " + "\n- ".join(str(e) for e in feedback))
    if main:
        from . import exposure
        prompt += ("\n\nEXPOSED WALLS (measured: the share of each wall not buried inside another part). Put every "
                   "facing feature (rose windows, clocks, portals, pediments, loggias, entrances) on an exposed wall "
                   "of its part; a feature on a wall that is more than half hidden is rejected:\n"
                   + exposure.exposure_text(massing, main))
    prompt += ("\n\nFacade logic (checked automatically - inconsistent answers are rejected): every facade, or every "
               "segment of one, gets ONE bay "
               "grid (bay_spacing; the length is divided into whole bays, symmetric about its centre). Pilasters "
               "stand on the bay lines; each window sits in the middle of a bay, so opening_width must be smaller than "
               "bay_spacing minus the pilaster width minus 0.3 m. A floor may use every 2nd or 3rd bay (bay_spacing = 2x "
               "or 3x the facade's), never a different rhythm. The entrance takes the central bay(s).")
    prompt += agent.user_directive(st)

    def merged(o):
        by_id = {p.get("id"): p for p in o.get("parts", []) if isinstance(p, dict)}
        parts = []
        for p in massing["parts"]:
            q = by_id.get(p["id"])
            parts.append(dict(p, **{k: q[k] for k in ("facade", "features") if q and k in q}))
        mats = dict(massing.get("materials") or {})
        mats.update(o.get("materials") or {})
        return sanitize_facades(prune_materials(dict(massing, parts=parts, materials=mats)))

    try:
        obj, _ = ai.ask_json(provider, agent.MASSING_SYSTEM, prompt,
                             validator=lambda o: check(merged(o)) + (facade_issues(merged(o), main)
                                                                     + _fixable_feature_issues(merged(o), main)
                                                                     if main else []),
                             images=list(sheets or [])[:4] + photos[:4], retries=2)
    except Exception as exc:
        entry["result"] = f"facade pass failed, kept the facades: {exc}"[:300]
        return massing, entry
    out = merged(obj)
    fixed = []
    facade_issues(out, main, fixed) if main else None
    n_feat = sum(len(p.get("features") or []) for p in out["parts"])
    entry.update({"verdict": f"facades refined, {n_feat} detail features", "differences": obj.get("notes", [])[:8]})
    if fixed:
        entry["layout_fixes"] = fixed[:12]
    prj.log("facade pass", {"features": n_feat})
    return out, entry


def _fixable_feature_issues(massing: dict, main) -> list[str]:
    from . import exposure
    return exposure.feature_issues(massing["parts"], main, fixable_only=True)


def score_pair(q) -> tuple:
    """(overall, facade) of a judge result."""
    return (q or {}).get("overall"), ((q or {}).get("scores") or {}).get("facade", 0)


def not_worse(before, after) -> bool:
    """The judge's test for keeping a change: overall higher, or equal with the facade score not lower."""
    b_all, b_fac = score_pair(before)
    a_all, a_fac = score_pair(after)
    return a_all is not None and (a_all > b_all or (a_all == b_all and a_fac >= b_fac))


def keep_better_parts(prj: Project, provider, massing: dict, refined: dict, before: dict, after: dict, st: dict):
    """A facade round scored lower as a whole: ask, from before/after comparison sheets, which parts it improved.
    Returns (model with only those parts changed, their ids), or (None, []) when no part qualifies."""
    changed = [p["id"] for p, q in zip(massing["parts"], refined["parts"])
               if (p.get("facade"), p.get("features")) != (q.get("facade"), q.get("features"))]
    sheets = [prj.root / i for i in (before.get("images") or []) + (after.get("images") or [])]
    if len(changed) < 2 or not sheets:
        return None, []

    def check(o):
        if not isinstance(o.get("keep"), list) or not all(i in changed for i in o["keep"]):
            return [f'"keep" must be a list of part ids from: {", ".join(changed)}']
        return []
    try:
        obj, _ = ai.ask_json(provider, agent.MASSING_SYSTEM, agent.FACADE_PARTS_PROMPT.format(
            name=st["name"], n=len(before.get("images") or []), parts=", ".join(changed)),
            validator=check, images=sheets, retries=1)
    except Exception as exc:
        prj.log("facade part choice failed", {"error": str(exc)[:200]})
        return None, []
    kept = [i for i in changed if i in obj["keep"]]
    if not kept or len(kept) == len(changed):
        return None, []
    new = {p["id"]: p for p in refined["parts"]}
    parts = [new[p["id"]] if p["id"] in kept else p for p in massing["parts"]]
    return sanitize_facades(prune_materials(dict(refined, parts=parts))), kept


def judge(prj: Project, provider, massing: dict, photos: list, sat_imgs: list, main, base_materials: dict, st: dict,
          out_name: str = "judge"):
    """Independent AI score of a model (it cannot edit anything): photo | render pairs from up to three photo
    viewpoints plus satellite | top view. The score is the mean of several independent readings, because one reading
    moves by a point or more on the same model. Stored in spec["quality"]; never blocks."""
    from .engine import runner as engine_runner
    from .research import satellite
    out_root = prj.root / "reports" / out_name
    views, pairs = {}, []
    for v in (massing.get("views") or [])[:3]:
        try:
            n = int(v["photo"])
        except (TypeError, ValueError, KeyError):
            continue
        if 1 <= n <= len(photos):
            views[f"photo{n}"] = cam_of(v, massing["parts"], 8.0)
            pairs.append((photos[n - 1], f"photo{n}"))
    sat = next((p for p in (sat_imgs or []) if p.name == "satellite_annotated.jpg"), None)
    if sat is not None:
        views["satplan"] = {"ortho_extent": list(satellite.EXTENT_M)}
    mats = {k: dict(v) for k, v in base_materials.items()}
    mats.update(massing.get("materials") or {})
    cand = {"footprint": {"local": [list(p) for p in main]}, "parts": massing["parts"], "materials": mats,
            "budgets": st["budgets"]}
    renders = {p.stem: p for p in engine_runner.preview(prj, cand, views, out_root)}
    sheets = [_side_by_side(ph, renders[name], out_root / f"judge_{name}.jpg", name) for ph, name in pairs
              if name in renders]
    if sat is not None and "satplan" in renders:
        sheets.append(_side_by_side(sat, renders["satplan"], out_root / "judge_satplan.jpg", "satellite"))
    if not sheets:
        return {"result": "no renders to judge"}

    def check(o):
        sc = o.get("scores") or {}
        if not all(isinstance(sc.get(k), (int, float)) for k in REVIEW_ASPECTS):
            return ["scores for every aspect are required"]
        return [] if isinstance(o.get("overall"), (int, float)) else ["overall is required"]
    readings, res = [], None
    for _ in range(max(1, int(settings_mod.load().get("judge_samples", 3) or 1))):
        try:
            obj, res = ai.ask_json(provider, agent.MASSING_SYSTEM, agent.JUDGE_PROMPT.format(name=st["name"]),
                                   validator=check, images=sheets, retries=1)
            readings.append(obj)
        except Exception as exc:
            if not readings:
                return {"result": f"judge failed: {exc}"[:300]}
    mean = lambda vals: round(sum(vals) / len(vals), 2)
    errors = list(dict.fromkeys(str(e)[:240] for r in readings for e in r.get("errors", [])))
    q = {"scores": {k: mean([r["scores"][k] for r in readings]) for k in REVIEW_ASPECTS},
         "overall": mean([r["overall"] for r in readings]), "readings": [r["overall"] for r in readings],
         "errors": errors[:5], "model": res.model, "images": [s.relative_to(prj.root).as_posix() for s in sheets]}
    prj.log("quality judged", {"overall": q["overall"], "readings": q["readings"], "scores": q["scores"]})
    return q


def material_errors(massing: dict, max_materials: int | None = None) -> list[str]:
    """Parts must use the materials the AI defined (not silently fall back to the defaults), and vice versa;
    with max_materials, the number of materials the model uses (opening included) must fit the budget."""
    defined = set(massing.get("materials") or {})
    used = used_materials(massing)
    errs = [f"part material '{m}' is not defined in materials" for m in sorted(used - defined - set(spec_mod.DEFAULT_MATERIALS))]
    errs += [f"material '{m}' is defined but no part uses it (assign it to the parts it belongs to)"
             for m in sorted(defined - used - {"opening"})]
    if max_materials and len(used | {"opening"}) > max_materials:
        errs.append(f"{len(used | {'opening'})} materials used, the limit is {max_materials} (opening included): "
                    f"reuse existing materials for details")
    return errs


def spec_blocking_issues(prj: Project) -> list[dict]:
    sp = prj.read_json("spec/landmark_spec.json")
    if not sp:
        return []
    facts = merged_facts(prj)
    issues = spec_mod.check_structure(sp["parts"], [tuple(p) for p in sp["footprint"]["local"]], facts)
    issues += [i for i in sp.get("issues", []) if i.get("code") == "ai_failed"]
    issues += unknown_material_issues(sp)
    return [i for i in issues if i["severity"] == "error"]


def review_issues(spec: dict) -> list[dict]:
    """Issues shown with the plan at A2 and A3 besides the structure check: unknown materials (blocking) and generated
    components (marked as generated, never observed)."""
    from . import generative, exposure
    hidden = []
    try:
        hidden = [{"severity": "warning", "part": m.split(":")[0].replace("part ", ""), "code": "hidden_feature",
                   "message": m} for m in exposure.feature_issues(spec, spec["footprint"]["local"])]
    except (KeyError, TypeError, ValueError):
        pass
    return unknown_material_issues(spec) + generative.review_items(spec) + hidden


def unknown_material_issues(spec: dict) -> list[dict]:
    """A part or feature that names a material the spec does not define would be drawn with a fallback colour by the
    builder. That is a blocking issue, not a silent fallback (plan M2 geometry requirements)."""
    defined = set(spec.get("materials") or {}) | set(spec_mod.DEFAULT_MATERIALS)
    out = []
    for m in sorted(used_materials(spec) - defined):
        out.append({"code": "unknown_material", "severity": "error",
                    "message": f"material '{m}' is used by a part but not defined; define it or map the part to an "
                               f"existing material"})
    return out


def spec_mod_parse(v):
    from .research.sources import parse_height
    return parse_height(v)


# ----------------------------------------------------------------------------- release
def release(prj: Project) -> dict:
    check_release_identity(prj)
    res = prj.read_json("reports/build_result.json", {})
    files = ["output/model.glb", "blender/model.blend", "spec/landmark_spec.json", "input/sources.json",
             "input/research.json", "reports/build_result.json"] + [f"renders/{r}" for r in res.get("renders", [])]
    sums = []
    for f in files:
        p = prj.root / f
        if p.exists():
            sums.append(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {f}")
    (prj.path("reports", "checksums.sha256")).write_text("\n".join(sums) + "\n", encoding="utf-8")
    from . import textures as textures_mod
    srcs = {s["id"]: s for s in prj.read_json("input/sources.json", {"sources": []})["sources"]}
    attributions = []
    for t in textures_mod.load(prj)["textures"]:
        s = srcs.get(t.get("source_id") or "", {})
        use = f"repeat on '{t['material']}'" if t["mode"] == "repeat" else f"panel on {t['part']} ({t['side']})"
        attributions.append(f"- {s.get('title') or t['file']}: {t.get('author') or s.get('author', '')} "
                            f"({s.get('licence_short') or t.get('licence')}) {s.get('url', '')} - {use}")
    st = prj.state()
    notes = [f"# {st['name']} - release notes", "", f"Date: {time.strftime('%Y-%m-%d')}",
             f"Triangles: {res.get('triangles')}, GLB bytes: {res.get('bytes')}, materials: {res.get('materials')}",
             "", "## Approvals"] + [f"- {a['phase']}: {a['decision']} {a.get('notes', '')}" for a in st.get("approvals", [])]
    if attributions:
        notes += ["", "## Texture attributions"] + attributions
    gen = [t for t in textures_mod.load(prj)["textures"] if t.get("licence") == "ai_generated"]
    if gen:
        tex_res = (prj.read_json("reports/autotex/notes.json", {}) or {}).get("result", {})
        notes += ["", "## Generated content (AI-drawn, not photographs)",
                  f"- {sum(1 for t in gen if t['mode'] == 'panel')} facade textures on "
                  f"{len({t.get('part') for t in gen if t['mode'] == 'panel'})} parts and "
                  f"{sum(1 for t in gen if t['mode'] == 'repeat')} material tiles were drawn by Codex image "
                  f"generation in the style of the licensed reference photos, reviewed at A2.5.",
                  *[f"- generation failed for {f['part']}: {f['why']}" for f in tex_res.get("generation_failed", [])]]
    refs = [s for s in srcs.values() if s.get("kind") == "satellite" or s.get("use") == "reference_only"]
    if refs:
        notes += ["", "## Reference imagery (not used as texture)"] + [
            f"- {s.get('title') or s.get('path')}: {s.get('author', '')} ({s.get('licence', '')}) {s.get('url', '')}"
            for s in refs]
    sp = prj.read_json("spec/landmark_spec.json", {})
    if sp.get("defaults_used"):
        notes += ["", "## Values from module defaults (not sourced)"] + [f"- {d['key']}: {d['value']}" for d in sp["defaults_used"]]
    prj.path("reports", "release_notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    return {"checksums": len(sums)}


# ----------------------------------------------------------------------------- runner
def run_phase(prj: Project, phase: str) -> str:
    """Execute one automatic phase, recording its AI calls in logs/ai_calls.jsonl. Returns the new status."""
    calls = ai.CallLog(prj.path("logs", "ai_calls.jsonl"), phase)
    with ai.logging_calls(calls):
        status = _run_phase(prj, phase)
    if calls.entries:
        prj.log("ai usage", calls.summary())
    return status


def _run_phase(prj: Project, phase: str) -> str:
    prj.set_phase(phase, "running")
    try:
        if phase == "setup":
            prj.set_phase(phase, "done")
        elif phase == "doctor":
            rep = doctor.run_all()
            prj.write_json("reports/doctor.json", rep)
            if rep["ok"]:
                prj.set_phase(phase, "done", "required checks pass")
            else:
                bad = [c["id"] for c in rep["checks"] if not c["ok"] and not c.get("optional")]
                prj.set_phase(phase, "failed", "missing: " + ", ".join(bad))
        elif phase == "research":
            rep = agent.run(prj)
            ok = sum(1 for s in rep["steps"] if s.get("ok"))
            prj.set_phase(phase, "done", f"{ok}/{len(rep['steps'])} research steps succeeded; "
                                         f"{len(rep['warnings'])} warnings")
        elif phase == "completion":
            items = missing_items(prj)
            prj.update_state(lambda s: s.update(missing=items))
            if items:
                prj.set_phase(phase, "blocked", f"{len(items)} item(s) needed from the user")
            else:
                prj.set_phase(phase, "done", "input gate READY")
        elif phase == "spec":
            sp = build_spec(prj)
            errs = [i for i in sp.get("issues", []) if i["severity"] == "error"]
            prj.set_phase(phase, "done", f"{len(sp['parts'])} parts, ~{sp['estimated_triangles']} triangles before "
                                         f"openings; {len(sp['defaults_used'])} defaults used"
                                         + (f"; {len(errs)} STRUCTURE ERROR(S): fix in A2 (edit spec or request changes)"
                                            if errs else ""))
        elif phase == "textures":
            # Generated elevations first (Codex), photo crops only for faces still bare (needs the vision AI).
            # Reviewed at A2.5; the build itself makes no AI call.
            if not settings_mod.load().get("auto_textures", True):
                prj.set_phase(phase, "done", "automatic textures are off in settings")
            else:
                from . import autotex
                res = autotex.run_v2(prj, ai.get("vision"))
                prj.set_phase(phase, "done", autotex.summary_line(res))
        elif phase == "build":
            prj.path("reports", "build_manifest.json").unlink(missing_ok=True)   # a new build replaces the old manifest
            res = engine_runner.build(prj)
            if res.get("ok"):
                prj.set_phase(phase, "done", f"{res['triangles']} triangles, {res['bytes']} bytes, "
                                             f"{len(res.get('renders', []))} renders")
                write_build_manifest(prj)
                write_quality(prj, res)
            else:
                prj.set_phase(phase, "failed", "; ".join(res.get("errors", ["build failed"]))[:500])
        elif phase == "release":
            r = release(prj)
            prj.set_phase(phase, "done", f"{r['checksums']} checksummed files")
        else:
            prj.set_phase(phase, "waiting_approval")
    except Exception as exc:
        prj.log("phase error", {"phase": phase, "trace": traceback.format_exc()[-2000:]})
        prj.set_phase(phase, "failed", f"{type(exc).__name__}: {exc}"[:500])
    return prj.state()["phases"][phase]["status"]


def run_until_blocked(prj: Project) -> dict:
    """Run automatic phases in order until an approval, a block (missing data) or a failure. Only one runner works on
    a project at a time; a second request returns reason "busy" and changes nothing."""
    with prj.run_lock(blocking=False) as got:
        if not got:
            return {"stopped_at": None, "reason": "busy"}
        return _run_until_blocked(prj)


def _run_until_blocked(prj: Project) -> dict:
    for p in PHASES:
        pid = p["id"]
        status = prj.state()["phases"][pid]["status"]
        if status in ("done", "approved"):
            continue
        if p["approval"]:
            if status != "waiting_approval":
                prj.set_phase(pid, "waiting_approval")
            return {"stopped_at": pid, "reason": "approval"}
        status = run_phase(prj, pid)
        if status != "done":
            return {"stopped_at": pid, "reason": status}
    return {"stopped_at": None, "reason": "complete"}


class SpecIssues(ValueError):
    """The plan has structural errors (floating parts, parts outside the footprint): approval refused."""

    def __init__(self, issues):
        self.issues = issues
        super().__init__("; ".join(i["message"] for i in issues))


class ApprovalError(ValueError):
    """The phase is not waiting for approval, or the phase or decision is not allowed. CLI and API share this rule."""


class ReleaseBlocked(RuntimeError):
    """Packaging refused: no build manifest, or the inputs changed since the build."""


ALLOWED_DECISIONS = ("approved", "changes_requested")
# Versions recorded in every build manifest. Bump COMPONENT_LIBRARY_VERSION with any change to a generator in geom.py.
COMPONENT_LIBRARY_VERSION = detail_registry.LIBRARY_VERSION
ALLOCATOR_VERSION = "none-v1"        # M2 adds the complexity allocator; until then no candidates are selected


def invalidate_from(prj: Project, phase: str, reason: str) -> None:
    """A phase's inputs changed: it and every later phase go back to pending, so their results and approvals are
    no longer trusted. The approval history is kept as it was."""
    ids = [p["id"] for p in PHASES]
    for pid in ids[ids.index(phase):]:
        if prj.state()["phases"][pid]["status"] != "pending":
            prj.set_phase(pid, "pending", reason)


def _digest(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:20]


def _file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:20] if path.is_file() else "absent"


_blender_version_cache: dict = {}


def _blender_version() -> str:
    if "v" not in _blender_version_cache:
        exe = doctor.find_blender()
        text = "none"
        if exe:
            try:
                import subprocess
                p = subprocess.run([exe, "-b", "--factory-startup", "--version"], stdin=subprocess.DEVNULL,
                                   capture_output=True, text=True, timeout=120, errors="replace")
                text = next((ln.strip() for ln in (p.stdout or "").splitlines() if ln.startswith("Blender")), "unknown")
            except (OSError, subprocess.SubprocessError):
                text = "unknown"
        _blender_version_cache["v"] = text
    return _blender_version_cache["v"]


def build_identity(prj: Project) -> dict:
    """Digests of every input a build depends on (docs/PRODUCTION_BASELINE.md 4.2). A build and its release must agree."""
    st = prj.state()
    effective = {k: st.get(k) for k in ("name", "location", "lat", "lon", "notes", "advanced_prompt", "budgets")}
    photos = {}
    for s in prj.read_json("input/sources.json", {"sources": []})["sources"]:
        if s.get("kind") == "photo" and s.get("path"):
            photos[s["id"]] = _file_digest(prj.root / s["path"])
    return {
        "state": _digest(effective),
        "spec": _file_digest(prj.root / "spec" / "landmark_spec.json"),
        "sources": _file_digest(prj.root / "input" / "sources.json"),
        "photos": _digest(photos),
        "textures": _file_digest(prj.root / "input" / "textures.json"),
        "component_library": _digest({"version": COMPONENT_LIBRARY_VERSION}),
        "allocator": _digest({"version": ALLOCATOR_VERSION}),
        "blender_script": _file_digest(Path(__file__).with_name("engine") / "blender_build.py"),
        "blender": _digest({"version": _blender_version()}),
        "detail_engine": _digest(detail_engine.engine_settings(settings_mod.load().get("detail_engine_v2", False))),
    }


def write_quality(prj: Project, result: dict) -> dict:
    """Deterministic metrics of a finished build, their inputs and version, the hard gate and the baseline thresholds
    (plan M6). Written next to the build result so a later build can be compared with it."""
    from . import quality
    sp = prj.read_json("spec/landmark_spec.json", {}) or {}
    m = quality.metrics(result, sp)
    try:
        from . import facade_grid, observe as observe_mod
        obs = (observe_mod.load(prj) or {}).get("observations", [])
        align = facade_grid.alignment(sp, obs) if obs else None
    except (OSError, ValueError, KeyError, TypeError):
        align = None
    m["floor_line_match"] = align["floor_line_match"] if align else None
    m["bay_line_match"] = align["bay_line_match"] if align else None
    rep = {"metrics": m, "gate": quality.gate(m, sp), "baseline": quality.evaluate(m),
           "grid_alignment": align,
           "digest": quality.score_digest(m)}
    prj.write_json("reports/quality.json", rep)
    return rep


def quality_blocking_issues(prj: Project) -> list[dict]:
    """With the detail engine on, the deterministic gate is the primary keep/reject signal: its problems block the
    build approval. With the flag off it is reported only, so version 1 projects keep their workflow."""
    if not settings_mod.load().get("detail_engine_v2", False):
        return []
    rep = prj.read_json("reports/quality.json") or {}
    return [{"code": "quality_gate", "severity": "error", "message": p} for p in rep.get("gate", [])]


def write_build_manifest(prj: Project) -> dict:
    manifest = {"identity": build_identity(prj), "written": time.strftime("%Y-%m-%dT%H:%M:%S")}
    prj.write_json("reports/build_manifest.json", manifest)
    return manifest


def check_release_identity(prj: Project) -> None:
    """Refuse to package unless the build manifest describes exactly the inputs on disk now."""
    manifest = prj.read_json("reports/build_manifest.json")
    if not manifest:
        raise ReleaseBlocked("no build manifest: the build must finish before release")
    now, then = build_identity(prj), manifest.get("identity", {})
    changed = sorted(k for k in set(now) | set(then) if now.get(k) != then.get(k))
    if changed:
        raise ReleaseBlocked("stale build: changed since the build: " + ", ".join(changed) + "; rebuild before release")


def approve(prj: Project, phase: str, decision: str, notes: str = "") -> dict:
    ids = [p["id"] for p in PHASES]
    if phase not in ids:
        raise ApprovalError(f"unknown phase {phase!r}")
    if decision not in ALLOWED_DECISIONS:
        raise ApprovalError("decision must be approved or changes_requested")
    status = prj.state()["phases"][phase]["status"]
    if status != "waiting_approval":
        raise ApprovalError(f"{phase} is {status}, not waiting for approval")
    if decision == "approved" and phase in ("spec_review", "build_review"):
        blocking = spec_blocking_issues(prj)
        if phase == "build_review":
            blocking += quality_blocking_issues(prj)
        if blocking:
            raise SpecIssues(blocking)
    prj.update_state(lambda s: s["approvals"].append({"phase": phase, "decision": decision, "notes": notes,
                                                      "t": time.strftime("%Y-%m-%dT%H:%M:%S")}))
    if decision == "approved":
        prj.set_phase(phase, "approved", notes)
        return {"ok": True}
    target = REWIND[phase]
    ids = [p["id"] for p in PHASES]
    for pid in ids[ids.index(target):]:
        prj.set_phase(pid, "pending", f"reset by change request on {phase}" if pid != phase else notes)
    return {"ok": True, "rewound_to": target}


def change_requests(st: dict) -> list[str]:
    """Reviewer notes that still apply: those given since the last restart from scratch."""
    approvals = st.get("approvals", [])
    last = max((i for i, a in enumerate(approvals) if a.get("decision") == "restart"), default=-1)
    return [a.get("notes", "") for a in approvals[last + 1:] if a.get("decision") == "changes_requested"]


def _record_decision(prj: Project, phase: str, decision: str, notes: str) -> None:
    prj.update_state(lambda s: s["approvals"].append({"phase": phase, "decision": decision, "notes": notes,
                                                      "t": time.strftime("%Y-%m-%dT%H:%M:%S")}))


def reopen(prj: Project, phase: str, notes: str = "") -> dict:
    """Go back to an approval stage (A1, A2, A3, A6) that was already reached, to review and adjust it. Its results are
    kept; only the later phases go back to pending and run again after the stage is approved."""
    ids = [p["id"] for p in PHASES]
    stage = next((p for p in PHASES if p["id"] == phase and p["approval"]), None)
    if stage is None:
        raise ValueError("only approval stages can be reopened")
    index = ids.index(phase)
    if prj.state()["phases"][ids[index - 1]]["status"] != "done":
        raise ValueError(f"{stage['title']} has not been reached yet")
    _record_decision(prj, phase, "reopened", notes)
    prj.set_phase(phase, "waiting_approval", notes or "reopened by user")
    for pid in ids[index + 1:]:
        prj.set_phase(pid, "pending", f"reset: {phase} was reopened")
    return {"ok": True, "reopened": phase}


RESTART_PHASES = ("research", "spec")


def restart(prj: Project, notes: str = "", advanced_prompt: str = "", start: str = "research") -> dict:
    """Run again from research (or from the massing spec) with the given note and advanced prompt. They replace the
    earlier ones, and earlier change requests no longer apply. Files the user added are kept."""
    if start not in RESTART_PHASES:
        raise ValueError(f"start must be one of {', '.join(RESTART_PHASES)}")
    prj.update_state(lambda s: s.update(notes=notes.strip(), advanced_prompt=advanced_prompt.strip()))
    _record_decision(prj, start, "restart", notes.strip())
    ids = [p["id"] for p in PHASES]
    for pid in ids[ids.index(start):]:
        prj.set_phase(pid, "pending", "restarted by user")
    return {"ok": True, "start": start}


def submit_inputs(prj: Project, values: dict) -> list:
    """Store user answers; footprint by OSM id is fetched immediately."""
    from .research import sources
    vals = dict(values)
    if "latlon" in vals and vals["latlon"]:
        lat, lon = [float(x) for x in re.split(r"[,\s]+", str(vals.pop("latlon")).strip())[:2]]
        prj.update_state(lambda s: s.update(lat=lat, lon=lon))
    if vals.get("footprint_confirmed") == "no":
        vals.pop("footprint_confirmed")
        p = prj.root / "input" / "geo" / "footprint.geojson"
        if p.exists():
            p.unlink()                      # the gate now asks for the correct OSM id
    fp = vals.pop("footprint", None)
    if fp:
        m = re.match(r"(way|relation)\D*(\d+)", str(fp).strip())
        if not m:
            raise ValueError("footprint must look like way/123 or relation/456")
        data = sources.osm_element_full(m.group(1), int(m.group(2)))
        nodes = {e["id"]: e for e in data["elements"] if e["type"] == "node"}
        way = next(e for e in data["elements"] if e["type"] == "way")
        ring = [(nodes[n]["lon"], nodes[n]["lat"]) for n in way["nodes"] if n in nodes]
        prj.write_json("input/geo/footprint.geojson", {"type": "FeatureCollection", "features": [{
            "type": "Feature", "properties": dict(way.get("tags", {}), osm_type=m.group(1), osm_id=int(m.group(2)),
                                                  l3d_match_method="user_osm_id", l3d_match_score=1.0),
            "geometry": {"type": "Polygon", "coordinates": [ring]}}]})
    prj.update_state(lambda s: s["user_inputs"].update({k: v for k, v in vals.items() if v not in (None, "")}))
    items = missing_items(prj)
    prj.update_state(lambda s: s.update(missing=items))
    if not items and prj.state()["phases"]["completion"]["status"] == "blocked":
        prj.set_phase("completion", "pending", "user data received")
    return items


def refit_plan(prj: Project) -> dict:
    """Fit the current spec's volumes to the measured footprint (plan_fit), save it, and send the plan back to A2
    for approval when anything changed. Returns the fit report."""
    from . import plan_fit
    spec = prj.read_json("spec/landmark_spec.json")
    if not spec:
        raise RuntimeError("no spec")
    main = [tuple(p) for p in spec["footprint"]["local"]]
    fitted, rep = plan_fit.fit(spec["parts"], main, lambda ps: spec_mod.structure_errors(ps, main))
    if rep["iou_after"] > rep["iou_before"]:
        spec["parts"] = fitted
        spec["plan_fit"] = rep
        prj.write_json("spec/landmark_spec.json", spec)
        invalidate_from(prj, "spec_review", f"plan fitted to the footprint: IoU {rep['iou_before']} -> "
                                            f"{rep['iou_after']}; approve the plan again")
    prj.log("plan fitted to the footprint", rep)
    return rep
