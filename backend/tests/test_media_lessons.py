import io
import wave

import pytest
from app.services import exams
from app.services import media_lessons as media
from app.services.exam_schemas import ExamDraft


def wav():
    out = io.BytesIO()
    with wave.open(out, 'wb') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b'\0' * 16000 * 2 * 4)
    return out.getvalue()


VTT = b'WEBVTT\n\nintro\n00:00.000 --> 00:03.000\nDas ist ja der Hammer!\n'


def test_vtt_rolling_captions_and_real_repetitions():
    text = '''WEBVTT
Kind: captions

00:00:00.000 --> 00:00:01.990 align:start
 
danke<00:00:00.500><c> danke</c>

00:00:01.990 --> 00:00:02.000
danke danke
 

00:00:02.000 --> 00:00:03.990
danke danke
danke<00:00:02.500><c> reicht</c><00:00:03.000><c> schon</c>

00:00:03.990 --> 00:00:04.000
danke reicht schon
 

00:00:04.000 --> 00:00:05.990
danke reicht schon
weiter

00:00:05.990 --> 00:00:06.000
weiter
 

00:00:06.000 --> 00:00:07.000
weiter
jetzt<00:00:06.500><c> geht's</c>
'''
    assert [c['text'] for c in media.parse_vtt(text.encode())] == ['danke danke', 'danke reicht schon', 'weiter', "jetzt geht's"]
    plain = VTT + b'\n00:03.000 --> 00:04.000\nDas ist ja der Hammer!\n'
    assert len(media.parse_vtt(plain)) == 2
    assert media.parse_vtt(b'\xef\xbb\xbf' + VTT)[0]['start'] == 0


def test_vtt_metadata_tags_entities_and_validation():
    assert media.parse_vtt(b'WEBVTT\n\nNOTE ignored\n00:00.000 --> 00:01.000\nsecret\n\nSTYLE\n::cue {}\n\n1\n00:01.000 --> 00:02.000\n<v Anna><b>Hallo</b> &amp; willkommen</v>\n')[0]['text'] == 'Hallo & willkommen'
    for data in [b'not vtt', b'WEBVTT\n\n00:00.000 --> 00:00.000\nBad', b'WEBVTT\n\n00:99.000 --> 00:01.000\nBad', b'WEBVTT\n', b'WEBVTT\n\xff']:
        with pytest.raises(ValueError):
            media.parse_vtt(data)


def generated():
    return ExamDraft.model_validate({'title': 'Test', 'learning_notes': [{'phrase': 'der Hammer', 'meaning': 'sehr erstaunlich'}],
        'questions': [{'id': '1', 'section': 'Hörverstehen', 'question': 'Wie reagiert die Person?', 'options': ['Überrascht', 'Müde'], 'answers': ['Überrascht'], 'explanation': 'HIDDEN_SOLUTION', 'passage': 'Invented text'}]})


def test_media_import_generate_scope_snapshot_and_assets(client, monkeypatch):
    owner = client.post('/workspaces', json={'name': 'Listening'}).json()['id']
    headers = {'X-Lernapp-Workspace': owner}
    files = {'transcript': ('a.vtt', VTT, 'text/vtt'), 'audio': ('a.wav', wav(), 'audio/wav')}
    response = client.post('/exams/media', files=files, headers=headers)
    assert response.status_code == 200, response.text
    eid = response.json()['id']
    assert client.post('/exams/media', files=files, headers=headers).json()['id'] == eid
    assert client.get(f'/exams/{eid}/assets/transcript', headers=headers).content == VTT
    assert client.get(f'/exams/{eid}/assets/source_audio', headers=headers).content == wav()
    assert client.get(f'/exams/{eid}/assets/pdf', headers=headers).status_code == 404
    assert client.get(f'/exams/{eid}/pages/1', headers=headers).status_code == 404
    assert client.get(f'/exams/{eid}/assets/transcript').status_code == 404
    monkeypatch.setattr(media.llm, 'complete', lambda *a, **kw: generated())
    data = exams.extract(eid, owner, 1, 1)
    assert data['extracted_pages'] == [1]
    assert data['draft']['questions'][0]['passage'] == 'Das ist ja der Hammer!'
    assert data['draft']['questions'][0]['audio_end'] == 3
    assert not data['draft']['questions'][0]['source_pages']
    exams.extract(eid, owner, 1, 1)
    data = exams.editor(eid, owner)
    assert len(data['draft']['questions']) == len(data['draft']['learning_notes']) == 1
    exams.save(eid, owner, ExamDraft.model_validate(data['draft']), True, data['revision'])
    attempt = client.post(f'/exams/{eid}/start', headers=headers, json={})
    assert attempt.status_code == 200
    assert 'HIDDEN_SOLUTION' not in attempt.text
    assert attempt.json()['test']['learning_notes'][0]['phrase'] == 'der Hammer'
    assert attempt.json()['test']['source_format'] == 'webvtt'
    assert client.post(f'/exams/{eid}/audio', files={'file': ('b.wav', wav())}, headers=headers).status_code == 409


