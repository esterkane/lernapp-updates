"""Negotiation roleplay (docs/api.md "Roleplay", ADR-0013, product rule 7) with fake backends."""

from __future__ import annotations

import base64
import json

import yaml
from app.core import ledger, rubrics
from app.services import results, roleplay
from app.services.fakes import silent_wav

from tests.conftest import ledger_count

CATEGORIES = {"gehalt", "einkauf", "vertrieb", "projektumfang", "beschwerde", "meeting"}
HIDDEN_KEYS = ("hidden_targets", "concession_ladder", "escalation_triggers")


def _start(client, learner_id: str, scenario_id: str = "einkauf_1_rahmenvertrag", voice: bool = False) -> dict:  # type: ignore[no-untyped-def]
    r = client.post("/roleplay/start", json={"scenario_id": scenario_id, "learner_id": learner_id, "voice": voice})
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _hidden_targets_from_yaml(scenario_id: str) -> dict[str, str]:
    raw = yaml.safe_load((roleplay.scenarios_dir() / f"{scenario_id}.yaml").read_text(encoding="utf-8"))
    return dict(raw["counterpart"]["hidden_targets"])


# ---------------------------------------------------------------- scenarios


def test_scenarios_load_and_cover_all_categories() -> None:
    scenarios = roleplay.load_scenarios()
    assert len(scenarios) >= 10
    assert {s.category for s in scenarios} == CATEGORIES
    assert len({s.id for s in scenarios}) == len(scenarios)
    for s in scenarios:
        assert s.counterpart.hidden_targets and s.counterpart.concession_ladder
        assert s.learner.goals and s.max_turns >= 4
    assert roleplay.seed_scenarios() == len(scenarios)
    assert roleplay.seed_scenarios() == len(scenarios)  # upsert is idempotent


