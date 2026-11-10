"""Parallel photo observations (throughput): same calls, shorter wall time, order and failures kept. No AI calls."""
import time
from types import SimpleNamespace

from landmark3d import observe
from landmark3d.research import ai


def _sources(n):
    return {f"SRC-{i}": {"id": f"SRC-{i}", "kind": "photo", "path": f"input/photos/p{i}.jpg"} for i in range(n)}


def _doc(sid, part):
    return {"observations": [{"id": "OBS-1", "part_id": part, "confidence": 0.9, "floor_lines": [0, 0.5, 1],
                              "bay_lines": [0, 0.5, 1], "elements": []}]}


def test_three_photos_run_together_not_one_after_another(tmp_path, monkeypatch):
    def slow(provider, name, part_id, sid, photo, src, parts):
        time.sleep(0.4)
        return _doc(sid, part_id)
    monkeypatch.setattr(observe, "propose", slow)
    prj = SimpleNamespace(root=tmp_path)
    photos = [f"input/photos/p{i}.jpg" for i in range(3)]
    t0 = time.time()
    log, errors = observe.run_for_photos(prj, object(), "X", photos, _sources(3), {"hall"})
    elapsed = time.time() - t0
    assert elapsed < 1.0, elapsed                       # sequential would take about 1.2 s
    assert [e["photo"] for e in log] == photos          # the log keeps the photo order
    assert all(e["result"] == "accepted" for e in log)


def test_one_failed_photo_does_not_stop_the_others(tmp_path, monkeypatch):
    def flaky(provider, name, part_id, sid, photo, src, parts):
        if sid == "SRC-1":
            raise ai.AIError("quota")
        return _doc(sid, part_id)
    monkeypatch.setattr(observe, "propose", flaky)
    prj = SimpleNamespace(root=tmp_path)
    photos = [f"input/photos/p{i}.jpg" for i in range(3)]
    log, _ = observe.run_for_photos(prj, object(), "X", photos, _sources(3), {"hall"})
    results = {e["photo"]: e["result"] for e in log}
    assert results["input/photos/p0.jpg"] == "accepted" and results["input/photos/p2.jpg"] == "accepted"
    assert results["input/photos/p1.jpg"].startswith("failed")
