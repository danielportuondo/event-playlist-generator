import httpx
from fastapi.testclient import TestClient

from app import main
from app.arc.models import CandidateTrack
from app.config import Config
from app.spotify import client as spotify_client
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
    assert "Event-Arc" in response.text


def test_presets_lists_all_six():
    response = client.get("/api/presets")

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 6
    ids = {p["id"] for p in data}
    assert "dinner_party" in ids and "workout_run" in ids
    assert all(
        {
            "id",
            "label",
            "description",
            "default_duration_min",
            "avg_track_len_min",
            "phases",
        }
        <= set(p)
        for p in data
    )
    for p in data:
        assert p["phases"], f"{p['id']} has no phases"
        assert all({"name", "fraction", "energy"} <= set(ph) for ph in p["phases"])


def test_session_reports_unauthenticated(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)
    monkeypatch.setattr(main.auth, "get_cached_access_token", lambda *a, **kw: None)

    assert client.get("/api/session").json() == {
        "authenticated": False,
        "visitor_live": False,
        "spotify_calls_today": 0,
    }


def test_session_reports_visitor_live_with_client_secret(monkeypatch):
    config = Config(
        gemini_api_key="test-key",
        gemini_model="test-model",
        spotify_client_id="test-client-id",
        spotify_redirect_uri="http://127.0.0.1:8000/callback",
        spotify_client_secret="test-secret",
    )
    monkeypatch.setattr(main, "_get_config", lambda: config)
    monkeypatch.setattr(main.auth, "get_cached_access_token", lambda *a, **kw: None)

    assert client.get("/api/session").json() == {
        "authenticated": False,
        "visitor_live": True,
        "spotify_calls_today": 0,
    }


def test_generate_requires_login(monkeypatch):
    _patch_pipeline(monkeypatch, [], token=None)
    monkeypatch.setattr(main.auth, "get_app_access_token", lambda *a, **kw: None)

    response = client.post("/api/generate", json={"event_id": "dinner_party"})

    assert response.status_code == 401


def test_generate_falls_back_to_app_token_for_visitors(monkeypatch):
    _patch_pipeline(monkeypatch, _fake_candidates(12), token=None)
    seen = {}

    def fake_app_token(*a, **kw):
        seen["called"] = True
        return "app-token"

    def resolver(candidates, access_token, client=None):
        seen["token"] = access_token
        return _resolve_all(candidates, access_token, client)

    monkeypatch.setattr(main.auth, "get_app_access_token", fake_app_token)
    monkeypatch.setattr(main, "resolve_candidates", resolver)

    response = client.post(
        "/api/generate", json={"event_id": "dinner_party", "duration_min": 15}
    )

    assert response.status_code == 200
    assert seen == {"called": True, "token": "app-token"}


def test_generate_prefers_user_token_when_logged_in(monkeypatch):
    _patch_pipeline(monkeypatch, _fake_candidates(12), token="user-token")
    seen = {}

    def resolver(candidates, access_token, client=None):
        seen["token"] = access_token
        return _resolve_all(candidates, access_token, client)

    monkeypatch.setattr(
        main.auth,
        "get_app_access_token",
        lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not be called")),
    )
    monkeypatch.setattr(main, "resolve_candidates", resolver)

    response = client.post(
        "/api/generate", json={"event_id": "dinner_party", "duration_min": 15}
    )

    assert response.status_code == 200
    assert seen["token"] == "user-token"


def _patch_visitor_pipeline(monkeypatch, candidates):
    _patch_pipeline(monkeypatch, candidates, token=None)
    monkeypatch.setattr(main.auth, "get_app_access_token", lambda *a, **kw: "app-token")


