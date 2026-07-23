import itertools
from dataclasses import dataclass

from app.arc.curve import Slot, build_target_curve
from app.arc.models import CandidateTrack, EventTemplate


class SequencingError(Exception):
    pass


@dataclass(frozen=True)
class SequencerWeights:
    lambda_smoothness: float = 1.0
    lambda_anti_clump: float = 40.0
    lambda_valence: float = 0.3
    valence_penalty_amount: float = 20.0
    max_iters: int = 500


@dataclass(frozen=True)
class SlotAssignment:
    slot: Slot
    track: CandidateTrack


@dataclass(frozen=True)
class SequenceResult:
    assignments: list[SlotAssignment]
    total_cost: float

    def breakdown(self) -> list[dict]:
        return [
            {
                "slot_index": a.slot.index,
                "phase": a.slot.phase.name,
                "target_energy": a.slot.target_energy,
                "actual_energy": a.track.energy,
                "title": a.track.title,
                "artist": a.track.artist,
            }
            for a in self.assignments
        ]


def fit_cost(track: CandidateTrack, slot: Slot) -> float:
    return abs(track.energy - slot.target_energy)


def _valence_out_of_range(track: CandidateTrack, slot: Slot) -> bool:
    low, high = slot.phase.valence
    return not (low <= track.valence <= high)


def compute_total_cost(
    assignments: dict[int, CandidateTrack],
    slots: list[Slot],
    template: EventTemplate,
    weights: SequencerWeights,
) -> float:
    slot_by_index = {slot.index: slot for slot in slots}
    ordered_indices = sorted(assignments)

    fit = sum(fit_cost(assignments[i], slot_by_index[i]) for i in ordered_indices)

    smoothness = 0.0
    anti_clump = 0.0
    for a, b in itertools.pairwise(ordered_indices):
        track_a, track_b = assignments[a], assignments[b]
        delta = abs(track_a.energy - track_b.energy)
        smoothness += max(0, delta - template.constraints.max_energy_jump)
        if track_a.artist == track_b.artist:
            anti_clump += 1

    valence_penalty = 0.0
    for i in ordered_indices:
        slot = slot_by_index[i]
        track = assignments[i]
        if _valence_out_of_range(track, slot):
            valence_penalty += weights.valence_penalty_amount

    return (
        fit
        + weights.lambda_smoothness * smoothness
        + weights.lambda_anti_clump * anti_clump
        + weights.lambda_valence * valence_penalty
    )


def _artist_count(assignments: dict[int, CandidateTrack], artist: str) -> int:
    return sum(1 for track in assignments.values() if track.artist == artist)


def _violates_hard_constraints(
    slot_index: int,
    track: CandidateTrack,
    assignments: dict[int, CandidateTrack],
    template: EventTemplate,
) -> bool:
    constraints = template.constraints
    if _artist_count(assignments, track.artist) >= constraints.max_same_artist_total:
        return True
    if constraints.no_same_artist_adjacent:
        for neighbor_index in (slot_index - 1, slot_index + 1):
            neighbor = assignments.get(neighbor_index)
            if neighbor is not None and neighbor.artist == track.artist:
                return True
    return False


def _assignment_is_valid(
    assignments: dict[int, CandidateTrack], template: EventTemplate
) -> bool:
    constraints = template.constraints
    artist_counts: dict[str, int] = {}
    for track in assignments.values():
        artist_counts[track.artist] = artist_counts.get(track.artist, 0) + 1
        if artist_counts[track.artist] > constraints.max_same_artist_total:
            return False
    if constraints.no_same_artist_adjacent:
        for index, track in assignments.items():
            neighbor = assignments.get(index + 1)
            if neighbor is not None and neighbor.artist == track.artist:
                return False
    return True


def greedy_fill(
    slots: list[Slot],
    candidates: list[CandidateTrack],
    template: EventTemplate,
    pinned: dict[int, CandidateTrack] | None = None,
) -> dict[int, CandidateTrack]:
    assignments: dict[int, CandidateTrack] = dict(pinned or {})
    used_ids = {id(track) for track in assignments.values()}

    for slot in slots:
        if slot.index in assignments:
            continue
        best_track = None
        best_cost = float("inf")
        for track in candidates:
            if id(track) in used_ids:
                continue
            if _violates_hard_constraints(slot.index, track, assignments, template):
                continue
            cost = fit_cost(track, slot)
            if cost < best_cost:
                best_cost = cost
                best_track = track
        if best_track is None:
            raise SequencingError(f"No eligible candidate for slot {slot.index}")
        assignments[slot.index] = best_track
        used_ids.add(id(best_track))
    return assignments


def local_search(
    assignments: dict[int, CandidateTrack],
    slots: list[Slot],
    template: EventTemplate,
    weights: SequencerWeights,
    pinned_indices: set[int] | None = None,
) -> dict[int, CandidateTrack]:
    pinned_indices = pinned_indices or set()
    current = dict(assignments)
    current_cost = compute_total_cost(current, slots, template, weights)

    swappable = [i for i in current if i not in pinned_indices]

    for _ in range(weights.max_iters):
        improved = False
        for a, b in itertools.combinations(swappable, 2):
            candidate = dict(current)
            candidate[a], candidate[b] = candidate[b], candidate[a]

            if not _assignment_is_valid(candidate, template):
                continue

            new_cost = compute_total_cost(candidate, slots, template, weights)
            if new_cost < current_cost:
                current = candidate
                current_cost = new_cost
                improved = True
                break
        if not improved:
            break
    return current


def pin_seed_tracks(
    seeds: list[CandidateTrack], slots: list[Slot]
) -> dict[int, CandidateTrack]:
    pinned: dict[int, CandidateTrack] = {}
    used_slot_indices: set[int] = set()
    for seed in seeds:
        best_slot = min(
            (s for s in slots if s.index not in used_slot_indices),
            key=lambda s: fit_cost(seed, s),
        )
        pinned[best_slot.index] = seed
        used_slot_indices.add(best_slot.index)
    return pinned


def sequence(
    template: EventTemplate,
    duration_min: float,
    candidates: list[CandidateTrack],
    seeds: list[CandidateTrack] | None = None,
    weights: SequencerWeights | None = None,
) -> SequenceResult:
    weights = weights or SequencerWeights()
    slots = build_target_curve(template, duration_min)

    if len(candidates) < len(slots):
        raise SequencingError(
            f"Need at least {len(slots)} candidates for {len(slots)} slots, "
            f"got {len(candidates)}"
        )

    pinned = pin_seed_tracks(seeds, slots) if seeds else {}
    pinned_ids = {id(track) for track in pinned.values()}
    remaining_candidates = [t for t in candidates if id(t) not in pinned_ids]

    assignments = greedy_fill(slots, remaining_candidates, template, pinned=pinned)
    assignments = local_search(
        assignments, slots, template, weights, pinned_indices=set(pinned)
    )

    slot_by_index = {slot.index: slot for slot in slots}
    ordered = [
        SlotAssignment(slot=slot_by_index[i], track=assignments[i])
        for i in sorted(assignments)
    ]
    total_cost = compute_total_cost(assignments, slots, template, weights)
    return SequenceResult(assignments=ordered, total_cost=total_cost)
