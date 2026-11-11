"""OpenCV is a pinned dependency for Smart Texture (docs/SMART_TEXTURE_PLAN.md). These checks fail early when an
install lacks it or a version drops a function the plan relies on."""
import numpy as np

cv2 = __import__("cv2")


def test_line_segment_detector_finds_a_line():
    img = np.zeros((200, 200), np.uint8)
    cv2.line(img, (10, 10), (190, 150), 255, 2)
    lines = cv2.createLineSegmentDetector().detect(img)[0]
    assert lines is not None and len(lines) >= 1


def test_pnp_recovers_a_known_camera():
    rng = np.random.default_rng(0)
    obj = rng.uniform(-1, 1, (12, 3)).astype(np.float64)
    K = np.array([[800.0, 0, 320], [0, 800, 240], [0, 0, 1]])
    rvec, tvec = np.array([0.1, -0.2, 0.05]), np.array([0.2, -0.1, 6.0])
    uv, _ = cv2.projectPoints(obj, rvec, tvec, K, None)
    ok, r, t, inliers = cv2.solvePnPRansac(obj, uv, K, None)
    assert ok and len(inliers) == len(obj)
    assert np.allclose(t.ravel(), tvec, atol=1e-3)


def test_grabcut_and_pyramids_exist():
    for name in ("grabCut", "pyrDown", "pyrUp", "findHomography", "undistort"):
        assert hasattr(cv2, name), name
