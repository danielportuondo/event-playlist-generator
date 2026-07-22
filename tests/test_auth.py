import base64
import hashlib
import json
import string
import threading
import time
from urllib.parse import parse_qs, parse_qsl, urlparse

import httpx

from app.config import Config
from app.spotify import auth

CONFIG = Config(
    gemini_api_key="test-key",
    gemini_model="test-model",
    spotify_client_id="test-client-id",
    spotify_redirect_uri="http://127.0.0.1:8000/callback",
)

_UNRESERVED = set(string.ascii_letters + string.digits + "-._~")


def test_code_verifier_is_43_to_128_unreserved_chars():
    verifier = auth.generate_code_verifier()

    assert 43 <= len(verifier) <= 128
    assert set(verifier) <= _UNRESERVED


def test_code_verifier_is_random_each_call():
    assert auth.generate_code_verifier() != auth.generate_code_verifier()


def test_code_challenge_is_s256_of_verifier():
    verifier = "a" * 64
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )

    assert auth.generate_code_challenge(verifier) == expected


def test_build_authorize_url_contains_expected_params():
    url = auth.build_authorize_url(CONFIG, state="abc123", code_challenge="xyz789")

    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "accounts.spotify.com"
    assert parsed.path == "/authorize"

    params = parse_qs(parsed.query)
    assert params["client_id"] == [CONFIG.spotify_client_id]
    assert params["response_type"] == ["code"]
    assert params["redirect_uri"] == [CONFIG.spotify_redirect_uri]
    assert params["code_challenge_method"] == ["S256"]
    assert params["code_challenge"] == ["xyz789"]
    assert params["state"] == ["abc123"]
    assert params["scope"] == ["playlist-modify-private"]


