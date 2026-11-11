"""Sweep synthetic views and seeds; report true reprojection error of the solved camera."""
import sys
sys.path.insert(0, "tests")
import numpy as np
from test_smarttex_t2 import SPEC, true_camera, render, coarse_points, kp, cm

fprior = "--prior" in sys.argv
noise = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else 0.015
tk = kp.model(SPEC)[0]
rows = []
for az in [200., 215., 230., 250., 270., 300., 330.]:
    for seed in range(3):
        cam = true_camera(az=az)
        img = render(SPEC, cam)
        co = coarse_points(cam, noise, 10, int(az) + seed)
        res = cm.solve(SPEC, img, co, f_prior=cam.f * 1.05 if fprior else None)
        c = res["camera"]
        pts, _ = kp.visible(SPEC, cam)
        X = np.array([tk[k] for k in pts])
        e = np.linalg.norm(c.project(X) - cam.project(X), axis=1) / cam.diag
        rows.append((az, seed, round(c.f / cam.f, 3), res["rms"], res["chamfer"], round(float(np.sqrt((e ** 2).mean())), 4),
                     round(float(e.max()), 4)))
        print(*rows[-1])
true = np.array([r[5] for r in rows])
print("median true rms", np.median(true), "share <= 0.005:", (true <= 0.005).mean())
