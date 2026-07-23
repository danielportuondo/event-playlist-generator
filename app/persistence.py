"""Gist-backed persistence for files that must survive ephemeral-disk restarts.

Opt-in via GIST_TOKEN + GIST_ID. Token caches are deliberately excluded:
the owner re-logs-in after a restart and the app token re-fetches itself.
"""

import logging
from pathlib import Path

import httpx

from app.config import Config
from app.spotify import client as spotify_client

SEED_CACHE_PATH = Path("resolution_cache.seed.json")
GIST_API = "https://api.github.com/gists"
TIMEOUT_SECONDS = 10.0

logger = logging.getLogger(__name__)


def _synced_paths() -> tuple[Path, ...]:
    # Read at call time: tests monkeypatch these module attributes.
    return (spotify_client.CALL_LOG_PATH, spotify_client.RESOLUTION_CACHE_PATH)


def _gist_name(path: Path) -> str:
    # Gists treat leading-dot files specially; store under plain names.
    return path.name.lstrip(".")


def _enabled(config: Config | None) -> bool:
    return config is not None and bool(config.gist_token and config.gist_id)


def _headers(config: Config) -> dict:
    return {
        "Authorization": f"Bearer {config.gist_token}",
        "Accept": "application/vnd.github+json",
    }


def hydrate(config: Config | None, client: httpx.Client | None = None) -> None:
    """Restore synced files from the Gist at boot; seed the resolution cache
    from the committed snapshot when nothing better exists."""
    if _enabled(config):
        owns_client = client is None
        client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        try:
            response = client.get(
                f"{GIST_API}/{config.gist_id}", headers=_headers(config)
            )
            response.raise_for_status()
            files = response.json().get("files", {})
            for path in _synced_paths():
                entry = files.get(_gist_name(path)) or {}
                content = entry.get("content")
                if entry.get("truncated") and entry.get("raw_url"):
                    # Gist API truncates inline content at 1MB.
                    raw = client.get(entry["raw_url"])
                    raw.raise_for_status()
                    content = raw.text
                if content:
                    path.write_text(content)
        except httpx.HTTPError as exc:
            logger.warning("Gist hydrate failed, continuing without it: %s", exc)
        finally:
            if owns_client:
                client.close()

    cache_path = spotify_client.RESOLUTION_CACHE_PATH
    if not cache_path.exists() and SEED_CACHE_PATH.exists():
        cache_path.write_text(SEED_CACHE_PATH.read_text())


def sync(config: Config | None, client: httpx.Client | None = None) -> None:
    """Best-effort write-through of synced files to the Gist; never raises."""
    if not _enabled(config):
        return

    files = {
        _gist_name(path): {"content": path.read_text()}
        for path in _synced_paths()
        if path.exists()
    }
    if not files:
        return

    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT_SECONDS)
    try:
        response = client.patch(
            f"{GIST_API}/{config.gist_id}",
            json={"files": files},
            headers=_headers(config),
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Gist sync failed, local state unaffected: %s", exc)
    finally:
        if owns_client:
            client.close()
