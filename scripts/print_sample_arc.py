"""M1 demo: sequence a hand-made list of fake candidates against the dinner_party
arc and print the ordering to stdout. No network, no Spotify, no LLM."""

from app.arc.models import CandidateTrack
from app.arc.presets import PRESETS
from app.arc.sequencer import sequence

# (title, artist, energy, valence, tempo) - hand-authored, spans low -> high -> low
FAKE_CANDIDATES = [
    ("Porch Light", "Nightbird", 20, 55, 78),
    ("Quiet Kitchen", "Low Tide Radio", 18, 50, 72),
    ("First Pour", "Coral Static", 28, 60, 85),
    ("Easy Chatter", "Marigold Hour", 32, 58, 88),
    ("Candle Wax", "Paper Weather", 24, 52, 80),
    ("Warm Static", "Half Moon Diner", 36, 62, 92),
    ("Settling In", "Velvet Antenna", 42, 65, 98),
    ("Second Glass", "Faded Postcard", 45, 68, 100),
    ("Loose Ends", "Glass Orchard", 48, 63, 104),
    ("Comfortable Noise", "Slow Comet", 40, 60, 96),
    ("Kitchen Table", "Kindred Static", 50, 70, 106),
    ("Getting Louder", "Amber Line", 58, 72, 112),
    ("Turn It Up", "Nightbird", 68, 78, 120),
    ("Dance Floor Kitchen", "Coral Static", 75, 82, 126),
    ("Peak Hour", "Marigold Hour", 80, 85, 128),
    ("Everybody's Talking", "Half Moon Diner", 72, 80, 122),
    ("Loud Laughing", "Velvet Antenna", 78, 83, 125),
    ("One More Song", "Slow Comet", 70, 79, 121),
    ("Last Dance", "Faded Postcard", 65, 75, 118),
    ("Coming Down", "Glass Orchard", 55, 68, 110),
    ("Sink Full of Glasses", "Kindred Static", 48, 60, 102),
    ("Slow Goodbye", "Amber Line", 40, 55, 94),
    ("Coat Rack", "Nightbird", 32, 50, 86),
    ("Last Call", "Paper Weather", 28, 48, 82),
    ("Porch Again", "Coral Static", 22, 45, 76),
    ("Quiet House", "Low Tide Radio", 16, 42, 70),
    ("Dishes", "Marigold Hour", 20, 44, 74),
    ("Goodnight", "Velvet Antenna", 14, 40, 68),
]


def build_fake_candidates() -> list[CandidateTrack]:
    return [
        CandidateTrack(
            title=title,
            artist=artist,
            energy=energy,
            tempo=tempo,
            valence=valence,
            rationale="hand-made fake candidate for M1 demo",
        )
        for title, artist, energy, valence, tempo in FAKE_CANDIDATES
    ]


def main() -> None:
    template = PRESETS["dinner_party"]
    duration_min = 60

    candidates = build_fake_candidates()
    result = sequence(template, duration_min, candidates)

    print(f"{template.label} ({template.description})")
    print(f"duration: {duration_min} min | slots: {len(result.assignments)} | "
          f"total cost: {result.total_cost:.1f}\n")
    print(f"{'#':>3}  {'phase':<10} {'target':>7} {'actual':>7}  {'artist':<18} title")
    print("-" * 78)
    for a in result.assignments:
        print(
            f"{a.slot.index:>3}  {a.slot.phase.name:<10} "
            f"{a.slot.target_energy:>7.1f} {a.track.energy:>7d}  "
            f"{a.track.artist:<18} {a.track.title}"
        )


if __name__ == "__main__":
    main()
