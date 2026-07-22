import pytest

from app.spotify import client


@pytest.fixture(autouse=True)
def _isolate_spotify_disk_state(tmp_path, monkeypatch):
    """Keep the resolution cache and call log out of the repo during tests."""
    monkeypatch.setattr(client, "RESOLUTION_CACHE_PATH", tmp_path / "resolution_cache.json")
    monkeypatch.setattr(client, "CALL_LOG_PATH", tmp_path / "call_log.json")
