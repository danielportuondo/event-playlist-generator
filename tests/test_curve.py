from app.arc.curve import allocate_slot_counts, build_target_curve
from app.arc.models import ArcConstraints, EventTemplate, Phase
from app.arc.presets import PRESETS


def _phases(*fractions: float) -> tuple[Phase, ...]:
    return tuple(
        Phase(f"p{i}", frac, (0, 100), (0, 100), (60, 140))
        for i, frac in enumerate(fractions)
    )


def test_allocate_slot_counts_sums_to_requested_total():
    phases = _phases(0.20, 0.30, 0.30, 0.20)
    counts = allocate_slot_counts(phases, 13)
    assert sum(counts) == 13


def test_allocate_slot_counts_uses_largest_remainder_tie_break():
    # raw = 2.6, 3.9, 3.9, 2.6 -> floor 2,3,3,2 (sum 10), 3 remainders to place.
    # remainders: .6, .9, .9, .6 -> largest first: idx1(.9), idx2(.9), then idx0(.6) beats idx3(.6) by order.
    phases = _phases(0.20, 0.30, 0.30, 0.20)
    counts = allocate_slot_counts(phases, 13)
    assert counts == [3, 4, 4, 2]


def test_build_target_curve_slot_count_matches_duration_over_avg_len():
    template = PRESETS["dinner_party"]
    slots = build_target_curve(template, duration_min=180)
    expected_n = round(180 / template.constraints.avg_track_len_min)
    assert len(slots) == expected_n


def test_build_target_curve_is_smooth_ramp_not_staircase():
    template = PRESETS["dinner_party"]
    slots = build_target_curve(template, duration_min=180)
    energies = [slot.target_energy for slot in slots]

    # Staircase would mean every slot within a phase shares the exact same value.
    # A smooth ramp means consecutive slots inside the same phase still differ.
    same_phase_pairs = [
        (a, b)
        for a, b in zip(slots, slots[1:])
        if a.phase.name == b.phase.name
    ]
    assert any(a.target_energy != b.target_energy for a, b in same_phase_pairs)

    # No single adjacent jump should equal the full gap between the two
    # phase midpoints it straddles (that would be a staircase step).
    for a, b in zip(slots, slots[1:]):
        if a.phase.name != b.phase.name:
            phase_a_mid = (a.phase.energy[0] + a.phase.energy[1]) / 2
            phase_b_mid = (b.phase.energy[0] + b.phase.energy[1]) / 2
            full_gap = abs(phase_b_mid - phase_a_mid)
            step = abs(b.target_energy - a.target_energy)
            if full_gap > 0:
                assert step < full_gap

    # Overall shape still ramps up then cools down for dinner_party.
    assert energies[0] < max(energies)
    assert energies[-1] < max(energies)


def test_build_target_curve_owning_phase_counts_match_allocation():
    template = PRESETS["dinner_party"]
    slots = build_target_curve(template, duration_min=180)
    n_slots = len(slots)
    expected_counts = allocate_slot_counts(template.phases, n_slots)
    actual_counts = [
        sum(1 for slot in slots if slot.phase.name == phase.name)
        for phase in template.phases
    ]
    assert actual_counts == expected_counts
