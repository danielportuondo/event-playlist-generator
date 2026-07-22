import json

from app.arc.curve import build_target_curve
from app.arc.presets import PRESETS
from app.config import Config
from app.llm.candidates import generate_candidates
from app.llm.prompt import Brief

TEMPLATE = PRESETS["dinner_party"]
CONFIG = Config(
    gemini_api_key="test-key",
    gemini_model="test-model",
    spotify_client_id="test-client-id",
    spotify_redirect_uri="http://127.0.0.1:8000/callback",
)


class _FakeResponse:
    def __init__(self, text):
        self.text = text


class _FakeModels:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse(self._responses.pop(0))


class FakeClient:
    def __init__(self, responses):
        self.models = _FakeModels(responses)


def _track(title, artist, energy=50, tempo=100, valence=50, rationale="fits the vibe"):
    return {
        "title": title,
        "artist": artist,
        "energy": energy,
        "tempo": tempo,
        "valence": valence,
        "rationale": rationale,
    }


def _n_slots(duration_min):
    return len(build_target_curve(TEMPLATE, duration_min))


def test_valid_batch_parses_in_a_single_call():
    duration_min = 8  # small template -> few slots, one batch should suffice
    n_slots = _n_slots(duration_min)
    tracks = [_track(f"Song {i}", f"Artist {i}") for i in range(n_slots + 2)]
    client = FakeClient([json.dumps(tracks)])

    result = generate_candidates(Brief(), TEMPLATE, duration_min, CONFIG, client=client)

    assert len(result) == len(tracks)
    assert len(client.models.calls) == 1


def test_strips_fences_and_drops_malformed_elements():
    duration_min = 8
    n_slots = _n_slots(duration_min)
    valid = [_track(f"Song {i}", f"Artist {i}") for i in range(n_slots + 1)]
    malformed = [
        {
            "title": "No Energy",
            "artist": "X",
            "tempo": 100,
            "valence": 50,
            "rationale": "r",
        },
        {
            "title": "Bad Range",
            "artist": "Y",
            "energy": 999,
            "tempo": 100,
            "valence": 50,
            "rationale": "r",
        },
        {
            "title": "",
            "artist": "Z",
            "energy": 50,
            "tempo": 100,
            "valence": 50,
            "rationale": "r",
        },
    ]
    raw = "```json\n" + json.dumps(valid + malformed) + "\n```"
    client = FakeClient([raw])

    result = generate_candidates(Brief(), TEMPLATE, duration_min, CONFIG, client=client)

    assert len(result) == len(valid)
    assert len(client.models.calls) == 1


def test_coerces_float_numeric_fields():
    duration_min = 8
    n_slots = _n_slots(duration_min)
    tracks = [
        _track(f"Song {i}", f"Artist {i}", energy=50.0, tempo=100.0, valence=50.0)
        for i in range(n_slots + 1)
    ]
    client = FakeClient([json.dumps(tracks)])

    result = generate_candidates(Brief(), TEMPLATE, duration_min, CONFIG, client=client)

    assert len(result) == len(tracks)
    assert all(isinstance(t.energy, int) for t in result)


def test_under_yield_triggers_exactly_one_retry():
    duration_min = 20
    n_slots = _n_slots(duration_min)
    assert n_slots >= 4

    first_batch = [_track("First", "ArtistA"), _track("Second", "ArtistB")]
    second_batch = [_track(f"Extra {i}", f"ArtistExtra{i}") for i in range(n_slots)]
    client = FakeClient([json.dumps(first_batch), json.dumps(second_batch)])

    result = generate_candidates(Brief(), TEMPLATE, duration_min, CONFIG, client=client)

    assert len(client.models.calls) == 2
    assert len(result) >= n_slots


def test_never_exceeds_max_calls_even_if_still_short():
    duration_min = 20
    n_slots = _n_slots(duration_min)

    batches = [json.dumps([_track(f"Song {i}", f"Artist{i}")]) for i in range(4)]
    client = FakeClient(batches)

    result = generate_candidates(Brief(), TEMPLATE, duration_min, CONFIG, client=client)

    assert len(client.models.calls) == 4
    assert len(result) == 4
    assert len(result) < n_slots


def test_dedupes_case_insensitive_across_calls():
    duration_min = 20
    n_slots = _n_slots(duration_min)

    first_batch = [_track("Same Song", "Same Artist")]
    second_batch = [_track("same song", "same artist")] + [
        _track(f"Unique {i}", f"UniqueArtist{i}") for i in range(n_slots)
    ]
    client = FakeClient([json.dumps(first_batch), json.dumps(second_batch)])

    result = generate_candidates(Brief(), TEMPLATE, duration_min, CONFIG, client=client)

    titles = [(t.title.lower(), t.artist.lower()) for t in result]
    assert titles.count(("same song", "same artist")) == 1


class _PhaseAwareModels:
    """Routes responses by the phase named in the prompt; order-independent
    so it works under the concurrent phase fan-out."""

    def __init__(self, responses_by_phase, fallback="[]"):
        self._by_phase = responses_by_phase
        self._fallback = fallback
        self.calls: list[dict] = []

    def generate_content(self, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        for name, response in self._by_phase.items():
            if f"'{name}' phase" in contents:
                return _FakeResponse(response)
        return _FakeResponse(self._fallback)


class PhaseAwareClient:
    def __init__(self, responses_by_phase, fallback="[]"):
        self.models = _PhaseAwareModels(responses_by_phase, fallback)


def _phase_slot_counts(duration_min):
    counts = {}
    for slot in build_target_curve(TEMPLATE, duration_min):
        counts[slot.phase.name] = counts.get(slot.phase.name, 0) + 1
    return counts


def test_long_duration_fans_out_one_call_per_phase():
    import math

    duration_min = 180
    n_slots = _n_slots(duration_min)
    counts = _phase_slot_counts(duration_min)
    responses = {
        name: json.dumps(
            [
                _track(f"{name} song {i}", f"{name} artist {i}")
                for i in range(math.ceil(2.5 * count))
            ]
        )
        for name, count in counts.items()
    }
    client = PhaseAwareClient(responses)

    result = generate_candidates(Brief(), TEMPLATE, duration_min, CONFIG, client=client)

    assert len(client.models.calls) == len(counts)
    assert all("phase specifically" in c["contents"] for c in client.models.calls)
    assert len(result) >= n_slots
    result_phases = {t.title.split(" song")[0] for t in result}
    assert result_phases == set(counts)


def test_phase_fanout_shortfall_triggers_capped_topup():
    duration_min = 180
    counts = _phase_slot_counts(duration_min)
    thin = {
        name: json.dumps(
            [_track(f"{name} song {i}", f"{name} artist {i}") for i in range(2)]
        )
        for name in counts
    }
    topup = json.dumps([_track(f"Topup {i}", f"TopupArtist {i}") for i in range(60)])
    client = PhaseAwareClient(thin, fallback=topup)

    result = generate_candidates(Brief(), TEMPLATE, duration_min, CONFIG, client=client)

    topup_calls = [
        c for c in client.models.calls if "phase specifically" not in c["contents"]
    ]
    assert len(topup_calls) == 1
    assert "Propose 60 candidate tracks" in topup_calls[0]["contents"]
    assert len(result) == 2 * len(counts) + 60
