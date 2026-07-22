import base64
import hashlib
import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from app.config import Config

AUTHORIZE_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
# playlist-modify-public is required even for private playlists: Spotify creates
# API playlists with public=True regardless of the request body, and modifying
# them 403s with only the private scope.
SCOPE = "playlist-modify-private playlist-modify-public"
TOKEN_CACHE_PATH = Path(".token_cache.json")
APP_TOKEN_CACHE_PATH = Path(".app_token_cache.json")


def generate_code_verifier() -> str:
    return secrets.token_urlsafe(96)[:128]


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def build_authorize_url(config: Config, state: str, code_challenge: str) -> str:
    params = {
        "client_id": config.spotify_client_id,
        "response_type": "code",
        "redirect_uri": config.spotify_redirect_uri,
        "code_challenge_method": "S256",
        "code_challenge": code_challenge,
        "state": state,
        "scope": SCOPE,
    }
    return f"{AUTHORIZE_URL}?{urlencode(params)}"


def exchange_code_for_token(
    config: Config, code: str, code_verifier: str, client: httpx.Client | None = None
) -> dict:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": config.spotify_redirect_uri,
                "client_id": config.spotify_client_id,
                "code_verifier": code_verifier,
            },
        )
        response.raise_for_status()
        return response.json()
    finally:
        if owns_client:
            client.close()


def refresh_access_token(
    config: Config, refresh_token: str, client: httpx.Client | None = None
) -> dict:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": config.spotify_client_id,
            },
        )
        response.raise_for_status()
        return response.json()
    finally:
        if owns_client:
            client.close()


def fetch_app_token(config: Config, client: httpx.Client | None = None) -> dict:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        response = client.post(
            TOKEN_URL,
            data={"grant_type": "client_credentials"},
            auth=(config.spotify_client_id, config.spotify_client_secret),
        )
        response.raise_for_status()
        return response.json()
    finally:
        if owns_client:
            client.close()


def get_app_access_token(
    config: Config,
    client: httpx.Client | None = None,
    cache_path: Path = APP_TOKEN_CACHE_PATH,
    now: float | None = None,
) -> str | None:
    if not config.spotify_client_secret:
        return None

    now = time.time() if now is None else now
    cache = load_token_cache(cache_path)
    if cache is not None and not is_token_expired(cache["expires_at"], now):
        return cache["access_token"]

    token_response = fetch_app_token(config, client=client)
    access_token = token_response["access_token"]
    save_token_cache(
        {"access_token": access_token, "expires_at": now + token_response["expires_in"]},
        cache_path,
    )
    return access_token


def load_token_cache(path: Path = TOKEN_CACHE_PATH) -> dict | None:
    if not path.exists():
        return None
    return json.loads(path.read_text())


def save_token_cache(data: dict, path: Path = TOKEN_CACHE_PATH) -> None:
    path.write_text(json.dumps(data))


def is_token_expired(expires_at: float, now: float, buffer_seconds: int = 60) -> bool:
    return now >= expires_at - buffer_seconds


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        query = urlparse(self.path).query
        self.server.callback_params = {k: v[0] for k, v in parse_qs(query).items()}
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html><body>Login complete, you can close this window.</body></html>")

    def log_message(self, format: str, *args: object) -> None:
        pass


def capture_callback(port: int) -> dict:
    server = HTTPServer(("127.0.0.1", port), _CallbackHandler)
    server.handle_request()
    return server.callback_params


def get_cached_access_token(
    config: Config,
    client: httpx.Client | None = None,
    cache_path: Path = TOKEN_CACHE_PATH,
    now: float | None = None,
) -> str | None:
    now = time.time() if now is None else now
    cache = load_token_cache(cache_path)
    if cache is None:
        return None

    if not is_token_expired(cache["expires_at"], now):
        return cache["access_token"]

    token_response = refresh_access_token(config, cache["refresh_token"], client=client)
    access_token = token_response["access_token"]
    save_token_cache(
        {
            "access_token": access_token,
            "refresh_token": token_response.get("refresh_token", cache["refresh_token"]),
            "expires_at": now + token_response["expires_in"],
        },
        cache_path,
    )
    return access_token


def get_valid_access_token(
    config: Config,
    client: httpx.Client | None = None,
    cache_path: Path = TOKEN_CACHE_PATH,
    now: float | None = None,
    open_browser=webbrowser.open,
    capture_callback=capture_callback,
) -> str:
    now = time.time() if now is None else now
    cache = load_token_cache(cache_path)

    if cache is not None and not is_token_expired(cache["expires_at"], now):
        return cache["access_token"]

    if cache is not None:
        token_response = refresh_access_token(config, cache["refresh_token"], client=client)
        refresh_token = token_response.get("refresh_token", cache["refresh_token"])
    else:
        code_verifier = generate_code_verifier()
        code_challenge = generate_code_challenge(code_verifier)
        state = secrets.token_urlsafe(16)
        open_browser(build_authorize_url(config, state, code_challenge))

        port = urlparse(config.spotify_redirect_uri).port
        params = capture_callback(port)

        if params.get("state") != state:
            raise ValueError("OAuth state mismatch on callback — aborting login.")
        if "error" in params:
            raise ValueError(f"Spotify authorization failed: {params['error']}")

        token_response = exchange_code_for_token(
            config, params["code"], code_verifier, client=client
        )
        refresh_token = token_response["refresh_token"]

    access_token = token_response["access_token"]
    expires_at = now + token_response["expires_in"]
    save_token_cache(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_at,
        },
        cache_path,
    )
    return access_token
