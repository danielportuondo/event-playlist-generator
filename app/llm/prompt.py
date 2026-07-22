from dataclasses import dataclass

from app.arc.models import EventTemplate, Phase


@dataclass(frozen=True)
class Brief:
    seeds: tuple[tuple[str, str], ...] = ()  # (title, artist) pairs
    vibe: str = ""
    discovery_mode: bool = False
    allow_explicit: bool = True


SYSTEM_PROMPT = (
    "You are a music curator. Given an event brief, propose candidate tracks. "
    "Respond with ONLY a JSON array of track objects — no prose, no markdown "
    "code fences, no commentary before or after. Each object must have exactly "
    "these fields: title (string), artist (string), energy (integer 0-100), "
    "tempo (integer BPM), valence (integer 0-100), rationale (string, at most "
    "15 words)."
)


def build_system_prompt() -> str:
    return SYSTEM_PROMPT


def build_user_prompt(
    brief: Brief,
    template: EventTemplate,
    n_candidates: int,
    focus_phase: Phase | None = None,
) -> str:
    lines: list[str] = []

    lines.append(f"Event: {template.label} — {template.description}")

    if brief.seeds:
        seed_list = ", ".join(f'"{title}" by {artist}' for title, artist in brief.seeds)
        lines.append(f"Seed songs: {seed_list}")
    else:
        lines.append("Seed songs: none given")

    lines.append("Phases (in order, energy/valence 0-100, tempo in BPM):")
    for phase in template.phases:
        lines.append(
            f"  - {phase.name}: energy {phase.energy[0]}-{phase.energy[1]}, "
            f"valence {phase.valence[0]}-{phase.valence[1]}, "
            f"tempo {phase.tempo[0]}-{phase.tempo[1]}"
        )

    if brief.vibe:
        lines.append(f"Vibe: {brief.vibe}")

    lines.append(
        "Mode: "
        + (
            "discovery (favor lesser-known tracks)"
            if brief.discovery_mode
            else "familiar (favor well-known tracks)"
        )
    )
    lines.append(
        "Explicit content: " + ("allowed" if brief.allow_explicit else "not allowed")
    )

    if focus_phase is not None:
        lines.append(
            f"Propose {n_candidates} candidate tracks for the '{focus_phase.name}' "
            f"phase specifically: energy {focus_phase.energy[0]}-{focus_phase.energy[1]}, "
            f"valence {focus_phase.valence[0]}-{focus_phase.valence[1]}, "
            f"tempo {focus_phase.tempo[0]}-{focus_phase.tempo[1]} BPM. "
            "Stay within those ranges."
        )
    else:
        lines.append(
            f"Propose {n_candidates} candidate tracks. Cover the full range of phases "
            "above — include low-energy as well as high-energy tracks, not just the "
            "loudest options."
        )

    if brief.seeds:
        lines.append(
            "Include each seed song itself as one of the candidates, with your "
            "own estimates for it."
        )

    return "\n".join(lines)
