import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str
    spotify_client_id: str
    spotify_redirect_uri: str
    # Optional: enables client-credentials search for anonymous visitors.
    spotify_client_secret: str = ""
    # Optional pair: persists call log + resolution cache to a private GitHub
    # Gist across restarts (Render's free-tier disk is ephemeral).
    gist_token: str = ""
    gist_id: str = ""


def load_config() -> Config:
    load_dotenv()

    gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    gemini_model = os.environ.get("GEMINI_MODEL", "").strip()
    spotify_client_id = os.environ.get("SPOTIFY_CLIENT_ID", "").strip()
    spotify_redirect_uri = os.environ.get("SPOTIFY_REDIRECT_URI", "").strip()
    spotify_client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET", "").strip()
    gist_token = os.environ.get("GIST_TOKEN", "").strip()
    gist_id = os.environ.get("GIST_ID", "").strip()

    missing = [
        name
        for name, value in (
            ("GEMINI_API_KEY", gemini_api_key),
            ("GEMINI_MODEL", gemini_model),
            ("SPOTIFY_CLIENT_ID", spotify_client_id),
            ("SPOTIFY_REDIRECT_URI", spotify_redirect_uri),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"Missing required env var(s): {', '.join(missing)}. Set them in .env "
            "(see .env.example)."
        )

    return Config(
        gemini_api_key=gemini_api_key,
        gemini_model=gemini_model,
        spotify_client_id=spotify_client_id,
        spotify_redirect_uri=spotify_redirect_uri,
        spotify_client_secret=spotify_client_secret,
        gist_token=gist_token,
        gist_id=gist_id,
    )
