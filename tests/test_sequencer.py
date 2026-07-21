import pytest

from app.arc.curve import Slot, build_target_curve
from app.arc.models import ArcConstraints, CandidateTrack, EventTemplate, Phase
from app.arc.presets import PRESETS
from app.arc.sequencer import (
    SequencerWeights,
    SequencingError,
    compute_total_cost,
    fit_cost,
    greedy_fill,
    local_search,
    pin_seed_tracks,
    sequence,
)


def _track(title, artist, energy, tempo=100, valence=50):
    return CandidateTrack(
        title=title, artist=artist, energy=energy, tempo=tempo, valence=valence,
        rationale="test",
    )


def _template(phases, **overrides):
    constraints = ArcConstraints(
        max_energy_jump=overrides.get("max_energy_jump", 100),
        max_same_artist_total=overrides.get("max_same_artist_total", 10),
        no_same_artist_adjacent=overrides.get("no_same_artist_adjacent", False),
        avg_track_len_min=3.7,
    )
    return EventTemplate(
        id="test", label="Test", description="", default_duration_min=60,
        phases=phases, constraints=constraints,
    )


# --- cost function ---------------------------------------------------------

def test_fit_cost_is_absolute_energy_difference():
    slot = Slot(index=0, phase=Phase("p", 1.0, (0, 100), (0, 100), (60, 140)), target_energy=50)
    track = _track("t", "a", energy=70)
    assert fit_cost(track, slot) == 20


def test_compute_total_cost_sums_fit_only_when_no_penalties_triggered():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 50), Slot(1, phase, 60)]
    template = _template((phase,))
    assignments = {0: _track("t1", "a1", 55), 1: _track("t2", "a2", 65)}
    cost = compute_total_cost(assignments, slots, template, SequencerWeights())
    assert cost == 5 + 5


def test_compute_total_cost_penalizes_energy_jump_beyond_max():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 50), Slot(1, phase, 50)]
    template = _template((phase,), max_energy_jump=10)
    weights = SequencerWeights(lambda_smoothness=1.0, lambda_anti_clump=0, lambda_valence=0)
    assignments = {0: _track("t1", "a1", 50), 1: _track("t2", "a2", 80)}
    cost = compute_total_cost(assignments, slots, template, weights)
    assert cost == 50  # fit(0+30) + smoothness(excess 20 over max_jump 10)


def test_compute_total_cost_penalizes_same_artist_adjacent():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 50), Slot(1, phase, 50)]
    template = _template((phase,), max_energy_jump=100)
    weights = SequencerWeights(lambda_smoothness=0, lambda_anti_clump=40, lambda_valence=0)
    assignments = {0: _track("t1", "same", 50), 1: _track("t2", "same", 50)}
    cost = compute_total_cost(assignments, slots, template, weights)
    assert cost == 40


def test_compute_total_cost_penalizes_valence_out_of_phase_range():
    phase = Phase("p", 1.0, (0, 100), (40, 60), (60, 140))
    slots = [Slot(0, phase, 50)]
    template = _template((phase,))
    weights = SequencerWeights(lambda_smoothness=0, lambda_anti_clump=0, lambda_valence=1.0, valence_penalty_amount=20)
    assignments = {0: _track("t1", "a1", 50, valence=90)}
    cost = compute_total_cost(assignments, slots, template, weights)
    assert cost == 20


# --- hard constraints (greedy fill) -----------------------------------------

def test_greedy_fill_respects_max_same_artist_total():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 50), Slot(1, phase, 50)]
    template = _template((phase,), max_same_artist_total=1)
    candidates = [
        _track("t1", "dup", 50),
        _track("t2", "dup", 51),
        _track("t3", "other", 52),
    ]
    assignments = greedy_fill(slots, candidates, template)
    artists = [assignments[i].artist for i in sorted(assignments)]
    assert artists.count("dup") <= 1


def test_greedy_fill_respects_no_same_artist_adjacent():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 50), Slot(1, phase, 50)]
    template = _template((phase,), max_same_artist_total=2, no_same_artist_adjacent=True)
    candidates = [
        _track("t1", "dup", 50),
        _track("t2", "dup", 51),
        _track("t3", "other", 90),
    ]
    assignments = greedy_fill(slots, candidates, template)
    assert assignments[0].artist != assignments[1].artist


