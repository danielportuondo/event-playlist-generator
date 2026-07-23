import pytest

from app.arc.models import ArcConstraints, EventTemplate, Phase


def _constraints(**overrides: object) -> ArcConstraints:
    base: dict[str, object] = {
        "max_energy_jump": 25,
        "max_same_artist_total": 2,
        "no_same_artist_adjacent": True,
        "avg_track_len_min": 3.7,
    }
    base.update(overrides)
    return ArcConstraints(**base)


def test_event_template_rejects_fractions_not_summing_to_one():
    bad_phases = (
        Phase("a", 0.5, (0, 50), (0, 50), (60, 120)),
        Phase("b", 0.6, (0, 50), (0, 50), (60, 120)),
    )
    with pytest.raises(ValueError, match="must be 1.0"):
        EventTemplate(
            id="broken",
            label="Broken",
            description="",
            default_duration_min=60,
            phases=bad_phases,
            constraints=_constraints(),
        )


def test_event_template_accepts_fractions_summing_to_one():
    phases = (
        Phase("a", 0.4, (0, 50), (0, 50), (60, 120)),
        Phase("b", 0.6, (0, 50), (0, 50), (60, 120)),
    )
    template = EventTemplate(
        id="ok",
        label="Ok",
        description="",
        default_duration_min=60,
        phases=phases,
        constraints=_constraints(),
    )
    assert template.id == "ok"