def test_generate_blocks_visitor_over_daily_budget(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("generate_candidates should not run when blocked")

    _patch_visitor_pipeline(monkeypatch, _fake_candidates(12))
    monkeypatch.setattr(main, "generate_candidates", boom)
    monkeypatch.setattr(main, "calls_today", lambda: 101)

    response = client.post(
        "/api/generate", json={"event_id": "dinner_party", "duration_min": 15}
    )

    assert response.status_code == 503
    assert "budget" in response.json()["detail"]


def test_generate_allows_visitor_under_daily_budget(monkeypatch):
    _patch_visitor_pipeline(monkeypatch, _fake_candidates(12))
    monkeypatch.setattr(main, "calls_today", lambda: 100)

    response = client.post(
        "/api/generate", json={"event_id": "dinner_party", "duration_min": 15}
    )

    assert response.status_code == 200


def test_generate_owner_keeps_reserve_above_visitor_ceiling(monkeypatch):
    _patch_pipeline(monkeypatch, _fake_candidates(12), token="user-token")
    monkeypatch.setattr(main, "calls_today", lambda: 101)

    response = client.post(
        "/api/generate", json={"event_id": "dinner_party", "duration_min": 15}
    )

    assert response.status_code == 200


def test_generate_blocks_owner_over_daily_budget(monkeypatch):
    _patch_pipeline(monkeypatch, _fake_candidates(12), token="user-token")
    monkeypatch.setattr(main, "calls_today", lambda: 121)

    response = client.post(
        "/api/generate", json={"event_id": "dinner_party", "duration_min": 15}
    )

    assert response.status_code == 503


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
        return ResolutionStats(
            total=len(candidates), resolved=2, rate=2 / len(candidates)
        )

    _patch_pipeline(monkeypatch, _fake_candidates(10), resolver=resolve_two)

    response = client.post(
        "/api/generate",
        json={"event_id": "dinner_party", "duration_min": 15},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 2
    assert "shortened" in data["warning"]


def test_generate_503_with_friendly_message_on_rate_lockout(monkeypatch):
    def resolve_429(candidates, access_token, client=None):
        response = httpx.Response(
            429,
            headers={"Retry-After": "77236"},
            request=httpx.Request("GET", "https://api.spotify.com/v1/search"),
        )
        raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)

    _patch_pipeline(monkeypatch, _fake_candidates(10), resolver=resolve_429)

    response = client.post(
        "/api/generate", json={"event_id": "dinner_party", "duration_min": 15}
    )

    assert response.status_code == 503
    assert "try again in about 21 hours" in response.json()["detail"]


def test_healthz_costs_nothing():
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_generate_rate_lockout_marks_day_exhausted(monkeypatch):
    def resolve_429(candidates, access_token, client=None):
        response = httpx.Response(
            429,
            headers={"Retry-After": "77236"},
            request=httpx.Request("GET", "https://api.spotify.com/v1/search"),
        )
        raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)

    _patch_pipeline(monkeypatch, _fake_candidates(10), resolver=resolve_429)

    client.post("/api/generate", json={"event_id": "dinner_party", "duration_min": 15})

    assert spotify_client.calls_today() >= main.OWNER_DAILY_CEILING


def test_generate_short_429_does_not_mark_day_exhausted(monkeypatch):
    def resolve_429(candidates, access_token, client=None):
        response = httpx.Response(
            429,
            headers={"Retry-After": "30"},
            request=httpx.Request("GET", "https://api.spotify.com/v1/search"),
        )
        raise httpx.HTTPStatusError("rate limited", request=response.request, response=response)

    _patch_pipeline(monkeypatch, _fake_candidates(10), resolver=resolve_429)

    response = client.post(
        "/api/generate", json={"event_id": "dinner_party", "duration_min": 15}
    )

    assert response.status_code == 503
    assert spotify_client.calls_today() == 0


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

    response = client.get("/callback", params={"code": "abc", "state": "never-issued"})

    assert response.status_code == 400


def test_callback_rejects_spotify_error(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)

    response = client.get("/callback", params={"error": "access_denied", "state": "s"})

    assert response.status_code == 400
    assert "access_denied" in response.json()["detail"]


def test_login_redirects_to_spotify_authorize(monkeypatch):
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)

    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"].startswith(
        "https://accounts.spotify.com/authorize"
    )


