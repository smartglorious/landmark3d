import pytest


@pytest.fixture(autouse=True)
def no_satellite_network(monkeypatch):
    """Tests never download imagery; satellite tests patch fetch_esri themselves."""
    from landmark3d.research import satellite

    def offline(*a, **k):
        raise RuntimeError("network disabled in tests")
    monkeypatch.setattr(satellite, "fetch_esri", offline)


@pytest.fixture(autouse=True)
def no_image_generation(monkeypatch):
    """Tests never call the real Codex image generator; generated-texture tests pass a fake one."""
    from landmark3d.smarttex import genfacade
    monkeypatch.setattr(genfacade, "codex_cli", lambda: None)
