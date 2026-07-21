import json
import math
import re

from app.arc.curve import build_target_curve
from app.arc.models import CandidateTrack, EventTemplate
from app.config import Config
from app.llm.prompt import Brief, build_system_prompt, build_user_prompt

MAX_CALLS = 2
MAX_CANDIDATES = 60

_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text.strip()).strip()


def _coerce_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value)
    return None


def _validate_item(item: object) -> CandidateTrack | None:
    if not isinstance(item, dict):
        return None

    title = item.get("title")
    artist = item.get("artist")
    rationale = item.get("rationale")

    if not isinstance(title, str) or not title.strip():
        return None
    if not isinstance(artist, str) or not artist.strip():
        return None
    if not isinstance(rationale, str) or not rationale.strip():
        return None

    energy = _coerce_int(item.get("energy"))
    tempo = _coerce_int(item.get("tempo"))
    valence = _coerce_int(item.get("valence"))

    if energy is None or not (0 <= energy <= 100):
        return None
    if valence is None or not (0 <= valence <= 100):
        return None
    if tempo is None or tempo <= 0:
        return None

    return CandidateTrack(
        title=title.strip(),
        artist=artist.strip(),
        energy=energy,
        tempo=tempo,
        valence=valence,
        rationale=rationale.strip(),
    )


def _parse_response_text(text: str) -> list[CandidateTrack]:
    cleaned = _strip_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return []

    if not isinstance(data, list):
        return []

    candidates = []
    for item in data:
        track = _validate_item(item)
        if track is not None:
            candidates.append(track)
    return candidates


def _dedup_key(track: CandidateTrack) -> tuple[str, str]:
    return (track.title.strip().lower(), track.artist.strip().lower())


def _call_model(client, model: str, system_prompt: str, user_prompt: str) -> str:
    from google.genai import types

    response = client.models.generate_content(
        model=model,
        contents=user_prompt,
        config=types.GenerateContentConfig(system_instruction=system_prompt),
    )
    return response.text or ""


def generate_candidates(
    brief: Brief,
    template: EventTemplate,
    duration_min: float,
    config: Config,
    client: object | None = None,
) -> list[CandidateTrack]:
    n_slots = len(build_target_curve(template, duration_min))
    n_candidates = min(MAX_CANDIDATES, math.ceil(2.5 * n_slots))

    if client is None:
        from google import genai

        client = genai.Client(api_key=config.gemini_api_key)

    system_prompt = build_system_prompt()
    collected: list[CandidateTrack] = []
    seen: set[tuple[str, str]] = set()

    for _call_num in range(MAX_CALLS):
        remaining = n_candidates - len(collected)
        if remaining <= 0:
            break

        user_prompt = build_user_prompt(brief, template, n_candidates=remaining)
        raw = _call_model(client, config.gemini_model, system_prompt, user_prompt)

        for track in _parse_response_text(raw):
            key = _dedup_key(track)
            if key in seen:
                continue
            seen.add(key)
            collected.append(track)

        if len(collected) >= n_slots:
            break

    return collected
