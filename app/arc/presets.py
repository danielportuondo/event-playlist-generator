from app.arc.models import ArcConstraints, EventTemplate, Phase


def _default_constraints(**overrides: object) -> ArcConstraints:
    base = dict(
        max_energy_jump=25,
        max_same_artist_total=2,
        no_same_artist_adjacent=True,
        avg_track_len_min=3.7,
    )
    base.update(overrides)
    return ArcConstraints(**base)


DINNER_PARTY = EventTemplate(
    id="dinner_party",
    label="Dinner Party",
    description="Warm and conversational to start, a lively middle, a soft landing.",
    default_duration_min=180,
    phases=(
        Phase("arrival", 0.20, (25, 40), (45, 70), (70, 100)),
        Phase("settling", 0.30, (35, 52), (45, 75), (80, 110)),
        Phase("peak", 0.30, (55, 78), (55, 90), (95, 128)),
        Phase("winddown", 0.20, (28, 45), (40, 70), (70, 100)),
    ),
    constraints=_default_constraints(),
)

HOUSE_PARTY = EventTemplate(
    id="house_party",
    label="House Party",
    description="Quick warm-up, a sustained high plateau, a slight late cool.",
    default_duration_min=180,
    phases=(
        Phase("warmup", 0.15, (45, 60), (55, 80), (100, 118)),
        Phase("plateau", 0.55, (70, 90), (60, 95), (118, 132)),
        Phase("late_cool", 0.30, (55, 75), (55, 85), (108, 126)),
    ),
    constraints=_default_constraints(max_same_artist_total=3),
)

WORKOUT_RUN = EventTemplate(
    id="workout_run",
    label="Workout / Run",
    description="Brief ramp, high steady, short high-intensity spike, cool-down.",
    default_duration_min=45,
    phases=(
        Phase("ramp", 0.15, (40, 60), (50, 80), (100, 120)),
        Phase("steady", 0.50, (65, 82), (55, 85), (120, 140)),
        Phase("spike", 0.15, (85, 100), (60, 95), (140, 175)),
        Phase("cooldown", 0.20, (30, 50), (45, 75), (90, 115)),
    ),
    constraints=_default_constraints(max_energy_jump=35, avg_track_len_min=3.5),
)

FOCUS_WORK = EventTemplate(
    id="focus_work",
    label="Focus Work",
    description="Low and flat throughout, tight energy band, low valence variance.",
    default_duration_min=120,
    phases=(
        Phase("settle_in", 0.20, (20, 32), (40, 60), (60, 90)),
        Phase("focus", 0.60, (22, 35), (40, 60), (60, 95)),
        Phase("ease_out", 0.20, (18, 30), (40, 60), (60, 90)),
    ),
    constraints=_default_constraints(max_energy_jump=15, max_same_artist_total=4),
)

ROAD_TRIP = EventTemplate(
    id="road_trip",
    label="Road Trip",
    description="Medium-high, gently undulating, no deep dips — keep momentum.",
    default_duration_min=180,
    phases=(
        Phase("set_off", 0.20, (50, 65), (55, 80), (95, 120)),
        Phase("cruise_1", 0.30, (55, 72), (55, 85), (100, 128)),
        Phase("cruise_2", 0.30, (55, 75), (55, 85), (100, 128)),
        Phase("home_stretch", 0.20, (50, 68), (55, 82), (95, 122)),
    ),
    constraints=_default_constraints(max_energy_jump=20),
)

WIND_DOWN = EventTemplate(
    id="wind_down",
    label="Wind Down",
    description="Steadily descending energy from medium to very low.",
    default_duration_min=60,
    phases=(
        Phase("medium", 0.25, (45, 60), (45, 70), (90, 115)),
        Phase("lower", 0.35, (30, 45), (40, 65), (75, 100)),
        Phase("low", 0.25, (18, 32), (35, 60), (60, 85)),
        Phase("very_low", 0.15, (5, 20), (30, 55), (50, 70)),
    ),
    constraints=_default_constraints(max_energy_jump=20, avg_track_len_min=4.0),
)

PRESETS: dict[str, EventTemplate] = {
    t.id: t
    for t in (
        DINNER_PARTY,
        HOUSE_PARTY,
        WORKOUT_RUN,
        FOCUS_WORK,
        ROAD_TRIP,
        WIND_DOWN,
    )
}