def test_scenario_list_hides_counterpart_secrets(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/roleplay/scenarios")
    assert r.status_code == 200, r.text
    rows = r.json()
    assert len(rows) >= 10 and {row["category"] for row in rows} == CATEGORIES
    dumped = json.dumps(rows)
    for key in HIDDEN_KEYS:
        assert key not in dumped
    row = rows[0]
    assert set(row) >= {"id", "title_de", "category", "difficulty", "description_de", "learner", "max_turns", "voice"}
    assert set(row["learner"]) == {"role", "goals", "constraints", "batna"}
    assert set(row["counterpart"]) == {"role", "personality"}

    r = client.get("/roleplay/scenarios", params={"category": "gehalt", "difficulty": 2})
    assert r.status_code == 200 and [x["id"] for x in r.json()] == ["gehalt_2_gegenangebot"]
    r = client.get("/roleplay/scenarios", params={"category": "einkauf"})
    assert [x["difficulty"] for x in r.json()] == [1, 2, 3]
    assert client.get("/roleplay/scenarios", params={"category": "unbekannt"}).json() == []


def test_public_scenario_never_reveals_secrets() -> None:
    for s in roleplay.load_scenarios():
        public = roleplay.public_scenario(s)
        pub = json.dumps(public, ensure_ascii=False)
        for key in HIDDEN_KEYS:
            assert key not in pub
        # the public counterpart card carries only role + personality, none of the target values
        counterpart = json.dumps(public["counterpart"], ensure_ascii=False)
        for value in s.counterpart.hidden_targets.values():
            assert value not in counterpart
        for step in s.counterpart.concession_ladder:
            assert step not in pub


# ---------------------------------------------------------------- start / turns


def test_start_returns_opening_line_and_ledger_rows(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    before = ledger_count()
    head = _start(client, learner_id)
    assert head["session_id"] and head["opening_line"]
    assert head["turn"] == 0 and head["max_turns"] == 12
    assert head["scenario"]["id"] == "einkauf_1_rahmenvertrag"
    assert "hidden_targets" not in json.dumps(head)
    assert head["opening_audio_b64"] == "" and head["opening_audio_mime"] is None  # text mode: no TTS
    assert ledger_count() - before >= 2  # llm tokens_in + tokens_out
    rows = ledger.rows_for_session(head["session_id"])
    assert {row["stage"] for row in rows} == {"llm"}
    assert all(row["prompt_version"] for row in rows)

    s = client.get(f"/roleplay/{head['session_id']}").json()
    assert s["ended"] is False and s["turn"] == 0 and s["coach"] is None and s["hidden_targets"] is None
    assert [t["role"] for t in s["turns"]] == ["assistant"]
    dumped = json.dumps(s, ensure_ascii=False)
    assert "concession_ladder" not in dumped and "escalation_triggers" not in dumped
    for value in _hidden_targets_from_yaml("einkauf_1_rahmenvertrag").values():
        assert value not in dumped


def test_text_turns_increment_and_never_correct(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = _start(client, learner_id)["session_id"]
    r = client.post(f"/roleplay/{sid}/turn", json={"text": "Ich habe gestern ein Meeting gehabt und will 175 Euro."})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {
        "session_id",
        "learner_text",
        "reply_text",
        "reply_audio_b64",
        "reply_audio_mime",
        "turn",
        "max_turns",
        "ended",
        "prosody",
        "coach",
        "hidden_targets",
        "result_id",
        "cost_eur_turn",
        "cost_eur_session",
        "duration_seconds",
        "learning_seconds",
    }
    assert body["turn"] == 1 and body["ended"] is False
    assert body["duration_seconds"] >= body["learning_seconds"] >= 0.0
    assert body["reply_text"] and "Korrektur" not in body["reply_text"] and "Tipp:" not in body["reply_text"]
    assert body["coach"] is None and body["hidden_targets"] is None and body["result_id"] is None
    assert body["prosody"] is None
    assert body["cost_eur_turn"] >= 0.0 and body["cost_eur_session"] >= body["cost_eur_turn"]
    assert abs(ledger.session_cost_eur(sid) - body["cost_eur_session"]) < 1e-9

    body2 = client.post(f"/roleplay/{sid}/turn", json={"text": "Welche Laufzeit schlagen Sie vor?"}).json()
    assert body2["turn"] == 2 and body2["ended"] is False

    s = client.get(f"/roleplay/{sid}").json()
    assert [t["role"] for t in s["turns"]] == ["assistant", "learner", "assistant", "learner", "assistant"]
    assert [t["ord"] for t in s["turns"]] == [0, 1, 2, 3, 4]
    assert s["turn"] == 2 and s["ended"] is False


def test_sanitize_reply_strips_corrections() -> None:
    raw = "Das klingt machbar.\nKorrektur: „ich will“ → „ich möchte“.\n  Tipp: Nutzen Sie den Konjunktiv.\nWann?"
    assert roleplay.sanitize_reply(raw) == "Das klingt machbar.\nWann?"
    assert roleplay.sanitize_reply("Korrektur: alles falsch.") == roleplay.EMPTY_REPLY_FALLBACK_DE


# ---------------------------------------------------------------- end / coach


def test_end_signal_triggers_coach_and_stores_result(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = _start(client, learner_id, "gehalt_1_jahresgespraech")["session_id"]
    client.post(f"/roleplay/{sid}/turn", json={"text": "Ich halte eine Anpassung auf 68.000 Euro für angemessen."})
    r = client.post(f"/roleplay/{sid}/turn", json={"text": "Vielen Dank. Rollenspiel Ende"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ended"] is True and body["turn"] == 2
    assert body["reply_text"] == "Rollenspiel beendet."

    coach = body["coach"]
    assert coach is not None and coach["outcome_vs_goals"]
    assert isinstance(coach["moves_used"], list) and isinstance(coach["moves_missed"], list)
    assert len(coach["better_formulations"]) >= 1 and coach["next_focus"]
    vocabulary = set(rubrics.error_tag_vocabulary())
    assert coach["error_tags"] and set(coach["error_tags"]) <= vocabulary
    rubric = coach["rubric_result"]
    assert rubric["rubric_version"] == "sprechen_v1" and rubric["task_type"] == "verhandlung"
    assert rubric["blueprint_id"] == "verhandlung_gehalt"
    assert [sc["criterion"] for sc in rubric["scores"]] == rubrics.criteria("sprechen_v1")
    assert all(0 <= sc["score"] <= 4 for sc in rubric["scores"])
    assert rubric["model_version"] and rubric["prompt_version"] == "1.0.0"
    assert rubric["summary_de"]

    assert body["hidden_targets"] == _hidden_targets_from_yaml("gehalt_1_jahresgespraech")
    assert body["result_id"]
    stored = results.get_result(body["result_id"])
    assert stored is not None
    assert stored["skill"] == "sprechen" and stored["task_type"] == "verhandlung"
    assert stored["rubric_version"] == "sprechen_v1" and stored["session_id"] == sid
    assert stored["score_max"] == 28.0 and 0.0 <= stored["score"] <= 28.0
    assert stored["payload"]["coach"]["outcome_vs_goals"] == coach["outcome_vs_goals"]
    assert stored["payload"]["scenario_id"] == "gehalt_1_jahresgespraech"

    # coach ran on the assessment tier and was metered
    rows = ledger.rows_for_session(sid)
    assert any(row["prompt_version"] == "1.0.0" and row["stage"] == "llm" for row in rows)
    assert body["cost_eur_session"] >= body["cost_eur_turn"] >= 0.0

    # turn after end → 400; state is persisted and now reveals the targets
    assert client.post(f"/roleplay/{sid}/turn", json={"text": "noch einer"}).status_code == 400
    s = client.get(f"/roleplay/{sid}").json()
    assert s["ended"] is True and s["ended_at"] is not None
    assert s["coach"]["rubric_result"]["task_type"] == "verhandlung"
    assert s["hidden_targets"] == body["hidden_targets"] and s["result_id"] == body["result_id"]
    assert s["turns"][-1]["text"] == "Rollenspiel beendet."


def test_reaching_max_turns_ends_the_roleplay(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    head = _start(client, learner_id, "vertrieb_1_neukunde")
    sid, max_turns = head["session_id"], head["max_turns"]
    for i in range(1, max_turns):
        body = client.post(f"/roleplay/{sid}/turn", json={"text": f"Argument Nummer {i}."}).json()
        assert body["turn"] == i and body["ended"] is False and body["coach"] is None
    body = client.post(f"/roleplay/{sid}/turn", json={"text": "Letztes Argument."}).json()
    assert body["turn"] == max_turns and body["ended"] is True
    assert body["coach"]["rubric_result"]["blueprint_id"] == "verhandlung_vertrieb"
    assert body["hidden_targets"] == _hidden_targets_from_yaml("vertrieb_1_neukunde")
    assert client.post(f"/roleplay/{sid}/turn", json={"text": "zu spät"}).status_code == 400


def test_end_endpoint_forces_coach_and_is_idempotent(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = _start(client, learner_id, "beschwerde_1_lieferverzug")["session_id"]
    client.post(f"/roleplay/{sid}/turn", json={"text": "Es tut mir leid, dass die Lieferung zu spät kam."})
    r = client.post(f"/roleplay/{sid}/end")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ended"] is True and body["turn"] == 1 and body["result_id"]
    assert body["coach"]["rubric_result"]["task_type"] == "verhandlung"
    assert body["hidden_targets"] == _hidden_targets_from_yaml("beschwerde_1_lieferverzug")
    again = client.post(f"/roleplay/{sid}/end").json()
    assert again["result_id"] == body["result_id"] and again["ended"] is True
    assert again["coach"] == body["coach"]
    assert len([r for r in results.recent_results(learner_id, skill="sprechen") if r["session_id"] == sid]) == 1


# ---------------------------------------------------------------- voice


def test_voice_turn_multipart_yields_prosody_and_audio(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    head = _start(client, learner_id, "meeting_1_ressourcen", voice=True)
    assert head["opening_audio_b64"] and head["opening_audio_mime"] in ("audio/wav", "audio/mpeg")
    sid = head["session_id"]
    r = client.post(f"/roleplay/{sid}/turn", files={"file": ("input.wav", silent_wav(3.0), "audio/wav")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["learner_text"]  # transcript from fake STT
    prosody = body["prosody"]
    assert prosody is not None and prosody["n_words"] > 0 and prosody["tempo_label_de"]
    assert "%" not in json.dumps(prosody, ensure_ascii=False)
    assert body["reply_audio_b64"] and base64.b64decode(body["reply_audio_b64"])[:4] == b"RIFF"
    assert body["turn"] == 1 and body["ended"] is False
    stages = {row["stage"] for row in ledger.rows_for_session(sid)}
    assert {"stt", "llm", "tts"} <= stages

    s = client.get(f"/roleplay/{sid}").json()
    learner_turn = next(t for t in s["turns"] if t["role"] == "learner")
    assert learner_turn["meta"]["input"] == "audio" and learner_turn["meta"]["prosody"]
    assert s["duration_seconds"] >= 3.0

    ended = client.post(f"/roleplay/{sid}/end").json()
    assert ended["ended"] is True and ended["reply_audio_b64"]  # "Rollenspiel beendet." is spoken too


# ---------------------------------------------------------------- errors


def test_not_found_and_validation(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    assert (
        client.post("/roleplay/start", json={"scenario_id": "gibt_es_nicht", "learner_id": learner_id}).status_code
        == 404
    )
    assert client.post("/roleplay/nope/turn", json={"text": "Hallo"}).status_code == 404
    assert client.post("/roleplay/nope/end").status_code == 404
    assert client.get("/roleplay/nope").status_code == 404
    sid = _start(client, learner_id)["session_id"]
    assert client.post(f"/roleplay/{sid}/turn", json={"text": "   "}).status_code == 400
    assert client.post(f"/roleplay/{sid}/turn", json={}).status_code == 400
    # a tutor session is not a roleplay session
    tutor_sid = client.post("/sessions", json={"learner_id": learner_id}).json()["session_id"]
    assert client.post(f"/roleplay/{tutor_sid}/turn", json={"text": "Hallo"}).status_code == 404
