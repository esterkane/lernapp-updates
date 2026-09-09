"""Desktop workspace boundaries, without provider network calls."""

from app.core.db import db_session
from app.db.base import Learner


def test_workspace_create_and_list(client):
    response = client.post("/workspaces", json={"name": "Business Deutsch", "level": "C1"})
    assert response.status_code == 201
    item = response.json()
    assert item["name"] == "Business Deutsch"
    assert item["id"] != "default"
    assert item in client.get("/workspaces").json()
    assert client.post("/workspaces", json={"name": "   "}).status_code == 422


def test_workspace_rejects_foreign_session_and_learner(client):
    a = client.post("/workspaces", json={"name": "A"}).json()["id"]
    b = client.post("/workspaces", json={"name": "B"}).json()["id"]
    headers = {"X-Lernapp-Workspace": a}
    sid = client.post("/sessions", json={"learner_id": b}).json()["session_id"]
    assert client.get(f"/sessions/{sid}", headers=headers).status_code == 404
    assert client.post(f"/sessions/{sid}/turn", json={"text": "Hallo"}, headers=headers).status_code == 404
    assert client.get("/provider-credentials", params={"learner_id": b}, headers=headers).status_code == 403
    assert client.get(f"/learners/{b}/export", headers=headers).status_code == 403
    assert client.get("/usage/summary", headers=headers).status_code == 200
    assert client.get("/usage/summary", headers={"X-Lernapp-Workspace": "missing"}).status_code == 404


def test_workspace_credentials_do_not_inherit_default_env(client, monkeypatch):
    from app.core import credentials
    from app.core.config import get_settings

    b = client.post("/workspaces", json={"name": "BYOK"}).json()["id"]
    monkeypatch.setattr(get_settings(), "openai_api_key", "sk-test-private-environment-value")
    assert credentials.api_key_for(b, "openai") is None
    assert not credentials.get_view(b, "openai").connected
    assert not credentials.has_key(b, "openai")


def test_workspace_defaults_and_task_scope(client):
    a = client.post("/workspaces", json={"name": "Task owner"}).json()["id"]
    headers = {"X-Lernapp-Workspace": a}
    response = client.post("/sessions", json={"kind": "tutor"}, headers=headers)
    assert response.status_code == 200, response.text
    sid = response.json()["session_id"]
    assert client.get(f"/sessions/{sid}", headers=headers).status_code == 200
    assert client.get("/tasks", headers=headers).json() == []
    with db_session() as db:
        from app.db.base import Session

        assert db.get(Session, sid).learner_id == a
        assert db.get(Learner, a) is not None


def test_missing_personal_key_never_calls_sdk(client, monkeypatch):
    import pytest
    from app.core import credentials

    b = client.post("/workspaces", json={"name": "No fallback"}).json()["id"]
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-owner-only-value")
    with pytest.raises(credentials.CredentialError):
        credentials.require_workspace_key(b, "openai")


def test_task_generation_and_export_are_scoped(client):
    a = client.post("/workspaces", json={"name": "Task A"}).json()["id"]
    b = client.post("/workspaces", json={"name": "Task B"}).json()["id"]
    from app.db.base import Task

    with db_session() as db:
        task = Task(
            learner_id=a,
            blueprint_id="fixture",
            blueprint_version="1",
            skill="lesen",
            task_type="fixture",
            level="B2",
            payload={"title": "private"},
            generator_model="fake",
            validator_model="fake",
            prompt_versions={},
        )
        db.add(task)
        db.flush()
        tid = task.id
    assert client.get("/tasks", headers={"X-Lernapp-Workspace": b}).json() == []
    assert client.get(f"/tasks/{tid}", headers={"X-Lernapp-Workspace": b}).status_code == 404
    assert (
        client.post(
            "/assess/writing", json={"task_id": tid, "learner_text": "Hallo"}, headers={"X-Lernapp-Workspace": b}
        ).status_code
        == 404
    )
    assert client.get("/tasks", headers={"X-Lernapp-Workspace": a}).json()[0]["task_id"] == tid
    from app.services.privacy import delete_learner, export_files

    assert tid in export_files(a)["tasks.json"]
    assert tid not in export_files(b)["tasks.json"]
    delete_learner(a)
    with db_session() as db:
        assert db.get(Task, tid) is None


