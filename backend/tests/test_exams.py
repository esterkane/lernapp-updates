import json
from datetime import UTC, datetime, timedelta

import pytest
from app.core.db import db_session
from app.db.exams import Exam, ExamAttempt
from app.services import exam_sources, exams, privacy, rag
from app.services.exam_schemas import ExamDraft

from tests.test_rag_german import minimal_pdf


@pytest.fixture
def exam(client):
    owner = client.post("/workspaces", json={"name": "Exams"}).json()["id"]
    headers = {"X-Lernapp-Workspace": owner}
    pdf = minimal_pdf(["Test", "Frage: Ist die Rechnung offen?", "A: ja B: nein", "Loesung: ja"])
    response = client.post("/exams", files={"file": ("test.pdf", pdf, "application/pdf")}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["id"], owner, headers


def draft():
    return {
        "title": "Rechnung",
        "duration_minutes": 1,
        "questions": [
            {
                "id": "q1",
                "kind": "choice",
                "page": 1,
                "question": "Ist sie offen?",
                "passage": "Die Rechnung ist noch offen. Bitte prüfen Sie den Zahlungseingang.",
                "source_pages": [1],
                "options": ["ja", "nein"],
                "answers": ["ja"],
                "explanation": "KEY_SECRET",
                "points": 2,
            },
            {"id": "q2", "kind": "writing", "page": 1, "question": "Schreibe eine Mahnung."},
        ],
    }


def publish(client, exam):
    identifier, owner, headers = exam
    response = client.put(
        f"/exams/{identifier}", headers=headers, json={"draft": draft(), "reviewed": True, "revision": 1}
    )
    assert response.status_code == 200, response.text


def test_upload_review_snapshot_score_and_replay(client, exam):
    identifier, owner, headers = exam
    assert client.post(f"/exams/{identifier}/start", json={}, headers=headers).status_code == 409
    publish(client, exam)
    response = client.post(f"/exams/{identifier}/start", json={"timed": True}, headers=headers)
    assert response.status_code == 200, response.text
    attempt = response.json()
    assert "KEY_SECRET" not in response.text
    assert "answers" not in attempt["test"]["questions"][0]
    assert "explanation" not in attempt["test"]["questions"][0]
    aid = attempt["id"]
    assert not client.get(f"/exams/attempts/{aid}", headers=headers).json()["outdated"]
    modified = draft()
    modified["questions"][0]["answers"] = ["nein"]
    assert (
        client.put(
            f"/exams/{identifier}", headers=headers, json={"draft": modified, "reviewed": True, "revision": 2}
        ).status_code
        == 200
    )
    assert client.get(f"/exams/attempts/{aid}", headers=headers).json()["outdated"]
    with db_session() as db:
        row = db.get(ExamAttempt, aid)
        row.started_at = datetime.now(UTC) - timedelta(minutes=2)
    result = client.post(
        f"/exams/attempts/{aid}/submit",
        headers=headers,
        json={"answers": {"q1": "ja", "q2": "Sehr geehrte Damen und Herren"}},
    ).json()["result"]
    assert result["score"] == result["score_max"] == 2
    assert result["items"][1]["correct"] is None
    assert result["time_exceeded"]
    assert result["items"][0]["explanation"] == "KEY_SECRET"
    again = client.post(f"/exams/attempts/{aid}/submit", headers=headers, json={"answers": {"q1": "nein"}}).json()
    assert again["result"] == result
    assert client.get("/exams/attempts", headers=headers).json()[0]["id"] == aid


def test_ownership_assets_export_and_delete(client, exam):
    identifier, owner, headers = exam
    other = client.post("/workspaces", json={"name": "Other"}).json()["id"]
    foreign = {"X-Lernapp-Workspace": other}
    publish(client, exam)
    aid = client.post(f"/exams/{identifier}/start", json={}, headers=headers).json()["id"]
    for path in [f"/exams/{identifier}/editor", f"/exams/{identifier}/assets/pdf", f"/exams/attempts/{aid}"]:
        assert client.get(path, headers=foreign).status_code == 404
    assert client.post(f"/exams/{identifier}/extract", headers=foreign, json={"first": 1, "last": 1}).status_code == 404
    assert client.post(f"/exams/attempts/{aid}/submit", headers=foreign, json={"answers": {}}).status_code == 404
    assert client.delete(f"/exams/{identifier}", headers=foreign).status_code == 404
    assert client.get(f"/exams/{identifier}/assets/pdf", headers=headers).content.startswith(b"%PDF-")
    assert rag.list_documents(owner) == []
    exported = json.loads(privacy.export_files(owner)["exams.json"])
    assert exported["exams"][0]["pdf_b64"] and exported["attempts"][0]["id"] == aid
    assert client.delete(f"/exams/{identifier}", headers=headers).status_code == 200
    assert client.get(f"/exams/attempts/{aid}", headers=headers).status_code == 404


def test_invalid_imports_and_drafts(client, exam):
    identifier, owner, headers = exam
    assert (
        client.post("/exams", headers=headers, files={"file": ("bad.pdf", b"html", "application/pdf")}).status_code
        == 400
    )
    data = draft()
    data["questions"][0]["answers"] = []
    assert (
        client.put(
            f"/exams/{identifier}", headers=headers, json={"draft": data, "reviewed": True, "revision": 1}
        ).status_code
        == 400
    )
    data = draft()
    data["questions"][0]["page"] = 2
    assert (
        client.put(
            f"/exams/{identifier}", headers=headers, json={"draft": data, "reviewed": False, "revision": 1}
        ).status_code
        == 400
    )
    data = draft()
    data["questions"][0]["audio_start"] = 10
    assert (
        client.put(
            f"/exams/{identifier}", headers=headers, json={"draft": data, "reviewed": False, "revision": 1}
        ).status_code
        == 400
    )
    data = draft()
    data["questions"].append(data["questions"][0])
    assert (
        client.put(
            f"/exams/{identifier}", headers=headers, json={"draft": data, "reviewed": False, "revision": 1}
        ).status_code
        == 422
    )
    publish(client, exam)
    assert (
        client.put(
            f"/exams/{identifier}", headers=headers, json={"draft": draft(), "reviewed": False, "revision": 1}
        ).status_code
        == 409
    )


def test_extraction_keeps_keys_private_until_review(client, exam, monkeypatch):
    identifier, owner, headers = exam
    calls = []

    def extract(*args, **kwargs):
        calls.append(kwargs)
        return ExamDraft.model_validate(draft())

    monkeypatch.setattr(exams.llm, "complete", extract)
    response = client.post(f"/exams/{identifier}/extract", headers=headers, json={"first": 1, "last": 1})
    assert response.status_code == 200, response.text
    assert not response.json()["reviewed"]
    assert response.json()["extracted_pages"] == [1]
    assert client.get(f"/exams/{identifier}/editor", headers=headers).json()["extracted_pages"] == [1]
    assert len(response.json()["draft"]["questions"]) == 2
    assert calls[0]["learner_id"] == owner
    assert calls[0]["session_id"] == f"exam:{identifier}"
    assert client.post(f"/exams/{identifier}/start", headers=headers, json={}).status_code == 409


def test_tandem_pair_parser_and_invalid_source(monkeypatch):
    parser = exam_sources.Links()
    parser.feed('<a href="/de/downloads.html?f=telc_Deutsch_B2_Modelltest.pdf&amp;d=attachment">PDF</a>')
    assert "telc_Deutsch_B2_Modelltest.pdf" in parser.links
    with pytest.raises(ValueError):
        exam_sources.import_source("arbitrary", "default")
    with pytest.raises(ValueError):
        exam_sources.download("http://127.0.0.1/private", 100)


def test_learner_deletion_cascades_assets(client, exam):
    identifier, owner, headers = exam
    privacy.delete_learner(owner)
    with db_session() as db:
        assert db.get(Exam, identifier) is None


def test_audio_attachment_suggestions_and_solution_isolation(client, exam, monkeypatch):
    from app.services import stt
    from app.services.fakes import silent_wav
    from app.services.schemas import Transcript, Word

    identifier, owner, headers = exam
    sound = silent_wav(3)
    response = client.post(
        f"/exams/{identifier}/audio", headers=headers, files={"file": ("test.wav", sound, "audio/wav")}
    )
    assert response.status_code == 200, response.text
    data = draft()
    data["questions"][0]["audio_reference"] = "Mieter werden ihre Wohnung schneller kündigen"
    response = client.put(
        f"/exams/{identifier}", headers=headers, json={"draft": data, "reviewed": False, "revision": 2}
    )
    assert response.status_code == 200, response.text
    calls = []

    def transcribe(*args, **kwargs):
        calls.append(kwargs)
        words = [
            Word(text=t, start=i * 0.2, end=(i + 1) * 0.2)
            for i, t in enumerate(data["questions"][0]["audio_reference"].split())
        ]
        return Transcript(text=data["questions"][0]["audio_reference"], words=words, duration_s=3)

    monkeypatch.setattr(stt, "transcribe_safe", transcribe)
    for _ in range(2):
        response = client.post(f"/exams/{identifier}/audio-suggestions", headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["suggestions"][0]["id"] == "q1"
    assert len(calls) == 1
    assert calls[0]["learner_id"] == owner
    current = client.get(f"/exams/{identifier}/editor", headers=headers).json()
    assert current["transcript"]["text"]
    assert (
        client.put(
            f"/exams/{identifier}",
            headers=headers,
            json={"draft": data, "reviewed": True, "revision": current["revision"]},
        ).status_code
        == 200
    )
    response = client.post(f"/exams/{identifier}/start", json={}, headers=headers)
    assert "audio_reference" not in response.text
    assert "Mieter" not in response.text
    assert "transcript" not in response.text
    assert (
        client.post(
            f"/exams/{identifier}/audio", headers=headers, files={"file": ("replacement.wav", sound, "audio/wav")}
        ).status_code
        == 409
    )
    assert client.get(f"/exams/{identifier}/assets/audio", headers=headers).content == sound


def test_unmatched_audio_does_not_invent_timestamps():
    words = [{"text": word, "start": i, "end": i + 1} for i, word in enumerate(["heute", "scheint", "die", "Sonne"])]
    assert exams.suggest_audio([{"id": "q", "audio_reference": "Ein völlig anderes Thema steht hier"}], words) == []


def test_practice_ui_hides_key_and_renders_result(client, exam, monkeypatch):
    from pathlib import Path

    from lernapp_ui import exam_api
    from streamlit.testing.v1 import AppTest

    identifier, owner, headers = exam
    publish(client, exam)
    attempt = client.post(f"/exams/{identifier}/start", json={}, headers=headers).json()
    monkeypatch.setattr(exam_api, "attempt", lambda aid: client.get(f"/exams/attempts/{aid}", headers=headers).json())
    monkeypatch.setattr(exam_api, "save_progress", lambda aid, answers, position, revision: exams.save_progress(aid, owner, answers, position, revision))
    monkeypatch.setattr(
        exam_api,
        "submit",
        lambda aid, answers: client.post(
            f"/exams/attempts/{aid}/submit", json={"answers": answers}, headers=headers
        ).json(),
    )
    page = Path(__file__).resolve().parents[2] / "frontend/lernapp_ui/pages/modelltests.py"
    app = AppTest.from_file(str(page))
    app.session_state["exam_active"] = attempt["id"]
    app.run()
    assert not app.exception
    assert not any("KEY_SECRET" in m.value for m in app.markdown)
    assert len(app.radio) == 1
    assert any("Die Rechnung ist noch offen." in m.value for m in app.markdown)
    rendered_pages = []
    def page_image(eid, page, aid):
        rendered_pages.append(page)
        return client.get(f"/exams/{eid}/pages/{page}", headers=headers, params={"attempt_id": aid}).content
    monkeypatch.setattr(exam_api, "page_image", page_image)
    app.checkbox(key=f"original_{attempt['id']}_q1").check().run()
    assert rendered_pages == [1]
    app.run()
    assert rendered_pages == [1]  # session page cache avoids repeat transfer
    app.radio[0].set_value("ja").run()
    next(b for b in app.button if b.label == "Weiter →").click().run()
    assert len(app.radio) == 0
    app.text_area[0].input("Sehr geehrte Damen und Herren").run()
    next(b for b in app.button if b.label == "← Vorherige Aufgabe").click().run()
    assert app.radio[0].value == "ja"
    next(b for b in app.button if b.label == "Weiter →").click().run()
    assert app.text_area[0].value == "Sehr geehrte Damen und Herren"
    next(b for b in app.button if b.label == "← Vorherige Aufgabe").click().run()
    next(b for b in app.button if b.label == "Test abgeben und auswerten").click().run()
    assert not app.exception
    assert any("KEY_SECRET" in m.value for m in app.markdown)
    assert any(m.value == "2 / 2" for m in app.metric)


def test_editor_and_import_ui_render(client, exam, monkeypatch):
    from pathlib import Path

    from lernapp_ui import exam_api
    from streamlit.testing.v1 import AppTest

    identifier, owner, headers = exam
    monkeypatch.setattr(exam_api, "listing", lambda: client.get("/exams", headers=headers).json())
    monkeypatch.setattr(exam_api, "editor", lambda eid: client.get(f"/exams/{eid}/editor", headers=headers).json())
    monkeypatch.setattr(exam_api, "import_status", lambda _: {"running": False, "error": None, "completed": 0, "total": 1})
    monkeypatch.setattr(exam_api, "sources", lambda: client.get("/exams/sources", headers=headers).json())
    page = Path(__file__).resolve().parents[2] / "frontend/lernapp_ui/pages/modelltests.py"
    app = AppTest.from_file(str(page)).run()
    assert app.selectbox[0].value == identifier
    assert any("Aufgaben fehlen" in option for option in app.selectbox[0].options)
    assert not any(b.label == "Test starten" for b in app.button)
    next(b for b in app.button if b.label == "Aufgaben vorbereiten").click().run()
    assert app.session_state["exam_mode"] == "Bearbeiten"
    assert app.selectbox[0].value == identifier
    assert not app.exception
    assert any(b.label == "Frage speichern" for b in app.button)
    assert any(b.label == "Aufgaben aus PDF erstellen" for b in app.button)
    next(b for b in app.button if b.label == "← Zurück zu den Tests").click().run()
    next(b for b in app.button if b.label == "PDF und Hördatei hinzufügen").click().run()
    assert not app.exception
    assert any(b.label == "TANDEM-Dateien importieren" for b in app.button)
    upload = next(b for b in app.button if b.label == "Dateien importieren und Aufgaben vorbereiten")
    assert not upload.disabled  # Form uploads only reach Python when submitted.
    upload.click().run()
    assert not app.exception
    assert any("Prüfungs-PDF auswählen" in w.value for w in app.warning)


def test_whole_pdf_extraction_resumes_after_failure(monkeypatch):
    from lernapp_ui import exam_api

    saved = {"pages": ["page"] * 10, "extracted_pages": []}
    calls = []
    fail = True

    def batch(identifier, first, last):
        nonlocal fail
        calls.append((first, last))
        if first == 5 and fail:
            fail = False
            raise RuntimeError("provider interrupted")
        saved["extracted_pages"] = sorted(set(saved["extracted_pages"]) | set(range(first, last + 1)))
        return saved

    monkeypatch.setattr(exam_api, "editor", lambda _: saved)
    monkeypatch.setattr(exam_api, "extract", batch)
    progress = []
    with pytest.raises(RuntimeError):
        exam_api.extract_all("test", lambda done, total: progress.append((done, total)))
    assert saved["extracted_pages"] == [1, 2, 3, 4]
    result = exam_api.extract_all("test", lambda done, total: progress.append((done, total)))
    assert calls == [(1, 4), (5, 8), (5, 8), (9, 10)]
    assert result["extracted_pages"] == list(range(1, 11))
    assert progress[-1] == (10, 10)
    exam_api.extract_all("test", lambda *_: None)
    assert len(calls) == 4


def test_background_import_retries_and_skips_saved_pages(monkeypatch):
    from app.services import exam_import_jobs as jobs

    data = {"pages": ["a", "b", "c"], "extracted_pages": [1]}
    calls = []

    def extract(identifier, owner, first, last):
        calls.append((first, last))
        if len(calls) == 1:
            raise exams.llm.LLMError("timeout")
        data["extracted_pages"].append(first)
        return data

    monkeypatch.setattr(exams, "editor", lambda *_: data)
    monkeypatch.setattr(exams, "extract", extract)
    jobs._work("retry-test", "owner")
    assert calls == [(2, 2), (2, 2), (3, 3)]
    assert jobs.status("retry-test", "owner") == {
        "running": False, "error": None, "completed": 3, "total": 3,
    }


def test_original_page_images_are_scoped_to_attempt(client, exam):
    identifier, owner, headers = exam
    body = draft()
    body['questions'][0]['source_pages'] = [1]
    response = client.put(f'/exams/{identifier}', headers=headers,
                          json={'draft': body, 'reviewed': True, 'revision': 1})
    assert response.status_code == 200
    attempt = client.post(f'/exams/{identifier}/start', headers=headers, json={}).json()['id']
    response = client.get(f'/exams/{identifier}/pages/1', headers=headers, params={'attempt_id': attempt})
    assert response.status_code == 200
    assert response.content.startswith(b'\x89PNG\r\n\x1a\n')
    assert client.get(f'/exams/{identifier}/pages/2', headers=headers,
                      params={'attempt_id': attempt}).status_code == 404
    foreign = client.post('/workspaces', json={'name': 'Foreign pages'}).json()['id']
    assert client.get(f'/exams/{identifier}/pages/1', headers={'X-Lernapp-Workspace': foreign},
                      params={'attempt_id': attempt}).status_code == 404


def test_pdf_null_characters_do_not_break_import(client, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(exams, 'PdfReader', lambda _: SimpleNamespace(
        is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: 'Text\x00mit Nullzeichen')]))
    response = client.post('/exams', files={'file': ('null.pdf', b'%PDF-1.4 test', 'application/pdf')})
    assert response.status_code == 200
    editor = client.get('/exams/' + response.json()['id'] + '/editor').json()
    assert editor['pages'] == ['Text mit Nullzeichen']


def test_render_cache_invalidates_rotation_and_checks_deleted_owner(client, exam, monkeypatch):
    import pypdfium2
    from app.services import exam_pages

    identifier, owner, headers = exam
    calls = []
    original = pypdfium2.PdfPage.render
    def counted(self, *args, **kwargs):
        calls.append(kwargs.get('rotation'))
        return original(self, *args, **kwargs)
    monkeypatch.setattr(pypdfium2.PdfPage, 'render', counted)
    image = exam_pages.render(identifier, owner, 1)
    assert exam_pages.render(identifier, owner, 1) == image
    assert calls == [0]
    with db_session() as db:
        row = db.get(Exam, identifier)
        row.payload = {**row.payload, 'page_rotation': 90}
    assert exam_pages.render(identifier, owner, 1) != image
    assert calls == [0, 90]
    client.delete(f'/exams/{identifier}', headers=headers)
    assert client.get(f'/exams/{identifier}/pages/1', headers=headers).status_code == 404


def test_progress_survives_reload_and_reset_rejects_stale_writes(client, exam):
    eid, owner, headers = exam
    publish(client, exam)
    aid = client.post(f'/exams/{eid}/start', headers=headers, json={}).json()['id']
    url = f'/exams/attempts/{aid}'
    saved = client.put(url+'/progress', headers=headers, json={'answers': {'q1': 'ja'}, 'position': 1, 'revision': 0})
    assert saved.status_code == 200
    response = client.get(url, headers=headers)
    assert 'KEY_SECRET' not in response.text
    assert '_progress' not in response.json()['test']
    assert response.json()['progress']['answers'] == {'q1': 'ja'}
    assert response.json()['progress']['position'] == 1
    assert client.put(url+'/progress', headers=headers, json={'answers': {}, 'revision': 0}).status_code == 409
    reset = client.post(url+'/reset', headers=headers, json={'revision': 1})
    assert reset.status_code == 200
    assert reset.json()['answers'] == {} and reset.json()['position'] == 0
    assert client.put(url+'/progress', headers=headers, json={'answers': {'q1': 'ja'}, 'revision': 1}).status_code == 409
    with db_session() as db:
        assert db.get(ExamAttempt, aid).snapshot['questions'] == ExamDraft.model_validate(draft()).model_dump()['questions']
    assert client.get('/exams/attempts', headers=headers).json()[0]['answered'] == 0


def test_progress_validation_ownership_and_submitted_immutability(client, exam):
    eid, owner, headers = exam
    publish(client, exam)
    aid = client.post(f'/exams/{eid}/start', headers=headers, json={}).json()['id']
    url = f'/exams/attempts/{aid}'
    for body in [{'answers': {'q1': 'bad'}}, {'answers': {'unknown': 'a'}}, {'answers': {'q2': 'x'*20001}}, {'position': 2}]:
        assert client.put(url+'/progress', headers=headers, json={**body, 'revision': 0}).status_code == 400
    foreign = {'X-Lernapp-Workspace': client.post('/workspaces', json={'name': 'Foreign'}).json()['id']}
    assert client.put(url+'/progress', headers=foreign, json={'revision': 0}).status_code == 404
    assert client.post(url+'/reset', headers=foreign, json={'revision': 0}).status_code == 404
    assert client.post(url+'/submit', headers=headers, json={'answers': {'q1': 'ja'}}).status_code == 200
    assert client.put(url+'/progress', headers=headers, json={'revision': 0}).status_code == 409
    assert client.post(url+'/reset', headers=headers, json={'revision': 0}).status_code == 409


def test_progress_ui_return_resume_fresh_session_and_reset(client, exam, monkeypatch):
    from pathlib import Path

    from lernapp_ui import exam_api
    from streamlit.testing.v1 import AppTest
    eid, owner, headers = exam
    publish(client, exam)
    monkeypatch.setattr(exam_api, 'listing', lambda: exams.list_exams(owner))
    monkeypatch.setattr(exam_api, 'history', lambda: exams.attempts(owner))
    monkeypatch.setattr(exam_api, 'start', lambda eid, timed: exams.start(eid, owner, timed))
    monkeypatch.setattr(exam_api, 'attempt', lambda aid: exams.get_attempt(aid, owner))
    monkeypatch.setattr(exam_api, 'save_progress', lambda aid, answers, position, revision: exams.save_progress(aid, owner, answers, position, revision))
    monkeypatch.setattr(exam_api, 'reset_progress', lambda aid, revision: exams.save_progress(aid, owner, {}, 0, revision, True))
    path = Path(__file__).resolve().parents[2]/'frontend/lernapp_ui/pages/modelltests.py'
    ui = AppTest.from_file(str(path)).run()
    next(b for b in ui.button if b.label == 'Test starten').click().run()
    aid = ui.session_state['exam_active']
    ui.radio(key=f'exam_answer_{aid}_q1').set_value('ja').run()
    ui.selectbox(key=f'exam_position_{aid}').set_value(1).run()
    ui.text_area(key=f'exam_answer_{aid}_q2').set_value('Bitte zahlen Sie.').run()
    ui.button(key=f'exam_overview_{aid}').click().run()
    assert not ui.exception
    fresh = AppTest.from_file(str(path)).run()
    next(b for b in fresh.button if b.label == 'Gespeicherte Übung fortsetzen').click().run()
    assert fresh.selectbox(key=f'exam_position_{aid}').value == 1
    assert fresh.text_area(key=f'exam_answer_{aid}_q2').value == 'Bitte zahlen Sie.'
    fresh.checkbox(key=f'exam_reset_confirm_{aid}').check().run()
    fresh.button(key=f'exam_reset_{aid}').click().run()
    assert not fresh.exception
    assert fresh.selectbox(key=f'exam_position_{aid}').value == 0
    assert fresh.radio(key=f'exam_answer_{aid}_q1').value is None
    assert exams.get_attempt(aid, owner)['progress']['answers'] == {}
