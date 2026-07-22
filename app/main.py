import secrets
import time
from collections import Counter
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.arc.curve import build_target_curve
from app.arc.presets import PRESETS
from app.arc.sequencer import SequencingError, sequence
from app.config import Config, load_config
from app.llm.candidates import generate_candidates
from app.llm.prompt import Brief
from app.spotify import auth
from app.spotify.client import (
    add_tracks,
    create_playlist,
    resolve_candidates,
    unfollow_playlist,
)

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Event-Arc Playlist Builder")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

_config: Config | None = None
_pending_auth: dict[str, str] = {}


def _get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config


class SeedInput(BaseModel):
    title: str
    artist: str


class GenerateRequest(BaseModel):
    event_id: str
    seeds: list[SeedInput] = Field(default_factory=list, max_length=2)
    duration_min: float | None = Field(default=None, ge=15, le=240)
    vibe: str = ""
    discovery_mode: bool = False
    allow_explicit: bool = True


class SaveRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    uris: list[str] = Field(min_length=1)
    description: str = ""


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/login")
def login() -> RedirectResponse:
    config = _get_config()
    verifier = auth.generate_code_verifier()
    state = secrets.token_urlsafe(16)
    _pending_auth[state] = verifier
    challenge = auth.generate_code_challenge(verifier)
    return RedirectResponse(auth.build_authorize_url(config, state, challenge))


@app.get("/callback")
def callback(code: str = "", state: str = "", error: str = "") -> RedirectResponse:
    if error:
        raise HTTPException(
            status_code=400, detail=f"Spotify authorization failed: {error}"
        )

    verifier = _pending_auth.pop(state, None)
    if verifier is None:
        raise HTTPException(status_code=400, detail="OAuth state mismatch on callback.")

    token_response = auth.exchange_code_for_token(_get_config(), code, verifier)
    auth.save_token_cache(
        {
            "access_token": token_response["access_token"],
            "refresh_token": token_response["refresh_token"],
            "expires_at": time.time() + token_response["expires_in"],
        }
    )
    return RedirectResponse("/")


@app.get("/api/session")
def session() -> dict:
    try:
        token = auth.get_cached_access_token(_get_config())
    except httpx.HTTPError:
        token = None
    return {"authenticated": token is not None}


@app.get("/api/presets")
def presets() -> list[dict]:
    return [
        {
            "id": t.id,
            "label": t.label,
            "description": t.description,
            "default_duration_min": t.default_duration_min,
        }
        for t in PRESETS.values()
    ]


@app.post("/api/generate")
def generate(request: GenerateRequest) -> dict:
    return run_pipeline(request)


@app.post("/api/save")
def save(request: SaveRequest) -> dict:
    config = _get_config()
    token = auth.get_cached_access_token(config)
    if token is None:
        raise HTTPException(
            status_code=401, detail="Not logged in — visit /login first."
        )

    try:
        playlist = create_playlist(request.name, token, description=request.description)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Spotify save failed: {exc}")

    try:
        add_tracks(playlist["id"], request.uris, token)
    except httpx.HTTPError as exc:
        try:
            unfollow_playlist(playlist["id"], token)
        except httpx.HTTPError:
            pass  # cleanup is best-effort; surface the original failure
        raise HTTPException(status_code=502, detail=f"Spotify save failed: {exc}")

    return {
        "playlist_id": playlist["id"],
        "playlist_url": playlist.get("external_urls", {}).get("spotify", ""),
        "track_count": len(request.uris),
    }


def run_pipeline(request: GenerateRequest) -> dict:
    config = _get_config()
    token = auth.get_cached_access_token(config)
    if token is None:
        raise HTTPException(
            status_code=401, detail="Not logged in — visit /login first."
        )

    template = PRESETS.get(request.event_id)
    if template is None:
        raise HTTPException(
            status_code=422, detail=f"Unknown event_id '{request.event_id}'."
        )

    duration_min = request.duration_min or template.default_duration_min
    brief = Brief(
        seeds=tuple((s.title, s.artist) for s in request.seeds),
        vibe=request.vibe,
        discovery_mode=request.discovery_mode,
        allow_explicit=request.allow_explicit,
    )

    try:
        candidates = generate_candidates(brief, template, duration_min, config)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Candidate generation failed: {exc}"
        )

    try:
        stats = resolve_candidates(candidates, token)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"Spotify resolution failed: {exc}")

    resolved = [t for t in candidates if t.spotify_uri]
    if not resolved:
        raise HTTPException(
            status_code=502, detail="No candidates resolved on Spotify."
        )

    seed_keys = {(s.title.lower(), s.artist.lower()) for s in request.seeds}
    seeds = [t for t in resolved if (t.title.lower(), t.artist.lower()) in seed_keys]

    warning = None
    n_slots = len(build_target_curve(template, duration_min))
    artist_counts = Counter(t.artist for t in resolved)
    cap = template.constraints.max_same_artist_total
    usable = sum(min(count, cap) for count in artist_counts.values())
    if usable < n_slots:
        duration_min = usable * template.constraints.avg_track_len_min
        warning = (
            f"Only {usable} of {n_slots} needed tracks are usable after Spotify "
            f"resolution and artist-repeat limits; "
            f"playlist shortened to ~{round(duration_min)} min."
        )

    try:
        result = sequence(template, duration_min, resolved, seeds=seeds or None)
    except SequencingError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    rows = result.breakdown()
    for row, assignment in zip(rows, result.assignments):
        track = assignment.track
        row["resolved_title"] = track.resolved_title
        row["resolved_artist"] = track.resolved_artist
        row["rationale"] = track.rationale
        row["spotify_uri"] = track.spotify_uri

    return {
        "rows": rows,
        "total_cost": result.total_cost,
        "resolution": {
            "total": stats.total,
            "resolved": stats.resolved,
            "rate": stats.rate,
        },
        "warning": warning,
    }