def test_workspace_switch_clears_old_chat_state(monkeypatch):
    from lernapp_ui import api
    from streamlit.testing.v1 import AppTest

    monkeypatch.setattr(
        api,
        "list_workspaces",
        lambda: [{"id": "default", "name": "Main", "level": "B2"}, {"id": "other", "name": "Work", "level": "C1"}],
    )
    app = AppTest.from_string("""
import streamlit as st
from lernapp_ui.workspace_ui import workspace_selector
from lernapp_ui import api
workspace_selector()
st.write(api._current_learner())
""").run()
    app.session_state["old_chat"] = "private old chat"
    app.selectbox(key="_workspace_select").select("other").run()
    assert not app.exception
    assert app.session_state["_workspace_id"] == "other"
    assert "old_chat" not in app.session_state
    assert app.markdown[-1].value == "other"


def test_workspace_creation_selects_new_workspace(monkeypatch):
    from lernapp_ui import api
    from streamlit.testing.v1 import AppTest

    rows = [{"id": "default", "name": "Main", "level": "B2"}]
    monkeypatch.setattr(api, "list_workspaces", lambda: list(rows))

    def create(name, level):
        row = {"id": "created", "name": name, "level": level}
        rows.append(row)
        return row

    monkeypatch.setattr(api, "create_workspace", create)
    app = AppTest.from_string("""
from lernapp_ui.workspace_ui import workspace_selector
workspace_selector()
""").run()
    app.text_input[0].set_value("Business")
    app.button[0].click().run()
    assert not app.exception
    assert app.session_state["_workspace_id"] == "created"


def test_background_voice_work_inherits_workspace(monkeypatch):
    from app.core.workspaces import current_workspace, selected_workspace
    from app.services import tutor

    seen = []
    monkeypatch.setattr(tutor.tts, "synthesize_safe", lambda *a, **k: seen.append(current_workspace()))
    token = selected_workspace.set("voice-workspace")
    try:
        tutor._post_steps("Hallo", None, session_id="fake", learner_id="voice-workspace")
    finally:
        selected_workspace.reset(token)
    assert seen == ["voice-workspace"]


def test_launcher_generates_private_api_token(tmp_path, monkeypatch):
    from lernapp_launcher import cli

    monkeypatch.delenv("LERNAPP_API_TOKEN", raising=False)
    monkeypatch.setattr(cli, "run_dir", lambda: tmp_path)
    env = cli._child_env(8012, 8512)
    token = env["LERNAPP_API_TOKEN"]
    assert len(token) >= 32
    token_file = tmp_path / "api-token"
    assert token_file.read_text() == token
    assert token_file.stat().st_mode & 0o077 == 0
    assert env["API_HOST"] == "127.0.0.1"
    assert cli._child_env(8012, 8512)["LERNAPP_API_TOKEN"] != token


def test_workspace_keys_and_settings_follow_request_context(client):
    from app.core import credentials

    credentials.ensure_encryption_key()
    a = client.post("/workspaces", json={"name": "Connected"}).json()["id"]
    b = client.post("/workspaces", json={"name": "Disconnected"}).json()["id"]
    credentials.set_credential(a, "openai", "sk-test-workspace-key-abcdefghijklABC")
    response = client.get("/settings", headers={"X-Lernapp-Workspace": a})
    assert response.status_code == 200
    assert response.json()["providers_configured"]["openai"]
    other = client.get("/settings", headers={"X-Lernapp-Workspace": b}).json()
    assert not other["providers_configured"]["openai"]
    assert other["secrets"]["OPENAI_API_KEY"] is None


def test_context_does_not_leak_between_parallel_requests(client):
    from concurrent.futures import ThreadPoolExecutor

    a = client.post("/workspaces", json={"name": "Concurrent A"}).json()["id"]
    b = client.post("/workspaces", json={"name": "Concurrent B"}).json()["id"]

    def create(lid):
        response = client.post("/sessions", json={"kind": "tutor"}, headers={"X-Lernapp-Workspace": lid})
        assert response.status_code == 200
        with db_session() as db:
            from app.db.base import Session

            return db.get(Session, response.json()["session_id"]).learner_id

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert list(executor.map(create, [a, b, a, b])) == [a, b, a, b]