def test_greedy_fill_raises_when_no_eligible_candidate_remains():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 50), Slot(1, phase, 50)]
    template = _template((phase,), max_same_artist_total=1)
    candidates = [_track("t1", "only", 50)]
    with pytest.raises(SequencingError):
        greedy_fill(slots, candidates, template)


# --- seed pinning -----------------------------------------------------------

def test_pin_seed_tracks_assigns_best_fit_slot():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 10), Slot(1, phase, 90)]
    seed = _track("seed", "artist", energy=88)
    pinned = pin_seed_tracks([seed], slots)
    assert pinned == {1: seed}


def test_pin_seed_tracks_assigns_distinct_slots_for_multiple_seeds():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 10), Slot(1, phase, 90)]
    seed1 = _track("seed1", "a1", energy=88)
    seed2 = _track("seed2", "a2", energy=85)
    pinned = pin_seed_tracks([seed1, seed2], slots)
    assert set(pinned.keys()) == {0, 1}
    assert seed1 in pinned.values()
    assert seed2 in pinned.values()


# --- local search ------------------------------------------------------------

def test_local_search_improves_on_greedy_when_greedy_is_suboptimal():
    phase = Phase("p", 1.0, (0, 100), (0, 100), (60, 140))
    slots = [Slot(0, phase, 90), Slot(1, phase, 95)]
    template = _template((phase,))
    weights = SequencerWeights(lambda_smoothness=0, lambda_anti_clump=0, lambda_valence=0, max_iters=10)
    track_a = _track("A", "artistA", energy=95)
    track_b = _track("B", "artistB", energy=80)

    greedy_assignments = greedy_fill(slots, [track_a, track_b], template)
    greedy_cost = compute_total_cost(greedy_assignments, slots, template, weights)
    assert greedy_cost == 20  # A@slot0(5) + B@slot1(15)

    improved = local_search(dict(greedy_assignments), slots, template, weights)
    improved_cost = compute_total_cost(improved, slots, template, weights)
    assert improved_cost == 10  # B@slot0(10) + A@slot1(0)
    assert improved_cost < greedy_cost


# --- end-to-end sequence() over a real preset --------------------------------

def _dinner_party_candidates() -> list[CandidateTrack]:
    energies = [18, 22, 30, 38, 42, 48, 58, 62, 70, 76, 66, 50, 34, 26, 44]
    tracks = []
    for i, energy in enumerate(energies):
        tracks.append(
            _track(
                title=f"Song {i}",
                artist=f"Artist {i % 8}",
                energy=energy,
                tempo=80 + energy // 2,
                valence=55,
            )
        )
    return tracks


def test_sequence_dinner_party_produces_ramp_and_cool_shape():
    template = PRESETS["dinner_party"]
    candidates = _dinner_party_candidates()

    result = sequence(template, duration_min=37, candidates=candidates)

    slots = build_target_curve(template, duration_min=37)
    assert len(result.assignments) == len(slots)

    used_titles = [a.track.title for a in result.assignments]
    assert len(used_titles) == len(set(used_titles))

    energy_by_phase: dict[str, list[int]] = {}
    for a in result.assignments:
        energy_by_phase.setdefault(a.slot.phase.name, []).append(a.track.energy)

    def mean(values: list[int]) -> float:
        return sum(values) / len(values)

    arrival_mean = mean(energy_by_phase["arrival"])
    peak_mean = mean(energy_by_phase["peak"])
    winddown_mean = mean(energy_by_phase["winddown"])

    assert arrival_mean < peak_mean
    assert winddown_mean < peak_mean

    artist_counts: dict[str, int] = {}
    for a in result.assignments:
        artist_counts[a.track.artist] = artist_counts.get(a.track.artist, 0) + 1
    assert all(count <= template.constraints.max_same_artist_total for count in artist_counts.values())

    for a, b in zip(result.assignments, result.assignments[1:]):
        assert a.track.artist != b.track.artist


def test_sequence_raises_when_not_enough_candidates():
    template = PRESETS["dinner_party"]
    with pytest.raises(SequencingError):
        sequence(template, duration_min=37, candidates=_dinner_party_candidates()[:3])
