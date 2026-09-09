from __future__ import annotations

import pytest
from app.core.prompts import all_prompt_names, list_versions, load_prompt
from app.services.schemas import SCHEMAS


def test_all_prompts_parse() -> None:
    for name in all_prompt_names():
        p = load_prompt(name)
        assert p.version.count(".") == 2
        if p.schema_name:
            assert p.schema_name in SCHEMAS, f"{name}: schema {p.schema_name} unknown"


def test_render_requires_all_placeholders() -> None:
    p = load_prompt("tutor_system")
    with pytest.raises(KeyError):
        p.render(learner_profile_block="x")
    out = p.render(learner_profile_block="Niveau: B2", rag_context="—")
    assert "Niveau: B2" in out and "{{" not in out


def test_highest_version_wins() -> None:
    vs = list_versions("writing_feedback")
    assert vs[-1].version == load_prompt("writing_feedback").version
