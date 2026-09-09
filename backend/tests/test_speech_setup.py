import io
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from app.api import speech_setup
from app.services import local_speech, local_voice, stt, tts


def wav(seconds=1):
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(16000)
        out.writeframes(b'\0\0' * int(16000 * seconds))
    return buf.getvalue()


def test_status_checks_only_local_cache(monkeypatch):
    import faster_whisper
    calls = []
    def offline(model, **kwargs):
        calls.append(kwargs)
        assert kwargs['local_files_only'] is True
        raise FileNotFoundError()
    monkeypatch.setattr(faster_whisper, 'download_model', offline)
    assert all(not entry['installed'] for entry in local_speech.status()['models'].values())
    assert len(calls) == 3


def test_missing_local_model_never_downloads_or_switches(monkeypatch):
    import faster_whisper
    monkeypatch.setattr(local_speech, 'cached_path', lambda model: None)
    monkeypatch.setattr(stt.FasterWhisperSTT, '_model', None)
    def forbidden(*args, **kwargs):
        pytest.fail('No automatic download or replacement model allowed')
    monkeypatch.setattr(faster_whisper, 'WhisperModel', forbidden)
    with pytest.raises(RuntimeError, match='herunterladen'):
        stt.FasterWhisperSTT('small')._load()


def test_install_does_not_activate_and_rejects_arbitrary_models(client, monkeypatch):
    calls = []
    monkeypatch.setattr(local_speech, 'start', lambda model: calls.append(model) or {'state': 'installing'})
    monkeypatch.setattr(speech_setup, 'write_env_file', lambda values: pytest.fail('Download must not change active selection'))
    assert client.post('/settings/speech/recognition/install', json={'model': '../../model'}).status_code == 422
    assert client.post('/settings/speech/recognition/install', json={'model': 'small'}).status_code == 200
    assert calls == ['small']


def test_activate_requires_downloaded_model(client, monkeypatch):
    changes = []
    monkeypatch.setattr(speech_setup, 'write_env_file', changes.append)
    monkeypatch.setattr(local_speech, 'cached_path', lambda model: None)
    assert client.post('/settings/speech/recognition/activate', json={'model': 'small'}).status_code == 400
    assert not changes
    monkeypatch.setattr(local_speech, 'cached_path', lambda model: Path('/test/model'))
    assert client.post('/settings/speech/recognition/activate', json={'model': 'small'}).status_code == 200
    assert changes == [{'STT_BACKEND': 'faster_whisper', 'STT_MODEL': 'small', 'STT_COMPUTE_TYPE': 'int8'}]


def test_microphone_upload_is_bounded_and_uses_explicit_model(client, monkeypatch):
    monkeypatch.setattr(local_speech, 'cached_path', lambda model: Path('/test/model'))
    seen = []
    def transcribe(data, model, owner):
        seen.append((model, owner))
        return {'text': 'Ich übe Deutsch.', 'local': True}
    monkeypatch.setattr(speech_setup, '_transcribe', transcribe)
    for data in (b'not audio', wav(21), b'x' * (4 * 1024 * 1024 + 1)):
        assert client.post('/settings/speech/recognition/test', data={'model': 'small'}, files={'file': ('test.wav', data)}).status_code == 400
    assert not seen
    result = client.post('/settings/speech/recognition/test', data={'model': 'medium'}, files={'file': ('test.wav', wav())})
    assert result.json()['text'] == 'Ich übe Deutsch.'
    assert seen == [('medium', 'default')]


def test_microphone_test_always_removes_recording(monkeypatch):
    seen = []
    def recognize(path, **kwargs):
        seen.append(path)
        assert path.read_bytes() == wav()
        return SimpleNamespace(text='Test')
    monkeypatch.setattr(stt, 'HookedSTT', lambda inner: SimpleNamespace(transcribe=recognize))
    assert speech_setup._transcribe(wav(), 'small', 'default')['text'] == 'Test'
    assert not seen[0].exists() and not seen[0].parent.exists()


def test_voice_test_returns_real_audio_without_cloud_fallback(client, monkeypatch):
    monkeypatch.setattr(local_voice, 'status', lambda: {'ready': True})
    def synth(text, **kwargs):
        return SimpleNamespace(data=wav(), mime='audio/wav')
    monkeypatch.setattr(tts, 'HookedTTS', lambda inner: SimpleNamespace(synthesize=synth))
    result = client.post('/settings/speech/voice/test', json={'backend': 'piper'})
    assert result.status_code == 200 and result.content == wav()
    assert result.headers['cache-control'] == 'no-store'
    def fail(text, **kwargs):
        raise RuntimeError('private-provider-key')
    monkeypatch.setattr(tts, 'HookedTTS', lambda inner: SimpleNamespace(synthesize=fail))
    result = client.post('/settings/speech/voice/test', json={'backend': 'piper'})
    assert result.status_code == 400 and 'private-provider-key' not in result.text


def test_voice_activation_and_preview_reject_missing_local_install(client, monkeypatch):
    monkeypatch.setattr(local_voice, 'status', lambda: {'ready': False})
    for action in ('activate', 'test'):
        assert client.post('/settings/speech/voice/' + action, json={'backend': 'piper'}).status_code == 400


def test_voice_preview_passes_the_real_usage_hooks(client, monkeypatch):
    from app.services.schemas import AudioBytes
    monkeypatch.setattr(local_voice, 'status', lambda: {'ready': True})
    seen = []
    def local_sample(self, text, *, session_id, learner_id):
        seen.append((session_id, learner_id))
        return AudioBytes(data=wav(), mime='audio/wav', chars=len(text), backend='piper', model='local/piper')
    monkeypatch.setattr(tts.PiperTTS, 'synthesize', local_sample)
    response = client.post('/settings/speech/voice/test', json={'backend': 'piper'})
    assert response.status_code == 200 and response.content == wav()
    assert seen == [('speech-setup', 'default')]


def test_microphone_preview_passes_the_real_usage_hooks(client, monkeypatch):
    from app.services.fakes import fake_transcript
    monkeypatch.setattr(local_speech, 'cached_path', lambda model: Path('/test/model'))
    seen = []
    def recognize(self, path, *, session_id, learner_id):
        seen.append((session_id, learner_id))
        return fake_transcript(1.0)
    monkeypatch.setattr(stt.FasterWhisperSTT, 'transcribe', recognize)
    response = client.post('/settings/speech/recognition/test', data={'model': 'small'}, files={'file': ('test.wav', wav())})
    assert response.status_code == 200
    assert response.json()['text']
    assert seen == [('speech-setup', 'default')]
