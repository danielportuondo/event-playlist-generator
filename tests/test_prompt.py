from app.arc.presets import PRESETS
from app.llm.prompt import Brief, build_system_prompt, build_user_prompt


def test_system_prompt_demands_json_only():
    prompt = build_system_prompt()
    assert "JSON" in prompt
    assert "no prose" in prompt.lower() or "only" in prompt.lower()
    assert "fence" in prompt.lower()


def test_user_prompt_includes_seeds():
    brief = Brief(seeds=(("Porch Light", "Nightbird"),), vibe="warm and cozy")
    template = PRESETS["dinner_party"]
    prompt = build_user_prompt(brief, template, n_candidates=20)

    assert "Porch Light" in prompt
    assert "Nightbird" in prompt
    assert "warm and cozy" in prompt


def test_user_prompt_includes_all_phase_names_and_ranges():
    brief = Brief()
    template = PRESETS["dinner_party"]
    prompt = build_user_prompt(brief, template, n_candidates=20)

    for phase in template.phases:
        assert phase.name in prompt
        assert str(phase.energy[0]) in prompt
        assert str(phase.energy[1]) in prompt


def test_user_prompt_includes_candidate_count_and_toggles():
    brief = Brief(discovery_mode=True, allow_explicit=False)
    template = PRESETS["dinner_party"]
    prompt = build_user_prompt(brief, template, n_candidates=37)

    assert "37" in prompt
    assert "discovery" in prompt.lower()
    assert "not allowed" in prompt.lower()


def test_user_prompt_handles_no_seeds():
    brief = Brief()
    template = PRESETS["dinner_party"]
    prompt = build_user_prompt(brief, template, n_candidates=20)

    assert "none given" in prompt.lower()


def test_user_prompt_requires_seed_inclusion_when_seeds_present():
    brief = Brief(seeds=(("Song A", "Artist A"),))
    template = PRESETS["dinner_party"]
    prompt = build_user_prompt(brief, template, n_candidates=20)

    assert "Include each seed song itself" in prompt


def test_user_prompt_omits_seed_inclusion_without_seeds():
    brief = Brief()
    template = PRESETS["dinner_party"]
    prompt = build_user_prompt(brief, template, n_candidates=20)

    assert "Include each seed song itself" not in prompt
