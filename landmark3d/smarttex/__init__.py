"""Smart Texture (docs/SMART_TEXTURE_PLAN.md): calibrated cameras per reference photo, projective texturing, measured
accuracy.

- metrics: T0 texture-only metrics (sky share, bay phase error, seam colour difference) and the baseline report.
- lens: T1 photo intake (EXIF focal prior, line straightness, one-parameter division undistortion, panorama check).
- keypoints: named 3D keypoints and edges of the spec, wireframe drawing.
- camera: T2 camera solve against the model (vanishing points, PnP, snapping, edge refinement, residuals).
- run: the AI keypoint step and input/cameras.json.
"""
