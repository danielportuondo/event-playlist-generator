import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx

from app.arc.models import CandidateTrack

BASE_URL = "https://api.spotify.com/v1"
RESOLUTION_CACHE_PATH = Path(".resolution_cache.json")
CALL_LOG_PATH = Path(".spotify_call_log.json")


def _load_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def record_call(path: Path | None = None) -> int:
    """Count one outgoing Spotify API request against today's total."""
    path = path or CALL_LOG_PATH
    today = time.strftime("%Y-%m-%d")
    log = _load_json(path)
    log[today] = log.get(today, 0) + 1
    path.write_text(json.dumps(log))
    return log[today]


def calls_today(path: Path | None = None) -> int:
    return _load_json(path or CALL_LOG_PATH).get(time.strftime("%Y-%m-%d"), 0)


@dataclass(frozen=True)
class TrackMatch:
    spotify_id: str
    spotify_uri: str
    resolved_title: str
    resolved_artist: str
    popularity: int


def _auth_headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def get_me(access_token: str, client: httpx.Client | None = None) -> dict:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        record_call()
        response = client.get(f"{BASE_URL}/me", headers=_auth_headers(access_token))
        response.raise_for_status()
        return response.json()
    finally:
        if owns_client:
            client.close()


@dataclass(frozen=True)
class ResolutionStats:
    total: int
    resolved: int
    rate: float


def _cache_key(title: str, artist: str) -> str:
    return f"{title.strip().lower()}|{artist.strip().lower()}"


# LLM-hallucinated tracks repeat across generations; caching the no-hit saves a
# search call each time. TTL'd so tracks Spotify adds later aren't missed forever.
NEGATIVE_TTL_SECONDS = 30 * 24 * 3600


def resolve_candidates(
    candidates: list[CandidateTrack],
    access_token: str,
    client: httpx.Client | None = None,
    cache_path: Path | None = None,
) -> ResolutionStats:
    owns_client = client is None
    client = client or httpx.Client()
    cache_path = cache_path or RESOLUTION_CACHE_PATH
    cache = _load_json(cache_path)
    cache_dirty = False
    seen_ids: set[str] = set()
    resolved = 0
    try:
        for track in candidates:
            key = _cache_key(track.title, track.artist)
            cached = cache.get(key)
            if cached is not None and "missed_at" in cached:
                if time.time() - cached["missed_at"] < NEGATIVE_TTL_SECONDS:
                    continue
                cached = None
            if cached is not None:
                match = TrackMatch(**cached)
            else:
                match = search_track(
                    track.title, track.artist, access_token, client=client
                )
                cache[key] = (
                    asdict(match) if match is not None else {"missed_at": time.time()}
                )
                cache_dirty = True
            if match is None or match.spotify_id in seen_ids:
                continue

            seen_ids.add(match.spotify_id)
            track.spotify_id = match.spotify_id
            track.spotify_uri = match.spotify_uri
            track.resolved_title = match.resolved_title
            track.resolved_artist = match.resolved_artist
            track.popularity = match.popularity
            resolved += 1
    finally:
        if cache_dirty:
            cache_path.write_text(json.dumps(cache))
        if owns_client:
            client.close()

    total = len(candidates)
    return ResolutionStats(
        total=total, resolved=resolved, rate=resolved / total if total else 0.0
    )


def create_playlist(
    name: str,
    access_token: str,
    description: str = "",
    client: httpx.Client | None = None,
) -> dict:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        # POST /users/{id}/playlists returns a bare 403 for this app; /me/playlists works.
        record_call()
        response = client.post(
            f"{BASE_URL}/me/playlists",
            json={"name": name, "description": description, "public": False},
            headers=_auth_headers(access_token),
        )
        response.raise_for_status()
        return response.json()
    finally:
        if owns_client:
            client.close()


def add_tracks(
    playlist_id: str,
    uris: list[str],
    access_token: str,
    client: httpx.Client | None = None,
) -> None:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        for start in range(0, len(uris), 100):
            # Feb 2026 API migration renamed /tracks -> /items; the old path 403s.
            record_call()
            response = client.post(
                f"{BASE_URL}/playlists/{playlist_id}/items",
                json={"uris": uris[start : start + 100]},
                headers=_auth_headers(access_token),
            )
            response.raise_for_status()
    finally:
        if owns_client:
            client.close()


def unfollow_playlist(
    playlist_id: str,
    access_token: str,
    client: httpx.Client | None = None,
) -> None:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        record_call()
        response = client.delete(
            f"{BASE_URL}/playlists/{playlist_id}/followers",
            headers=_auth_headers(access_token),
        )
        response.raise_for_status()
    finally:
        if owns_client:
            client.close()


def search_track(
    title: str,
    artist: str,
    access_token: str,
    client: httpx.Client | None = None,
    max_attempts: int = 4,
    sleep=time.sleep,
) -> TrackMatch | None:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        query = f'track:"{title}" artist:"{artist}"'
        for attempt in range(max_attempts):
            record_call()
            response = client.get(
                f"{BASE_URL}/search",
                params={"q": query, "type": "track", "limit": 5},
                headers=_auth_headers(access_token),
            )
            if response.status_code == 429 and attempt < max_attempts - 1:
                retry_after = int(response.headers.get("Retry-After", "1"))
                # Dev-mode quota exhaustion returns Retry-After in the hours;
                # only short waits are worth riding out in-request.
                if retry_after > 30:
                    break
                sleep(retry_after)
                continue
            break
        response.raise_for_status()
        items = response.json().get("tracks", {}).get("items", [])
        if not items:
            return None

        top = items[0]
        artists = top.get("artists") or [{}]
        return TrackMatch(
            spotify_id=top["id"],
            spotify_uri=top["uri"],
            resolved_title=top["name"],
            resolved_artist=artists[0].get("name", ""),
            popularity=top.get("popularity", 0),
        )
    finally:
        if owns_client:
            client.close()
