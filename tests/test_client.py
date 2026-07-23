import json
import time
from urllib.parse import parse_qs, urlparse

import httpx

from app.arc.models import CandidateTrack
from app.spotify import client


def _track(title, artist):
    return CandidateTrack(
        title=title, artist=artist, energy=50, tempo=100, valence=50, rationale="r"
    )


def _client_with_responses(responses_by_title):
    def handler(request: httpx.Request) -> httpx.Response:
        q = parse_qs(urlparse(str(request.url)).query)["q"][0]
        title = q.split('"')[1]
        return httpx.Response(200, json=responses_by_title[title])

    return httpx.Client(transport=httpx.MockTransport(handler))


def _search_result(
    spotify_id, name="Resolved Name", artist_name="Resolved Artist", popularity=50
):
    return {
        "tracks": {
            "items": [
                {
                    "id": spotify_id,
                    "uri": f"spotify:track:{spotify_id}",
                    "name": name,
                    "artists": [{"name": artist_name}],
                    "popularity": popularity,
                }
            ]
        }
    }


_NO_RESULTS = {"tracks": {"items": []}}


def _mock_client(status_code, json_body, captured=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["request"] = request
        return httpx.Response(status_code, json=json_body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_me_sends_bearer_auth_and_returns_json():
    captured = {}
    http_client = _mock_client(
        200, {"id": "user-1", "display_name": "Test User"}, captured
    )

    result = client.get_me("token-abc", client=http_client)

    assert captured["request"].url == "https://api.spotify.com/v1/me"
    assert captured["request"].headers["Authorization"] == "Bearer token-abc"
    assert result == {"id": "user-1", "display_name": "Test User"}


def test_search_track_builds_expected_query():
    captured = {}
    http_client = _mock_client(200, {"tracks": {"items": []}}, captured)

    client.search_track("Landslide", "Fleetwood Mac", "token-abc", client=http_client)

    request = captured["request"]
    assert request.url.host == "api.spotify.com"
    assert request.url.path == "/v1/search"
    params = parse_qs(urlparse(str(request.url)).query)
    assert params["q"] == ['track:"Landslide" artist:"Fleetwood Mac"']
    assert params["type"] == ["track"]
    assert params["limit"] == ["5"]
    assert request.headers["Authorization"] == "Bearer token-abc"


def test_search_track_returns_none_when_no_results():
    http_client = _mock_client(200, {"tracks": {"items": []}})

    result = client.search_track(
        "Nonexistent Song", "Nobody", "token-abc", client=http_client
    )

    assert result is None


def test_resolve_candidates_serves_repeat_lookups_from_cache(tmp_path):
    cache_path = tmp_path / "resolution_cache.json"
    tracks = [
        CandidateTrack(
            title="Dreams",
            artist="Fleetwood Mac",
            energy=50,
            tempo=120,
            valence=60,
            rationale="r",
        )
    ]
    first = client.resolve_candidates(
        tracks,
        "token-abc",
        client=_mock_client(200, _search_result("track-id-1")),
        cache_path=cache_path,
    )
    assert first.resolved == 1

    def exploding(request):
        raise AssertionError("cached lookup must not hit the API")

    repeat = [
        CandidateTrack(
            title="  DREAMS ",
            artist="fleetwood mac",
            energy=50,
            tempo=120,
            valence=60,
            rationale="r",
        )
    ]
    second = client.resolve_candidates(
        repeat,
        "token-abc",
        client=httpx.Client(transport=httpx.MockTransport(exploding)),
        cache_path=cache_path,
    )

    assert second.resolved == 1
    assert repeat[0].spotify_id == "track-id-1"


def test_resolve_candidates_caches_no_hits_negatively(tmp_path):
    cache_path = tmp_path / "resolution_cache.json"
    tracks = [
        CandidateTrack(
            title="Ghost Song",
            artist="Nobody",
            energy=50,
            tempo=120,
            valence=60,
            rationale="r",
        )
    ]
    client.resolve_candidates(
        tracks,
        "token-abc",
        client=_mock_client(200, _NO_RESULTS),
        cache_path=cache_path,
    )

    def exploding(request):
        raise AssertionError("negative-cached lookup must not hit the API")

    repeat = [
        CandidateTrack(
            title="Ghost Song",
            artist="Nobody",
            energy=50,
            tempo=120,
            valence=60,
            rationale="r",
        )
    ]
    stats = client.resolve_candidates(
        repeat,
        "token-abc",
        client=httpx.Client(transport=httpx.MockTransport(exploding)),
        cache_path=cache_path,
    )

    assert stats.resolved == 0
    assert repeat[0].spotify_id is None


def test_resolve_candidates_retries_no_hit_after_negative_ttl(tmp_path):
    cache_path = tmp_path / "resolution_cache.json"
    key = client._cache_key("Ghost Song", "Nobody")
    expired = time.time() - client.NEGATIVE_TTL_SECONDS - 1
    cache_path.write_text(json.dumps({key: {"missed_at": expired}}))

    tracks = [
        CandidateTrack(
            title="Ghost Song",
            artist="Nobody",
            energy=50,
            tempo=120,
            valence=60,
            rationale="r",
        )
    ]
    stats = client.resolve_candidates(
        tracks,
        "token-abc",
        client=_mock_client(200, _search_result("track-id-9")),
        cache_path=cache_path,
    )

    assert stats.resolved == 1
    assert tracks[0].spotify_id == "track-id-9"
    assert json.loads(cache_path.read_text())[key]["spotify_id"] == "track-id-9"


def test_record_call_counts_each_search_request(monkeypatch, tmp_path):
    log_path = tmp_path / "call_log.json"
    monkeypatch.setattr(client, "CALL_LOG_PATH", log_path)
    http_client = _mock_client(200, _NO_RESULTS)

    client.search_track("A", "B", "token-abc", client=http_client)
    client.search_track("C", "D", "token-abc", client=http_client)

    assert client.calls_today() == 2


def test_mark_day_exhausted_trips_the_daily_budget(monkeypatch, tmp_path):
    log_path = tmp_path / "call_log.json"
    monkeypatch.setattr(client, "CALL_LOG_PATH", log_path)
    client.record_call()

    client.mark_day_exhausted()

    assert client.calls_today() == 1000


def test_mark_day_exhausted_keeps_higher_existing_count(tmp_path):
    log_path = tmp_path / "call_log.json"
    log_path.write_text(json.dumps({time.strftime("%Y-%m-%d"): 2000}))

    client.mark_day_exhausted(path=log_path)

    assert client.calls_today(path=log_path) == 2000


def test_search_track_retries_on_429_honoring_retry_after():
    calls = {"n": 0}
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(429, headers={"Retry-After": "7"})
        return httpx.Response(200, json=_search_result("track-id-1"))

    http_client = httpx.Client(transport=httpx.MockTransport(handler))

    result = client.search_track(
        "Landslide",
        "Fleetwood Mac",
        "token-abc",
        client=http_client,
        sleep=sleeps.append,
    )

    assert result.spotify_id == "track-id-1"
    assert calls["n"] == 3
    assert sleeps == [7, 7]


def test_search_track_fails_fast_on_long_retry_after():
    sleeps = []
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "77236"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))

    try:
        client.search_track(
            "Landslide",
            "Fleetwood Mac",
            "token-abc",
            client=http_client,
            sleep=sleeps.append,
        )
        raise AssertionError("expected HTTPStatusError")
    except httpx.HTTPStatusError as exc:
        assert exc.response.status_code == 429
    # An hours-long lockout is not worth sleeping through in-request
    assert sleeps == []
    assert calls["n"] == 1


