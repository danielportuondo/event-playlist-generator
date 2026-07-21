import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    gemini_api_key: str
    gemini_model: str


def load_config() -> Config:
    load_dotenv()

    gemini_api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    gemini_model = os.environ.get("GEMINI_MODEL", "").strip()

    missing = [
        name
        for name, value in (
            ("GEMINI_API_KEY", gemini_api_key),
            ("GEMINI_MODEL", gemini_model),
        )
        if not value
    ]
    if missing:
        raise ValueError(
            f"Missing required env var(s): {', '.join(missing)}. Set them in .env "
            "(see .env.example)."
        )

    return Config(gemini_api_key=gemini_api_key, gemini_model=gemini_model)
