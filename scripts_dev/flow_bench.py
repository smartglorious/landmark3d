"""P12 benchmark of the restructured flow on dev projects: texture phase (generation first) -> A2.5 approved -> build
(no AI). Records per project: texture and build time, Codex and Claude calls, generated faces / tiles, failures,
bare faces, T0 texture metrics and M6 quality. Specs are reused as they are (the spec phase is not rerun).

    python scripts_dev/flow_bench.py <project-id> ... > reports/benchmark/flow_bench_<date>.json
"""
import json
import sys
import time

from landmark3d import pipeline
from landmark3d.project import Project
from landmark3d.smarttex import metrics


def calls_since(prj, t0):
    out = {}
    p = prj.root / "logs" / "ai_calls.jsonl"
    if not p.is_file():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("t", "") >= t0:
            s = out.setdefault(e["step"], {"calls": 0, "seconds": 0.0})
            s["calls"] += 1
            s["seconds"] = round(s["seconds"] + float(e.get("seconds", 0)), 1)
    return out


def bench(pid: str) -> dict:
    prj = Project(pid)
    t0 = time.strftime("%Y-%m-%dT%H:%M:%S")
    row = {"project": pid}
    pipeline.invalidate_from(prj, "textures", "flow benchmark")    # cached images are reused; new steps run
    t = time.time()
    pipeline.run_until_blocked(prj)
    row["texture_s"] = round(time.time() - t)
    row["texture_message"] = prj.state()["phases"]["textures"]["message"]
    res = (prj.read_json("reports/autotex/notes.json", {}) or {}).get("result", {})
    row["texture"] = {k: res.get(k) for k in ("generated_faces", "projected_faces", "bay_faces", "unique_faces",
                                              "swatches", "generated_tiles", "bare_faces", "generation_failed",
                                              "crop_fallback")}
    pipeline.approve(prj, "texture_review", "approved", "flow benchmark")
    t = time.time()
    out = pipeline.run_until_blocked(prj)
    row["build_s"] = round(time.time() - t)
    row["stopped_at"] = out
    b = prj.read_json("reports/build_result.json", {}) or {}
    row["build"] = {k: b.get(k) for k in ("ok", "triangles", "bytes", "skinned_faces", "panels", "openings")}
    q = prj.read_json("reports/quality.json", {}) or {}
    row["m6"] = {k: (q.get("metrics") or {}).get(k) for k in ("plan_iou", "facade_coverage", "texel_warnings",
                                                             "byte_ratio", "triangle_ratio")}
    row["m6_gate"] = q.get("gate")
    try:
        row["t0"] = metrics.texture_report(prj).get("summary")
    except Exception as exc:
        row["t0"] = f"failed: {exc}"[:200]
    row["ai_calls"] = calls_since(prj, t0)
    return row


if __name__ == "__main__":
    rows = []
    for pid in sys.argv[1:]:
        rows.append(bench(pid))
        print(json.dumps(rows[-1], ensure_ascii=False), file=sys.stderr, flush=True)
    print(json.dumps(rows, ensure_ascii=False, indent=1))