def _mock_client(status_code, json_body, captured):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured["body"] = dict(parse_qsl(request.content.decode()))
        return httpx.Response(status_code, json=json_body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_exchange_code_for_token_posts_pkce_params_and_parses_response():
    captured = {}
    client = _mock_client(
        200,
        {
            "access_token": "at-1",
            "refresh_token": "rt-1",
            "expires_in": 3600,
            "token_type": "Bearer",
        },
        captured,
    )

    result = auth.exchange_code_for_token(
        CONFIG, code="auth-code", code_verifier="verifier-123", client=client
    )

    assert captured["request"].url == "https://accounts.spotify.com/api/token"
    assert captured["body"] == {
        "grant_type": "authorization_code",
        "code": "auth-code",
        "redirect_uri": CONFIG.spotify_redirect_uri,
        "client_id": CONFIG.spotify_client_id,
        "code_verifier": "verifier-123",
    }
    assert result == {
        "access_token": "at-1",
        "refresh_token": "rt-1",
        "expires_in": 3600,
        "token_type": "Bearer",
    }


def test_refresh_access_token_posts_refresh_params_and_parses_response():
    captured = {}
    client = _mock_client(
        200,
        {"access_token": "at-2", "expires_in": 3600, "token_type": "Bearer"},
        captured,
    )

    result = auth.refresh_access_token(CONFIG, refresh_token="rt-1", client=client)

    assert captured["body"] == {
        "grant_type": "refresh_token",
        "refresh_token": "rt-1",
        "client_id": CONFIG.spotify_client_id,
    }
    assert result["access_token"] == "at-2"


def test_load_token_cache_returns_none_when_file_missing(tmp_path):
    path = tmp_path / ".token_cache.json"

    assert auth.load_token_cache(path) is None


def test_save_and_load_token_cache_roundtrip(tmp_path):
    path = tmp_path / ".token_cache.json"
    data = {"access_token": "at", "refresh_token": "rt", "expires_at": 12345.0}

    auth.save_token_cache(data, path)

    assert json.loads(path.read_text()) == data
    assert auth.load_token_cache(path) == data


def test_is_token_expired_true_past_buffer():
    assert auth.is_token_expired(expires_at=1000.0, now=990.0, buffer_seconds=60) is True


def test_is_token_expired_false_when_comfortably_valid():
    assert auth.is_token_expired(expires_at=1000.0, now=100.0, buffer_seconds=60) is False


def test_capture_callback_returns_query_params_from_redirect():
    port = 8765

    def send_redirect():
        time.sleep(0.1)
        httpx.get(f"http://127.0.0.1:{port}/callback", params={"code": "abc", "state": "xyz"})

    thread = threading.Thread(target=send_redirect)
    thread.start()
    params = auth.capture_callback(port)
    thread.join()

    assert params == {"code": "abc", "state": "xyz"}


def _token_response_client(json_body, captured=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["body"] = dict(parse_qsl(request.content.decode()))
        return httpx.Response(200, json=json_body)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_get_valid_access_token_returns_cached_token_when_not_expired(tmp_path):
    cache_path = tmp_path / ".token_cache.json"
    auth.save_token_cache(
        {"access_token": "cached-at", "refresh_token": "rt", "expires_at": 1000.0},
        cache_path,
    )

    def exploding_client(*args, **kwargs):
        raise AssertionError("should not make an HTTP call for a valid cached token")

    token = auth.get_valid_access_token(
        CONFIG,
        client=httpx.Client(transport=httpx.MockTransport(exploding_client)),
        cache_path=cache_path,
        now=500.0,
    )

    assert token == "cached-at"


def test_get_valid_access_token_refreshes_when_expired(tmp_path):
    cache_path = tmp_path / ".token_cache.json"
    auth.save_token_cache(
        {"access_token": "old-at", "refresh_token": "rt-1", "expires_at": 1000.0},
        cache_path,
    )
    client = _token_response_client(
        {"access_token": "new-at", "expires_in": 3600, "token_type": "Bearer"}
    )

    token = auth.get_valid_access_token(
        CONFIG, client=client, cache_path=cache_path, now=999.0
    )

    assert token == "new-at"
    saved = auth.load_token_cache(cache_path)
    assert saved["access_token"] == "new-at"
    assert saved["refresh_token"] == "rt-1"  # retained, not returned by Spotify
    assert saved["expires_at"] == 999.0 + 3600


def test_get_valid_access_token_runs_interactive_login_when_no_cache(tmp_path):
    cache_path = tmp_path / ".token_cache.json"
    captured = {}

    def fake_open_browser(url):
        captured["url"] = url

    def fake_capture_callback(port):
        state = parse_qs(urlparse(captured["url"]).query)["state"][0]
        return {"code": "auth-code-1", "state": state}

    client = _token_response_client(
        {"access_token": "fresh-at", "refresh_token": "fresh-rt", "expires_in": 3600}
    )

    token = auth.get_valid_access_token(
        CONFIG,
        client=client,
        cache_path=cache_path,
        now=0.0,
        open_browser=fake_open_browser,
        capture_callback=fake_capture_callback,
    )

    assert token == "fresh-at"
    assert "url" in captured
    saved = auth.load_token_cache(cache_path)
    assert saved == {
        "access_token": "fresh-at",
        "refresh_token": "fresh-rt",
        "expires_at": 3600.0,
    }


def test_get_valid_access_token_raises_on_state_mismatch(tmp_path):
    cache_path = tmp_path / ".token_cache.json"

    token = None
    try:
        auth.get_valid_access_token(
            CONFIG,
            client=httpx.Client(),
            cache_path=cache_path,
            now=0.0,
            open_browser=lambda url: None,
            capture_callback=lambda port: {"code": "abc", "state": "wrong-state"},
        )
    except ValueError as exc:
        token = str(exc)

    assert token is not None and "state" in token.lower()


def test_get_valid_access_token_raises_on_spotify_error_param(tmp_path):
    cache_path = tmp_path / ".token_cache.json"
    captured = {}

    def fake_open_browser(url):
        captured["url"] = url

    def fake_capture_callback(port):
        state = parse_qs(urlparse(captured["url"]).query)["state"][0]
        return {"error": "access_denied", "state": state}

    message = None
    try:
        auth.get_valid_access_token(
            CONFIG,
            client=httpx.Client(),
            cache_path=cache_path,
            now=0.0,
            open_browser=fake_open_browser,
            capture_callback=fake_capture_callback,
        )
    except ValueError as exc:
        message = str(exc)

    assert message is not None and "access_denied" in message


def test_get_cached_access_token_returns_none_without_cache(tmp_path):
    assert auth.get_cached_access_token(CONFIG, cache_path=tmp_path / "none.json") is None


def test_get_cached_access_token_returns_valid_cached_token(tmp_path):
    cache_path = tmp_path / ".token_cache.json"
    auth.save_token_cache(
        {"access_token": "cached-at", "refresh_token": "rt", "expires_at": 1000.0},
        cache_path,
    )

    def exploding_client(*args, **kwargs):
        raise AssertionError("should not make an HTTP call for a valid cached token")

    token = auth.get_cached_access_token(
        CONFIG,
        client=httpx.Client(transport=httpx.MockTransport(exploding_client)),
        cache_path=cache_path,
        now=500.0,
    )

    assert token == "cached-at"


def test_get_cached_access_token_refreshes_expired_token(tmp_path):
    cache_path = tmp_path / ".token_cache.json"
    auth.save_token_cache(
        {"access_token": "old-at", "refresh_token": "rt-1", "expires_at": 1000.0},
        cache_path,
    )
    client = _token_response_client(
        {"access_token": "new-at", "expires_in": 3600, "token_type": "Bearer"}
    )

    token = auth.get_cached_access_token(
        CONFIG, client=client, cache_path=cache_path, now=999.0
    )

    assert token == "new-at"
    saved = auth.load_token_cache(cache_path)
    assert saved["access_token"] == "new-at"
    assert saved["refresh_token"] == "rt-1"
    assert saved["expires_at"] == 999.0 + 3600
