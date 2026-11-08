"""End-to-end smoke run without AI: create a project, run to each stop, answer gates, approve, build."""
import json
import os
import sys

os.environ.setdefault("LANDMARK3D_HOME", os.path.join(os.environ.get("TEMP", "/tmp"), "l3d_e2e_home"))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from landmark3d import pipeline, settings  # noqa: E402
from landmark3d.project import Project  # noqa: E402

settings.update({"projects_dir": os.path.join(os.environ["LANDMARK3D_HOME"], "projects"),
                 "contact": os.environ.get("L3D_TEST_CONTACT", "https://example.org/landmark3d-local-test")})
name = sys.argv[1] if len(sys.argv) > 1 else "Ben Thanh Market"
loc = sys.argv[2] if len(sys.argv) > 2 else "Ho Chi Minh City, Vietnam"
prj = Project.create(name, loc)
print("project", prj.id, prj.root)
for step in range(12):
    r = pipeline.run_until_blocked(prj)
    st = prj.state()
    print("STOP", r, st["phases"][r["stopped_at"]]["message"] if r["stopped_at"] else "")
    if r["reason"] == "complete":
        break
    if r["reason"] == "approval":
        pipeline.approve(prj, r["stopped_at"], "approved", "e2e auto-approve")
    elif r["reason"] == "blocked":
        print("MISSING", json.dumps(st["missing"], ensure_ascii=False)[:600])
        answers = {}
        for it in st["missing"]:
            if it["id"] == "main_wall_height_m":
                answers[it["id"]] = 8.0
            elif it["id"] == "roof_form":
                answers[it["id"]] = it.get("default", "hip")
        left = pipeline.submit_inputs(prj, answers)
        if left:
            print("STILL MISSING", [i["id"] for i in left])
            break
    else:
        print("FAILED", st["phases"][r["stopped_at"]])
        break
print(json.dumps(prj.read_json("reports/build_result.json", {}), indent=1)[:900])
