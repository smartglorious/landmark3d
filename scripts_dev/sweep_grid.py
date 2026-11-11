"""Grid vs no grid: 3 coarse points, given noise, banded synthetic facades."""
import sys
sys.path.insert(0, "tests")
import numpy as np
from test_smarttex_t2 import SPEC_GRID, true_camera, render, coarse_points, kp, cm

noise = float(sys.argv[1]) if len(sys.argv) > 1 else 0.03
n = int(sys.argv[2]) if len(sys.argv) > 2 else 3
tk = kp.model(SPEC_GRID)[0]
res = {True: [], False: []}
for az in [200., 215., 230., 250., 270., 300., 330.]:
    for seed in range(2):
        cam = true_camera(az=az)
        img = render(SPEC_GRID, cam, grid_bands=True)
        co = coarse_points(cam, noise, n, int(az) + seed + 7)
        pts, _ = kp.visible(SPEC_GRID, cam)
        X = np.array([tk[k] for k in pts])
        row = [az, seed]
        for g in (False, True):
            r = cm.solve(SPEC_GRID, img, co, view={"azimuth_deg": az}, use_grid=g)
            if r["status"] != "solved":
                res[g].append(1.0); row += ["fail"]; continue
            e = np.linalg.norm(r["camera"].project(X) - cam.project(X), axis=1) / cam.diag
            res[g].append(float(np.sqrt((e ** 2).mean())))
            row += [round(res[g][-1], 4), r.get("start"), round(r["support"], 2), not r["gate"]]
        print(*row, flush=True)
for g in (False, True):
    a = np.array(res[g])
    print("grid" if g else "no grid", "median", round(float(np.median(a)), 4), "share<=0.005", round(float((a <= 0.005).mean()), 2))
