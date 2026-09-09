from app.core import models
from app.services import assessment


def test_opt_out_overrides_legacy_progress_flag(client, monkeypatch):
    seen = []

    def assess(text, **kwargs):
        seen.append(kwargs["is_progress_point"])
        return {"ok": True}

    monkeypatch.setattr(assessment, "assess_writing", assess)
    assert (
        client.post(
            "/assess/writing", json={"learner_text": "Hallo", "is_progress_point": True, "second_opinion": "none"}
        ).status_code
        == 200
    )
    assert seen == [False]


def test_gemini_choice_is_scoped_and_never_falls_back_to_anthropic(client, monkeypatch):
    monkeypatch.setattr(models, "_has_key", lambda provider: True)
    seen = []

    def assess(text, **kwargs):
        seen.append(
            (kwargs["is_progress_point"], models.resolve_tier("validator").model, models.validator_candidates())
        )
        return {"ok": True}

    monkeypatch.setattr(assessment, "assess_writing", assess)
    r = client.post("/assess/writing", json={"learner_text": "Hallo", "second_opinion": "gemini"})
    assert r.status_code == 200, r.text
    assert seen[0][0] is True
    assert seen[0][1].startswith("gemini/")
    assert all(m.startswith("gemini/") for m in seen[0][2])
    assert models.requested_validator_provider.get() is None


def test_missing_gemini_fails_before_first_paid_assessment(client, monkeypatch):
    monkeypatch.setattr(models, "_has_key", lambda provider: provider == "openai")

    def forbidden(*a, **kw):
        raise AssertionError("No paid assessment should start")

    monkeypatch.setattr(assessment, "assess_writing", forbidden)
    assert client.post("/assess/writing", json={"learner_text": "Hallo", "second_opinion": "gemini"}).status_code == 400
