from dataclasses import dataclass


@dataclass(frozen=True)
class Phase:
    name: str
    fraction: float
    energy: tuple[int, int]
    valence: tuple[int, int]
    tempo: tuple[int, int]


@dataclass(frozen=True)
class ArcConstraints:
    max_energy_jump: int
    max_same_artist_total: int
    no_same_artist_adjacent: bool
    avg_track_len_min: float


@dataclass(frozen=True)
class EventTemplate:
    id: str
    label: str
    description: str
    default_duration_min: int
    phases: tuple[Phase, ...]
    constraints: ArcConstraints

    def __post_init__(self) -> None:
        total = round(sum(phase.fraction for phase in self.phases), 6)
        if total != 1.0:
            raise ValueError(
                f"EventTemplate '{self.id}': phase fractions sum to {total}, must be 1.0"
            )


@dataclass
class CandidateTrack:
    title: str
    artist: str
    energy: int
    tempo: int
    valence: int
    rationale: str
    spotify_uri: str | None = None
    spotify_id: str | None = None
    resolved_title: str | None = None
    resolved_artist: str | None = None
    popularity: int | None = None
