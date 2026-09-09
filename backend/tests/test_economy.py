from app.core.blueprints import get_blueprint
from app.services import tasks, tutor
from app.services.schemas import GeneratedTask


def test_speaking_requirements_are_scoped():
    bp = get_blueprint("testdaf_digital_sprechen")
    bt = bp.task("informationen_erfragen")
    assert bt is not None
    scoped = tasks._task_blueprint(bp, bt)
    assert scoped.language_functions == ["informationen_erfragen"]
    assert len(scoped.tasks) == 1
    assert bp.language_functions != scoped.language_functions
    prompt, _ = tasks._generator_prompt(bp, bt, "C1", "Känguru-Bücher")
    assert "abwaegen" not in prompt
    candidate = GeneratedTask.model_construct(items=[], expected_answers={}, language_functions=[])
    assert tasks._apply_blueprint_parameters(candidate, bp, bt, "C1").language_functions == ["informationen_erfragen"]


def test_economy_skips_optional_tip(client, monkeypatch):
    learner = client.post("/workspaces", json={"name": "Economy test"}).json()["id"]
    assert client.patch(f"/learners/{learner}", json={"economy_mode": True}).status_code == 200
    monkeypatch.setattr(tutor.tts, "synthesize_safe", lambda *a, **kw: None)

    def forbidden(*a, **kw):
        raise AssertionError("Optional cloud tip must not run in economy mode")

    monkeypatch.setattr(tutor, "_pronunciation_tip_call", forbidden)
    _, tip, failures = tutor._post_steps("Hallo", object(), session_id="economy", learner_id=learner)
    assert tip is None and failures == []


def test_reopening_validated_task_does_not_call_provider(client):
    from tests.conftest import ledger_count

    created = client.post(
        "/tasks/generate",
        json={
            "blueprint_id": "testdaf_digital_sprechen",
            "task_type": "informationen_erfragen",
        },
    ).json()
    assert created["status"] == "ok"
    before = ledger_count()
    reopened = client.get("/tasks/" + created["task_id"])
    assert reopened.status_code == 200
    assert reopened.json()["task"] == created["task"]
    assert ledger_count() == before
