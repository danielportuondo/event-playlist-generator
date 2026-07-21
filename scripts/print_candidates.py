"""M2 acceptance script: call the Gemini API for candidate tracks against a
preset arc and print the validated candidates as JSON. Needs GEMINI_API_KEY
and GEMINI_MODEL set in .env (see .env.example)."""

import json
from dataclasses import asdict

from app.arc.presets import PRESETS
from app.config import load_config
from app.llm.candidates import generate_candidates
from app.llm.prompt import Brief


def main() -> None:
    config = load_config()
    template = PRESETS["dinner_party"]
    duration_min = 60

    brief = Brief(
        seeds=(("Landslide", "Fleetwood Mac"),),
        vibe="warm, conversational, a little nostalgic",
        discovery_mode=False,
        allow_explicit=True,
    )

    candidates = generate_candidates(brief, template, duration_min, config)

    print(f"# {len(candidates)} candidates for {template.label} ({duration_min} min)")
    print(json.dumps([asdict(c) for c in candidates], indent=2))


if __name__ == "__main__":
    main()
