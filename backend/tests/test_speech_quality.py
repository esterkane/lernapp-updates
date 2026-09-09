from types import SimpleNamespace

import pytest
from app.services.speech_text import prepare
from app.services.tts import OpenAIMiniTTS, PiperTTS


def test_reading_text_preserves_english_and_numbers():
    source = "## **Konjunktiv II**\n- Cash und `Accounts Receivable`: z. B. [Rechnung](https://example.org). Betrag: 1.250,50 Euro."
    spoken = prepare(source)
    assert spoken == "Konjunktiv zwei\nCash und Accounts Receivable: zum Beispiel Rechnung. Betrag: 1.250,50 Euro."
    assert "**" in source


def test_openai_uses_selected_voice_pace_and_language_guidance(monkeypatch):
    import litellm
    from app.core import credentials

    captured = []
    monkeypatch.setattr(credentials, "require_workspace_key", lambda *a: "test-key")
    monkeypatch.setattr(litellm, "speech", lambda **kw: captured.append(kw) or SimpleNamespace(content=b"audio"))
    result = OpenAIMiniTTS(voice="cedar", speed=0.8).synthesize(
        "Cash und Receivable", session_id="test-voice", learner_id="default"
    )
    assert result.data == b"audio"
    call = captured[0]
    assert call["voice"] == "cedar" and call["speed"] == 0.8
    assert "natürlichem Englisch" in call["instructions"]
    assert call["input"] == "Cash und Receivable"


def test_piper_slow_speed_uses_longer_phonemes(tmp_path, monkeypatch):
    import sys

    from app.services import tts

    model = tmp_path / "models/piper/de_DE-thorsten-medium.onnx"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"model")
    model.with_suffix(".onnx.json").write_text("{}")
    monkeypatch.setattr(tts, "get_settings", lambda: SimpleNamespace(data_path=tmp_path, tts_speed=0.85))
    scales = []

    def synth(text, wav, syn_config):
        scales.append(syn_config.length_scale)
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(b"\0\0" * 22050)

    monkeypatch.setitem(
        sys.modules,
        "piper",
        SimpleNamespace(
            PiperVoice=SimpleNamespace(load=lambda p: SimpleNamespace(synthesize_wav=synth)),
            SynthesisConfig=lambda **kw: SimpleNamespace(**kw),
        ),
    )
    PiperTTS(speed=0.8).synthesize("Hallo", session_id="test-pace", learner_id="default")
    assert scales == [1.25]


def test_voice_preview_parameters_persist_only_on_activation(client, monkeypatch):
    from app.api import speech_setup

    changes = []
    monkeypatch.setattr(speech_setup, "_check_voice", lambda b: None)
    monkeypatch.setattr(speech_setup, "write_env_file", changes.append)
    body = {"backend": "openai_mini_tts", "voice": "marin", "speed": 0.85}
    assert client.post("/settings/speech/voice/activate", json=body).status_code == 200
    assert changes == [{"TTS_BACKEND": "openai_mini_tts", "OPENAI_TTS_VOICE": "marin", "TTS_SPEED": "0.85"}]
    assert client.post("/settings/speech/voice/activate", json={**body, "speed": 3}).status_code == 422


@pytest.mark.parametrize("speed", ["nan", "infinity", "fast", "0.1", "2"])
def test_invalid_speed_cannot_break_settings(client, monkeypatch, speed):
    from app.api import settings

    monkeypatch.setattr(settings, "write_env_file", lambda values: pytest.fail("Invalid settings must not be saved"))
    assert client.put("/settings", json={"values": {"TTS_SPEED": speed}}).status_code == 400
