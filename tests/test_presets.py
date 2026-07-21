from app.arc.presets import PRESETS


def test_every_preset_phase_fractions_sum_to_one():
    for preset in PRESETS.values():
        total = round(sum(phase.fraction for phase in preset.phases), 6)
        assert total == 1.0, f"{preset.id} phases sum to {total}, not 1.0"


def test_expected_preset_ids_present():
    expected = {
        "dinner_party",
        "house_party",
        "workout_run",
        "focus_work",
        "road_trip",
        "wind_down",
    }
    assert set(PRESETS.keys()) == expected
