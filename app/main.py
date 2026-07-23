import secrets
import time
from collections import Counter
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import persistence
from app.arc.curve import build_target_curve
from app.arc.presets import PRESETS
from app.arc.sequencer import SequencingError, sequence
from app.config import Config, load_config
from app.llm.candidates import generate_candidates
from app.llm.prompt import Brief
from app.spotify import auth
from app.spotify.client import (
    add_tracks,
    calls_today,
    create_playlist,
    mark_day_exhausted,
    resolve_candidates,
    unfollow_playlist,
)

STATIC_DIR = Path(__file__).parent / "static"

# Observed dev-mode lockout at ~230 calls/day; ceilings keep an ~80-call margin,
# with 20 calls reserved above the visitor ceiling for the owner's own use.
WORST_CASE_CALLS_PER_GENERATION = 30
VISITOR_DAILY_CEILING = 130
OWNER_DAILY_CEILING = 150

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        config = _get_config()
    except ValueError:
        config = None  # missing env vars keep surfacing on first request, not at boot
    persistence.hydrate(config)
    yield


app = FastAPI(title="Event-Arc Playlist Builder", lifespan=lifespan)
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


@app.get("/healthz")
def healthz() -> dict:
    # Keep-alive ping target (UptimeRobot); must cost zero external calls.
    return {"status": "ok"}


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
    config = _get_config()
    try:
        token = auth.get_cached_access_token(config)
    except httpx.HTTPError:
        token = None
    return {
        "authenticated": token is not None,
        "visitor_live": bool(config.spotify_client_secret),
        "spotify_calls_today": calls_today(),
    }


@app.get("/api/presets")
def presets() -> list[dict]:
    return [
        {
            "id": t.id,
            "label": t.label,
            "description": t.description,
            "default_duration_min": t.default_duration_min,
            "avg_track_len_min": t.constraints.avg_track_len_min,
            "phases": [
                {"name": p.name, "fraction": p.fraction, "energy": list(p.energy)}
                for p in t.phases
            ],
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

    persistence.sync(config)

    return {
        "playlist_id": playlist["id"],
        "playlist_url": playlist.get("external_urls", {}).get("spotify", ""),
        "track_count": len(request.uris),
    }


def run_pipeline(request: GenerateRequest) -> dict:
    config = _get_config()
    try:
        token = auth.get_cached_access_token(config)
    except httpx.HTTPError:
        token = None
    is_visitor = False
    if token is None:
        # Anonymous visitors: search-only client-credentials token (no allowlist cap).
        try:
            token = auth.get_app_access_token(config)
            is_visitor = token is not None
        except httpx.HTTPError:
            token = None
    if token is None:
        raise HTTPException(
            status_code=401, detail="Not logged in — visit /login first."
        )

    template = PRESETS.get(request.event_id)
    if template is None:
        raise HTTPException(
            status_code=422, detail=f"Unknown event_id '{request.event_id}'."
        )

    ceiling = VISITOR_DAILY_CEILING if is_visitor else OWNER_DAILY_CEILING
    if calls_today() + WORST_CASE_CALLS_PER_GENERATION > ceiling:
        raise HTTPException(
            status_code=503,
            detail="Daily Spotify budget for this app is used up — try again tomorrow.",
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
    except Exception as exc:  # noqa: BLE001 — genai SDK raises many types; all map to 502
        raise HTTPException(
            status_code=502, detail=f"Candidate generation failed: {exc}"
        )

    try:
        stats = resolve_candidates(candidates, token)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            retry_after = int(exc.response.headers.get("Retry-After", "0"))
            if retry_after > 3600:
                # Hours-long lockout means the daily quota is gone; trip the
                # budget guard so later requests stop hitting Spotify at all.
                mark_day_exhausted()
                persistence.sync(config)
            hours = max(1, round(retry_after / 3600))
            raise HTTPException(
                status_code=503,
                detail=(
                    "Spotify's rate limit for this app was reached — "
                    f"try again in about {hours} hour{'s' if hours > 1 else ''}."
                ),
            )
        raise HTTPException(status_code=502, detail=f"Spotify resolution failed: {exc}")
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

    persistence.sync(config)

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
