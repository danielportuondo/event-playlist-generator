import json

import httpx

from app import persistence
from app.config import Config
from app.spotify import client as spotify_client

GIST_CONFIG = Config(
    gemini_api_key="k",
    gemini_model="m",
    spotify_client_id="cid",
    spotify_redirect_uri="http://127.0.0.1:8000/callback",
    gist_token="ghp-test",
    gist_id="gist-123",
)
NO_GIST_CONFIG = Config(
    gemini_api_key="k",
    gemini_model="m",
    spotify_client_id="cid",
    spotify_redirect_uri="http://127.0.0.1:8000/callback",
)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_hydrate_restores_synced_files_from_gist():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert "gist-123" in str(request.url)
        assert request.headers["Authorization"] == "Bearer ghp-test"
        return httpx.Response(
            200,
            json={
                "files": {
                    "call_log.json": {"content": json.dumps({"2026-07-23": 42})},
                    "resolution_cache.json": {
                        "content": json.dumps({"a|b": {"missed_at": 1}})
                    },
                }
            },
        )

    persistence.hydrate(GIST_CONFIG, client=_client(handler))

    assert json.loads(spotify_client.CALL_LOG_PATH.read_text()) == {"2026-07-23": 42}
    assert "a|b" in json.loads(spotify_client.RESOLUTION_CACHE_PATH.read_text())


def test_hydrate_fetches_truncated_files_via_raw_url():
    def handler(request: httpx.Request) -> httpx.Response:
        if "raw" in str(request.url):
            return httpx.Response(200, text=json.dumps({"big": True}))
        return httpx.Response(
            200,
            json={
                "files": {
                    "resolution_cache.json": {
                        "content": "{trunc",
                        "truncated": True,
                        "raw_url": "https://gist.example/raw",
                    }
                }
            },
        )

    persistence.hydrate(GIST_CONFIG, client=_client(handler))

    assert json.loads(spotify_client.RESOLUTION_CACHE_PATH.read_text()) == {"big": True}


def test_hydrate_falls_back_to_committed_seed(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    seed.write_text(json.dumps({"x|y": {"missed_at": 1}}))
    monkeypatch.setattr(persistence, "SEED_CACHE_PATH", seed)

    persistence.hydrate(NO_GIST_CONFIG)

    assert spotify_client.RESOLUTION_CACHE_PATH.read_text() == seed.read_text()


def test_hydrate_seeds_even_when_config_is_none(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    seed.write_text("{}")
    monkeypatch.setattr(persistence, "SEED_CACHE_PATH", seed)

    persistence.hydrate(None)

    assert spotify_client.RESOLUTION_CACHE_PATH.exists()


def test_hydrate_survives_gist_api_failure(tmp_path, monkeypatch):
    seed = tmp_path / "seed.json"
    seed.write_text("{}")
    monkeypatch.setattr(persistence, "SEED_CACHE_PATH", seed)

    persistence.hydrate(
        GIST_CONFIG, client=_client(lambda request: httpx.Response(500))
    )

    assert spotify_client.RESOLUTION_CACHE_PATH.exists()


def test_hydrate_does_not_overwrite_existing_cache_with_seed(tmp_path, monkeypatch):
    spotify_client.RESOLUTION_CACHE_PATH.write_text('{"live": 1}')
    seed = tmp_path / "seed.json"
    seed.write_text('{"stale": 1}')
    monkeypatch.setattr(persistence, "SEED_CACHE_PATH", seed)

    persistence.hydrate(NO_GIST_CONFIG)

    assert spotify_client.RESOLUTION_CACHE_PATH.read_text() == '{"live": 1}'


def test_sync_is_a_noop_when_gist_env_unset():
    spotify_client.record_call()

    persistence.sync(NO_GIST_CONFIG)  # would raise via real HTTP if not a no-op


def test_sync_patches_gist_with_local_files():
    spotify_client.record_call()
    spotify_client.RESOLUTION_CACHE_PATH.write_text('{"a|b": {"missed_at": 1}}')
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["method"] = request.method
        seen["files"] = json.loads(request.content)["files"]
        return httpx.Response(200, json={})

    persistence.sync(GIST_CONFIG, client=_client(handler))

    assert seen["method"] == "PATCH"
    assert set(seen["files"]) == {"call_log.json", "resolution_cache.json"}
    assert '"missed_at"' in seen["files"]["resolution_cache.json"]["content"]


def test_sync_swallows_http_errors():
    spotify_client.record_call()

    persistence.sync(
        GIST_CONFIG, client=_client(lambda request: httpx.Response(500))
    )  # must not raise
