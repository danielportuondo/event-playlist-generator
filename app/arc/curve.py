from dataclasses import dataclass
from itertools import pairwise

from app.arc.models import EventTemplate, Phase


@dataclass(frozen=True)
class Slot:
    index: int
    phase: Phase
    target_energy: float


def allocate_slot_counts(phases: tuple[Phase, ...], n_slots: int) -> list[int]:
    raw = [phase.fraction * n_slots for phase in phases]
    counts = [int(r) for r in raw]  # floor
    remainder_needed = n_slots - sum(counts)

    remainders = sorted(
        range(len(phases)),
        key=lambda i: (-(raw[i] - counts[i]), i),
    )
    for i in remainders[:remainder_needed]:
        counts[i] += 1
    return counts


def _phase_midpoint_anchors(phases: tuple[Phase, ...]) -> list[tuple[float, float]]:
    anchors = []
    cursor = 0.0
    for phase in phases:
        mid_position = cursor + phase.fraction / 2
        mid_energy = (phase.energy[0] + phase.energy[1]) / 2
        anchors.append((mid_position, mid_energy))
        cursor += phase.fraction
    return anchors


def _interpolate_energy(anchors: list[tuple[float, float]], position: float) -> float:
    if position <= anchors[0][0]:
        return anchors[0][1]
    if position >= anchors[-1][0]:
        return anchors[-1][1]
    for (pos_a, energy_a), (pos_b, energy_b) in pairwise(anchors):
        if pos_a <= position <= pos_b:
            span = pos_b - pos_a
            t = (position - pos_a) / span if span > 0 else 0.0
            return energy_a + t * (energy_b - energy_a)
    return anchors[-1][1]


def build_target_curve(template: EventTemplate, duration_min: float) -> list[Slot]:
    n_slots = max(1, round(duration_min / template.constraints.avg_track_len_min))
    counts = allocate_slot_counts(template.phases, n_slots)
    anchors = _phase_midpoint_anchors(template.phases)

    owning_phases: list[Phase] = []
    for phase, count in zip(template.phases, counts):
        owning_phases.extend([phase] * count)

    slots = []
    for i in range(n_slots):
        position = (i + 0.5) / n_slots
        target_energy = _interpolate_energy(anchors, position)
        slots.append(Slot(index=i, phase=owning_phases[i], target_energy=target_energy))
    return slots