def _patch_save(monkeypatch, token="at-1"):
    calls = {}
    monkeypatch.setattr(main, "_get_config", lambda: CONFIG)
    monkeypatch.setattr(main.auth, "get_cached_access_token", lambda *a, **kw: token)

    def fake_create(name, access_token, description=""):
        calls["create"] = {"name": name, "description": description}
        return {
            "id": "pl-1",
            "external_urls": {"spotify": "https://open.spotify.com/playlist/pl-1"},
        }

    def fake_add(playlist_id, uris, access_token):
        calls["add"] = {"playlist_id": playlist_id, "uris": uris}

    monkeypatch.setattr(main, "create_playlist", fake_create)
    monkeypatch.setattr(main, "add_tracks", fake_add)
    return calls


def test_save_requires_login(monkeypatch):
    _patch_save(monkeypatch, token=None)

    response = client.post(
        "/api/save", json={"name": "Mix", "uris": ["spotify:track:a"]}
    )

    assert response.status_code == 401


def test_save_rejects_empty_uris(monkeypatch):
    _patch_save(monkeypatch)

    response = client.post("/api/save", json={"name": "Mix", "uris": []})

    assert response.status_code == 422


def test_save_creates_playlist_and_adds_tracks(monkeypatch):
    calls = _patch_save(monkeypatch)
    uris = ["spotify:track:a", "spotify:track:b"]

    response = client.post("/api/save", json={"name": "Dinner mix", "uris": uris})

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "playlist_id": "pl-1",
        "playlist_url": "https://open.spotify.com/playlist/pl-1",
        "track_count": 2,
    }
    assert calls["create"] == {"name": "Dinner mix", "description": ""}
    assert calls["add"] == {"playlist_id": "pl-1", "uris": uris}


def test_save_returns_502_on_spotify_error(monkeypatch):
    _patch_save(monkeypatch)

    def boom(*a, **kw):
        raise httpx.HTTPError("spotify down")

    monkeypatch.setattr(main, "create_playlist", boom)

    response = client.post(
        "/api/save", json={"name": "Mix", "uris": ["spotify:track:a"]}
    )

    assert response.status_code == 502
    assert "Spotify save failed" in response.json()["detail"]


def test_generate_shortens_playlist_when_artist_cap_limits_pool(monkeypatch):
    # 11 tracks by one artist + 1 unique: all resolve, but dinner_party caps
    # each artist at 2, so only 3 tracks are usable for the 4 slots of 15 min.
    candidates = [
        CandidateTrack(
            title=f"Song {i}",
            artist="Same Artist" if i < 11 else "Other Artist",
            energy=(10 + i * 7) % 90,
            tempo=100,
            valence=60,
            rationale=f"reason {i}",
        )
        for i in range(12)
    ]
    _patch_pipeline(monkeypatch, candidates)

    response = client.post(
        "/api/generate",
        json={"event_id": "dinner_party", "duration_min": 15},
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["rows"]) == 3
    assert "artist-repeat" in data["warning"]
    artists = [r["artist"] for r in data["rows"]]
    assert artists.count("Same Artist") == 2


def test_save_deletes_playlist_when_add_tracks_fails(monkeypatch):
    calls = _patch_save(monkeypatch)

    def add_boom(*a, **kw):
        raise httpx.HTTPError("add failed")

    def fake_unfollow(playlist_id, access_token):
        calls["unfollow"] = playlist_id

    monkeypatch.setattr(main, "add_tracks", add_boom)
    monkeypatch.setattr(main, "unfollow_playlist", fake_unfollow)

    response = client.post(
        "/api/save", json={"name": "Mix", "uris": ["spotify:track:a"]}
    )

    assert response.status_code == 502
    assert calls["unfollow"] == "pl-1"


def test_save_surfaces_add_error_even_if_cleanup_fails(monkeypatch):
    _patch_save(monkeypatch)

    def boom(*a, **kw):
        raise httpx.HTTPError("spotify down")

    monkeypatch.setattr(main, "add_tracks", boom)
    monkeypatch.setattr(main, "unfollow_playlist", boom)

    response = client.post(
        "/api/save", json={"name": "Mix", "uris": ["spotify:track:a"]}
    )

    assert response.status_code == 502
    assert "Spotify save failed" in response.json()["detail"]