def test_search_track_parses_top_result():
    http_client = _mock_client(
        200,
        {
            "tracks": {
                "items": [
                    {
                        "id": "track-id-1",
                        "uri": "spotify:track:track-id-1",
                        "name": "Landslide",
                        "artists": [{"name": "Fleetwood Mac"}],
                        "popularity": 72,
                    }
                ]
            }
        },
    )

    result = client.search_track(
        "Landslide", "Fleetwood Mac", "token-abc", client=http_client
    )

    assert result.spotify_id == "track-id-1"
    assert result.spotify_uri == "spotify:track:track-id-1"
    assert result.resolved_title == "Landslide"
    assert result.resolved_artist == "Fleetwood Mac"
    assert result.popularity == 72


def test_resolve_candidates_fills_in_fields_on_match():
    tracks = [_track("Landslide", "Fleetwood Mac")]
    http_client = _client_with_responses(
        {"Landslide": _search_result("id-1", "Landslide", "Fleetwood Mac", 72)}
    )

    stats = client.resolve_candidates(tracks, "token-abc", client=http_client)

    assert tracks[0].spotify_id == "id-1"
    assert tracks[0].spotify_uri == "spotify:track:id-1"
    assert tracks[0].resolved_title == "Landslide"
    assert tracks[0].resolved_artist == "Fleetwood Mac"
    assert tracks[0].popularity == 72
    assert stats.total == 1
    assert stats.resolved == 1
    assert stats.rate == 1.0


