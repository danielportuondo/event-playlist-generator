from fastapi.testclient import TestClient

from app import main
from app.arc.models import CandidateTrack
from app.config import Config
from app.spotify.client import ResolutionStats

CONFIG = Config(
    gemini_api_key="test-key",
    gemini_model="test-model",
    spotify_client_id="test-client-id",
    spotify_redirect_uri="http://127.0.0.1:8000/callback",
)

client = TestClient(main.app, raise_server_exceptions=False)


def _fake_candidates(n: int, energy_spread: bool = True) -> list[CandidateTrack]:
    return [
        CandidateTrack(
            title=f"Song {i}",
            artist=f"Artist {i}",
            energy=(10 + i * 7) % 90 if energy_spread else 50,
            tempo=100,
            valence=60,
            rationale=f"reason {i}",
        )
        for i in range(n)
    ]


def _resolve_all(candidates, access_token, client=None):
    for i, track in enumerate(candidates):
        track.spotify_id = f"id-{i}"
        track.spotify_uri = f"spotify:track:id-{i}"
        track.resolved_title = track.title
        track.resolved_artist = track.artist
    return ResolutionStats(total=len(candidates), resolved=len(candidates), rate=1.0)


def _patch_pipeline(monkeypatch, candidates, resolver=_resolve_all, token="at-1"):
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)
    monkeypatch.setattr(main.auth, "get_cached_access_token", lambda *a, **kw: token)
    monkeypatch.setattr(main, "generate_candidates", lambda *a, **kw: candidates)
    monkeypatch.setattr(main, "resolve_candidates", resolver)


def test_index_serves_html():
    response = client.get("/")

    assert response.status_code == 200
    assert "Event-Arc Playlist Builder" in response.text


def test_presets_lists_all_six():
    response = client.get("/api/presets")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 6
    ids = {p["id"] for p in data}
    assert "dinner_party" in ids and "workout_run" in ids
    assert all(
        {"id", "label", "description", "default_duration_min"} <= set(p) for p in data
    )


def test_session_reports_unauthenticated(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)
    monkeypatch.setattr(main.auth, "get_cached_access_token", lambda *a, **kw: None)

    assert client.get("/api/session").json() == {"authenticated": False}


def test_generate_requires_login(monkeypatch):
    _patch_pipeline(monkeypatch, [], token=None)

    response = client.post("/api/generate", json={"event_id": "dinner_party"})

    assert response.status_code == 401


def test_generate_rejects_unknown_event(monkeypatch):
    _patch_pipeline(monkeypatch, _fake_candidates(10))

    response = client.post("/api/generate", json={"event_id": "rave_in_a_cave"})

    assert response.status_code == 422


def test_generate_returns_ordered_breakdown(monkeypatch):
    _patch_pipeline(monkeypatch, _fake_candidates(12))

    response = client.post(
        "/api/generate",
        json={"event_id": "dinner_party", "duration_min": 15},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["warning"] is None
    assert data["resolution"]["rate"] == 1.0
    rows = data["rows"]
    assert [r["slot_index"] for r in rows] == list(range(len(rows)))
    assert all(
        {"phase", "target_energy", "actual_energy", "title", "rationale", "spotify_uri"}
        <= set(r)
        for r in rows
    )
    assert all(r["spotify_uri"].startswith("spotify:track:") for r in rows)


def test_generate_pins_seed_track(monkeypatch):
    candidates = _fake_candidates(12)
    _patch_pipeline(monkeypatch, candidates)

    response = client.post(
        "/api/generate",
        json={
            "event_id": "dinner_party",
            "duration_min": 15,
            "seeds": [{"title": "song 3", "artist": "ARTIST 3"}],
        },
    )

    assert response.status_code == 200
    titles = [r["title"] for r in response.json()["rows"]]
    assert "Song 3" in titles


def test_generate_shortens_playlist_on_resolution_shortfall(monkeypatch):
    def resolve_two(candidates, access_token, client=None):
        for i, track in enumerate(candidates[:2]):
            track.spotify_id = f"id-{i}"
            track.spotify_uri = f"spotify:track:id-{i}"
            track.resolved_title = track.title
            track.resolved_artist = track.artist
        return ResolutionStats(total=len(candidates), resolved=2, rate=2 / len(candidates))

    _patch_pipeline(monkeypatch, _fake_candidates(10), resolver=resolve_two)

    response = client.post(
        "/api/generate",
        json={"event_id": "dinner_party", "duration_min": 15},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 2
    assert "shortened" in data["warning"]


def test_generate_502_when_nothing_resolves(monkeypatch):
    def resolve_none(candidates, access_token, client=None):
        return ResolutionStats(total=len(candidates), resolved=0, rate=0.0)

    _patch_pipeline(monkeypatch, _fake_candidates(10), resolver=resolve_none)

    response = client.post(
        "/api/generate",
        json={"event_id": "dinner_party", "duration_min": 15},
    )

    assert response.status_code == 502


def test_callback_rejects_state_mismatch(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)

    response = client.get(
        "/callback", params={"code": "abc", "state": "never-issued"}
    )

    assert response.status_code == 400


def test_callback_rejects_spotify_error(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)

    response = client.get(
        "/callback", params={"error": "access_denied", "state": "s"}
    )

    assert response.status_code == 400
    assert "access_denied" in response.json()["detail"]


def test_login_redirects_to_spotify_authorize(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith("https://accounts.spotify.com/authorize")