def test_out_of_range_transcript_and_ungrounded_notes(client, monkeypatch):
    with pytest.raises(ValueError, match='hinaus'):
        media.create('default', 'Test', VTT.replace(b'00:03.000', b'00:30.000'), wav())
    row = media.create('default', 'Test', VTT, wav())
    bad = generated()
    bad.learning_notes[0].phrase = 'invented quote'
    monkeypatch.setattr(media.llm, 'complete', lambda *a, **kw: bad)
    with pytest.raises(ValueError, match='fehlt im Transkript'):
        exams.extract(row['id'], 'default', 1, 1)
    assert exams.editor(row['id'], 'default').get('extracted_pages', []) == []


def test_webm_local_playback_preserves_duration_and_caches():
    import av
    import numpy as np
    buf = io.BytesIO()
    with av.open(buf, 'w', format='webm') as output:
        stream = output.add_stream('libopus', rate=48000)
        stream.layout = 'mono'
        frame = av.AudioFrame.from_ndarray(np.zeros((1, 48000), dtype=np.float32), format='flt', layout='mono')
        frame.sample_rate = 48000
        for packet in stream.encode(frame):
            output.mux(packet)
        for packet in stream.encode(None):
            output.mux(packet)
    raw = buf.getvalue()
    mp3 = media.playback(raw)
    assert not mp3.startswith(b'\x1aE\xdf\xa3')
    assert abs(exams.audio_duration(mp3) - exams.audio_duration(raw)) < .1
    assert media.playback(raw) is mp3
    assert media.playback(wav()) == wav()


def test_media_editor_and_practice_ui(client, monkeypatch):
    from pathlib import Path

    from lernapp_ui import exam_api
    from streamlit.testing.v1 import AppTest
    owner = client.post('/workspaces', json={'name': 'Media UI'}).json()['id']
    item = media.create(owner, 'My listening lesson', VTT, wav())
    eid = item['id']
    monkeypatch.setattr(media.llm, 'complete', lambda *a, **kw: generated())
    data = exams.extract(eid, owner, 1, 1)
    monkeypatch.setattr(exam_api, 'listing', lambda: exams.list_exams(owner))
    monkeypatch.setattr(exam_api, 'editor', lambda _: exams.editor(eid, owner))
    monkeypatch.setattr(exam_api, 'asset', lambda _, kind: exams.asset(eid, owner, kind))
    path = Path(__file__).resolve().parents[2] / 'frontend/lernapp_ui/pages/modelltests.py'
    ui = AppTest.from_file(str(path))
    ui.session_state['exam_mode'] = 'Bearbeiten'
    ui.run()
    assert not ui.exception
    assert any(b.label == 'Erklärung speichern' for b in ui.button)
    assert any(b.label == 'Frage speichern' for b in ui.button)
    assert not any('PDF' in b.label for b in ui.button)
    assert not any('PDF' in n.label for n in ui.number_input)
    exams.save(eid, owner, ExamDraft.model_validate(data['draft']), True, data['revision'])
    attempt = exams.start(eid, owner, False)
    monkeypatch.setattr(exam_api, 'attempt', lambda _: exams.get_attempt(attempt['id'], owner))
    practice = AppTest.from_file(str(path))
    practice.session_state['exam_active'] = attempt['id']
    practice.run()
    assert not practice.exception
    assert any('der Hammer' in m.value for m in practice.markdown)
    assert any(e.label == 'Transkript als Lesehilfe anzeigen' for e in practice.expander)
    assert not any('HIDDEN_SOLUTION' in m.value for m in practice.markdown)
    assert any(b.label == '← Zurück zur Übersicht' for b in practice.button)


def test_short_closing_fragment_stays_with_its_context():
    chunks = media.sections([
        {"start": 0, "end": 230, "text": "This song is a list of movie titles. " * 30},
        {"start": 240, "end": 250, "text": "Another title. Thank you."},
    ])
    assert len(chunks) == 1
    assert chunks[0]["end"] == 250
    assert chunks[0]["text"].endswith("Another title. Thank you.")