def test_resolve_candidates_leaves_fields_none_on_no_match():
    tracks = [_track("Totally Made Up Song", "Nobody")]
    http_client = _client_with_responses({"Totally Made Up Song": _NO_RESULTS})

    stats = client.resolve_candidates(tracks, "token-abc", client=http_client)

    assert tracks[0].spotify_id is None
    assert tracks[0].spotify_uri is None
    assert stats.total == 1
    assert stats.resolved == 0
    assert stats.rate == 0.0


def test_resolve_candidates_dedupes_on_spotify_id():
    tracks = [_track("Song A", "Artist"), _track("Song B (Remaster)", "Artist")]
    http_client = _client_with_responses(
        {
            "Song A": _search_result("same-id"),
            "Song B (Remaster)": _search_result("same-id"),
        }
    )

    stats = client.resolve_candidates(tracks, "token-abc", client=http_client)

    assert tracks[0].spotify_id == "same-id"
    assert tracks[1].spotify_id is None  # dropped as a duplicate resolution
    assert stats.total == 2
    assert stats.resolved == 1
    assert stats.rate == 0.5


def test_resolve_candidates_computes_match_rate_across_mixed_results():
    tracks = [
        _track("Song A", "Artist"),
        _track("Song B", "Artist"),
        _track("Song C", "Artist"),
        _track("Song D", "Artist"),
    ]
    http_client = _client_with_responses(
        {
            "Song A": _search_result("id-a"),
            "Song B": _search_result("id-b"),
            "Song C": _NO_RESULTS,
            "Song D": _NO_RESULTS,
        }
    )

    stats = client.resolve_candidates(tracks, "token-abc", client=http_client)

    assert stats.total == 4
    assert stats.resolved == 2
    assert stats.rate == 0.5


def test_create_playlist_posts_private_playlist():
    captured = {}
    http_client = _mock_client(
        201,
        {
            "id": "pl-1",
            "external_urls": {"spotify": "https://open.spotify.com/playlist/pl-1"},
        },
        captured,
    )

    result = client.create_playlist(
        "Dinner mix", "token-abc", description="arc test", client=http_client
    )

    request = captured["request"]
    assert request.url == "https://api.spotify.com/v1/me/playlists"
    assert request.headers["Authorization"] == "Bearer token-abc"
    body = json.loads(request.content)
    assert body == {"name": "Dinner mix", "description": "arc test", "public": False}
    assert result["id"] == "pl-1"


def test_add_tracks_batches_by_100():
    batches = []

    def handler(request: httpx.Request) -> httpx.Response:
        batches.append(json.loads(request.content)["uris"])
        assert str(request.url) == "https://api.spotify.com/v1/playlists/pl-1/items"
        assert request.headers["Authorization"] == "Bearer token-abc"
        return httpx.Response(201, json={"snapshot_id": "snap"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    uris = [f"spotify:track:{i}" for i in range(230)]

    client.add_tracks("pl-1", uris, "token-abc", client=http_client)

    assert [len(b) for b in batches] == [100, 100, 30]
    assert batches[0][0] == "spotify:track:0"
    assert batches[2][-1] == "spotify:track:229"


def test_unfollow_playlist_deletes_followers():
    captured = {}
    http_client = _mock_client(200, {}, captured)

    client.unfollow_playlist("pl-1", "token-abc", client=http_client)

    request = captured["request"]
    assert request.method == "DELETE"
    assert request.url == "https://api.spotify.com/v1/playlists/pl-1/followers"
    assert request.headers["Authorization"] == "Bearer token-abc"
