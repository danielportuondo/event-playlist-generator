from dataclasses import dataclass

import httpx

from app.arc.models import CandidateTrack

BASE_URL = "https://api.spotify.com/v1"


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


def resolve_candidates(
    candidates: list[CandidateTrack],
    access_token: str,
    client: httpx.Client | None = None,
) -> ResolutionStats:
    owns_client = client is None
    client = client or httpx.Client()
    seen_ids: set[str] = set()
    resolved = 0
    try:
        for track in candidates:
            match = search_track(track.title, track.artist, access_token, client=client)
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
        response = client.delete(
            f"{BASE_URL}/playlists/{playlist_id}/followers",
            headers=_auth_headers(access_token),
        )
        response.raise_for_status()
    finally:
        if owns_client:
            client.close()


def search_track(
    title: str, artist: str, access_token: str, client: httpx.Client | None = None
) -> TrackMatch | None:
    owns_client = client is None
    client = client or httpx.Client()
    try:
        query = f'track:"{title}" artist:"{artist}"'
        response = client.get(
            f"{BASE_URL}/search",
            params={"q": query, "type": "track", "limit": 5},
            headers=_auth_headers(access_token),
        )
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
