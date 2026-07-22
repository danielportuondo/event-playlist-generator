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


def _search_result(spotify_id, name="Resolved Name", artist_name="Resolved Artist", popularity=50):
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
    http_client = _mock_client(200, {"id": "user-1", "display_name": "Test User"}, captured)

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

    result = client.search_track("Nonexistent Song", "Nobody", "token-abc", client=http_client)

    assert result is None


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

    result = client.search_track("Landslide", "Fleetwood Mac", "token-abc", client=http_client)

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
