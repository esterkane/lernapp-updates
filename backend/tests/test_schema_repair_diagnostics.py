import pytest
from app.core.prompts import load_prompt
from app.services import llm
from pydantic import BaseModel


class SmallOutput(BaseModel):
    count: int


def test_repair_schema_is_explicit_and_logs_exclude_content(monkeypatch, caplog):
    seen = []

    def fake(schema, messages):
        seen.append(messages)
        return '{"count":"private-learner-content"}', {"tokens_in": 1, "tokens_out": 1, "request_id": None}

    monkeypatch.setattr(llm, "_fake_response", fake)
    with pytest.raises(llm.LLMError):
        llm.complete(
            "assessment",
            [{"role": "user", "content": "test"}],
            SmallOutput,
            session_id="repair-diagnostics",
            learner_id="default",
            prompt_version="test",
        )
    assert len(seen) == 2
    assert "Verbindliches Schema" in seen[1][-1]["content"]
    assert "private-learner-content" not in caplog.text
    assert "int_parsing" in caplog.text
    assert "schema repair failed" in caplog.text


def test_assessment_prompt_matches_provider_schema():
    for name in ("writing_feedback", "speaking_feedback"):
        p = load_prompt(name)
        assert p.schema_name == "LLMRubricOutput"
        assert "JSON nach RubricResult" not in p.body
        assert "ganze Zahl 0 bis 4" in p.body
