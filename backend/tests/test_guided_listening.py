import io
import wave
from pathlib import Path

import av
import numpy as np
import pytest
from app.services import exams
from app.services import media_lessons as media
from app.services.exam_schemas import ExamDraft
from lernapp_ui import exam_api
from lernapp_ui.exam_navigation import position_index
from streamlit.testing.v1 import AppTest


def recording():
    samples = np.concatenate([np.full(16000, n / 10) for n in range(1, 6)])
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes((samples * 32767).astype('<i2').tobytes())
    return out.getvalue()


@pytest.fixture
def lesson(client):
    owner = client.post('/workspaces', json={'name': 'Guided lesson'}).json()['id']
    vtt = b'WEBVTT\n\n00:00.000 --> 00:04.000\nHallo und willkommen.\n'
    eid = media.create(owner, 'Guided', vtt, recording())['id']
    d = exams.editor(eid, owner)
    draft = ExamDraft.model_validate({'title': 'Guided', 'questions': [
        {'id': f'q{i}', 'section': 'Hörverstehen', 'kind': 'choice', 'question': f'Frage {i}',
         'options': ['Ja', 'Nein'], 'answers': ['Ja'], 'explanation': f'Secret {i}', 'audio_start': i-1, 'audio_end': i}
        for i in [1, 2]]})
    exams.save(eid, owner, draft, False, d['revision'], extracted_pages=[1])
    exams.save(eid, owner, draft, True, d['revision']+1)
    aid = exams.start(eid, owner, False)['id']
    return owner, eid, aid


def test_single_check_survives_resume_and_invalidates_on_edit(client, lesson):
    owner, _, aid = lesson
    h = {'X-Lernapp-Workspace': owner}
    url = f'/exams/attempts/{aid}'
    assert 'Secret' not in client.get(url, headers=h).text
    assert client.post(url+'/questions/q1/check', json={'answer': 'Ja', 'revision': 0}).status_code == 404
    r = client.post(url+'/questions/q1/check', headers=h, json={'answer': 'Ja', 'revision': 0})
    assert r.status_code == 200
    assert r.json()['checked']['q1']['correct'] is True
    resumed = client.get(url, headers=h).json()
    assert resumed['progress']['checked']['q1']['explanation'] == 'Secret 1'
    assert 'Secret 2' not in str(resumed)
    assert resumed['result'] is None
    assert client.post(url+'/questions/q2/check', headers=h, json={'answer': 'Ja', 'revision': 0}).status_code == 409
    r = client.put(url+'/progress', headers=h, json={'answers': {'q1': 'Nein'}, 'position': 0, 'revision': 1})
    assert r.json()['checked'] == {}
    r = client.post(url+'/questions/q1/check', headers=h, json={'answer': 'Nein', 'revision': 2})
    assert r.json()['checked']['q1']['correct'] is False
    assert client.post(url+'/questions/q2/check', headers=h, json={'answer': 'Fake', 'revision': 3}).status_code == 400
    assert client.post(url+'/reset', headers=h, json={'revision': 3}).json()['checked'] == {}


def test_clip_is_physically_bounded_and_owned(client, lesson):
    data = media.audio_clip(recording(), 1.25, 2.75)
    with av.open(io.BytesIO(data)) as c:
        decoded = sum(f.samples/f.sample_rate for f in c.decode(audio=0))
    assert decoded == pytest.approx(1.5, abs=.03)
    assert media.audio_clip(recording(), 1.25, 2.75) is data
    with pytest.raises(ValueError):
        media.audio_clip(recording(), 7, 8)
    owner, _, aid = lesson
    url = f'/exams/attempts/{aid}/questions/q1/audio'
    assert client.get(url).status_code == 404
    r = client.get(url, headers={'X-Lernapp-Workspace': owner})
    assert r.status_code == 200 and r.headers['content-type'] == 'audio/mpeg'
    assert client.get(url.replace('q1', 'unknown'), headers={'X-Lernapp-Workspace': owner}).status_code == 400


def test_guided_ui_real_dropdown_check_next_and_resume(client, lesson, monkeypatch):
    owner, eid, aid = lesson
    monkeypatch.setattr(exam_api, 'attempt', lambda _: exams.get_attempt(aid, owner))
    monkeypatch.setattr(exam_api, 'question_audio', lambda _, q: exams.question_audio(aid, owner, q))
    def save(_, answers, position, revision):
        assert type(position) is int
        return exams.save_progress(aid, owner, answers, position, revision)
    monkeypatch.setattr(exam_api, 'save_progress', save)
    monkeypatch.setattr(exam_api, 'check_answer', lambda _, q, a, r: exams.check_answer(aid, owner, q, a, r))
    path = Path(__file__).resolve().parents[2] / 'frontend/lernapp_ui/pages/modelltests.py'
    ui = AppTest.from_file(str(path))
    ui.session_state['exam_active'] = aid
    # Recover the exact legacy bad value reported by the user.
    ui.session_state[f'exam_position_{aid}'] = '2. Hörverstehen'
    ui.run()
    assert not ui.exception and ui.session_state[f'exam_position_{aid}'] == 1
    ui.selectbox(key=f'exam_selection_{aid}_1').set_value('1. Hörverstehen').run()
    assert exams.get_attempt(aid, owner)['progress']['position'] == 0
    assert next(b for b in ui.button if b.label == 'Weiter zum nächsten Abschnitt →').disabled
    ui.radio(key=f'exam_answer_{aid}_q1').set_value('Ja').run()
    ui.button(key=f'exam_check_{aid}_q1').click().run()
    assert not ui.exception and any(x.value == 'Richtig!' for x in ui.success)
    next(b for b in ui.button if b.label == 'Weiter zum nächsten Abschnitt →').click().run()
    assert ui.session_state[f'exam_position_{aid}'] == 1
    fresh = AppTest.from_file(str(path))
    fresh.session_state['exam_active'] = aid
    fresh.run()
    assert fresh.session_state[f'exam_position_{aid}'] == 1
    assert exams.get_attempt(aid, owner)['progress']['checked']['q1']['correct']


def test_position_labels_never_become_api_values():
    assert position_index('2. Hörverstehen', 4) == 1
    assert position_index('2. Hörverstehen · beantwortet', 4) == 1
    assert position_index('bad', 4) == 0
    assert position_index(999, 4) == 3
