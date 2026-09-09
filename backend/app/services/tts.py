"""Text-to-speech adapters (ADR-0002). Metered in ``chars``.

``get_tts()`` wraps the configured backend in ``HookedTTS`` (every synthesis goes through the hook
engine, ADR-0016); ``synthesize_safe`` returns ``AudioBytes | FailureContext`` for the session hub.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Protocol

from app.core import credentials
from app.core.config import get_settings
from app.core.errors import FailureContext
from app.core.hooks import HookDenied, SessionState, TierCall, engine
from app.core.ledger import meter
from app.core.models import speech_config
from app.core.workspaces import current_workspace
from app.services.fakes import silent_wav
from app.services.schemas import AudioBytes

log = logging.getLogger(__name__)

_SENT_RE = re.compile(r"(?<=[.!?…])\s+")


def split_sentences(text: str, max_chars: int = 600) -> list[str]:
    parts: list[str] = []
    for sent in _SENT_RE.split(text.strip()):
        if not sent:
            continue
        if parts and len(parts[-1]) + len(sent) + 1 <= max_chars:
            parts[-1] += " " + sent
        else:
            parts.append(sent)
    return parts or [text]


class TTSBackend(Protocol):
    name: str
    model: str

    def synthesize(self, text: str, *, session_id: str | None, learner_id: str | None) -> AudioBytes: ...


def _speech_meta(m: Any, resp: Any) -> None:
    """Attach provider request id / usage of a ``litellm.speech`` response to the meter (binary body → headers)."""
    from app.services.stt import provider_request_id, provider_usage

    rid = provider_request_id(resp)
    if rid:
        m.meta["request_id"] = rid
    raw = provider_usage(resp)
    if raw:
        m.meta["raw_usage"] = raw


class GoogleWavenetTTS:
    """Google Cloud TTS — billed per character, which is exactly what we measure."""

    name = "google_wavenet"
    model = "google/wavenet-de"

    def __init__(self, voice: str | None = None) -> None:
        self.voice = voice or speech_config().tts_voice

    def synthesize(self, text: str, *, session_id: str | None, learner_id: str | None) -> AudioBytes:
        from google.cloud import texttospeech as gtts

        if learner_id not in (None, "system", get_settings().default_learner_id):
            raise credentials.CredentialError("Bitte in diesem Lernbereich einen eigenen Sprachanbieter verbinden.")
        client = gtts.TextToSpeechClient()
        chunks: list[bytes] = []
        with meter(
            "tts",
            "google",
            self.model,
            "chars",
            session_id=session_id,
            learner_id=learner_id,
            meta={"voice": self.voice, "operation": "synthesize"},
        ) as m:
            for part in split_sentences(text):
                m.quantity += len(part)
                resp = client.synthesize_speech(
                    input=gtts.SynthesisInput(text=part),
                    voice=gtts.VoiceSelectionParams(language_code="de-DE", name=self.voice),
                    audio_config=gtts.AudioConfig(audio_encoding=gtts.AudioEncoding.MP3),
                )
                chunks.append(resp.audio_content)
        return AudioBytes(
            data=b"".join(chunks), mime="audio/mpeg", chars=len(text), backend=self.name, model=self.model
        )


class OpenAIMiniTTS:
    """OpenAI mini-tts is billed per audio token; pricing.yaml approximates that via characters
    (``approx_per_minute`` / ``chars_per_minute_assumption``). We record the characters we measure and
    keep any provider-reported usage in ``raw_usage`` so the approximation can be checked later."""

    name = "openai_mini_tts"
    model = "openai/gpt-4o-mini-tts"

    def __init__(self, voice: str | None = None, speed: float | None = None) -> None:
        v = voice or get_settings().openai_tts_voice or speech_config().tts_voice
        self.voice = v if v and not v.startswith(("de-DE", "de_DE")) else "marin"
        self.speed = speed if speed is not None else get_settings().tts_speed

    def synthesize(self, text: str, *, session_id: str | None, learner_id: str | None) -> AudioBytes:
        import litellm

        api_key = credentials.require_workspace_key(learner_id, "openai")
        with meter(
            "tts",
            "openai",
            self.model,
            "chars",
            session_id=session_id,
            learner_id=learner_id,
            meta={"voice": self.voice, "operation": "speech"},
        ) as m:
            m.quantity = len(text)
            resp = litellm.speech(
                model="gpt-4o-mini-tts",
                voice=self.voice,
                speed=self.speed,
                instructions=(
                    "Lies den Text vollständig und deutlich vor, ohne ihn zu verändern oder zu übersetzen. "
                    "Sprich deutsche Erklärungen auf Hochdeutsch. Sprich englische Wörter und "
                    "Fachbegriffe wie Cash, Receivable und Accounts Receivable auf natürlichem Englisch "
                    "aus, nicht nach deutschen Lautregeln. Mache kurze Pausen zwischen den Sätzen."
                ),
                input=text,
                response_format="mp3",
                **({"api_key": api_key} if api_key else {}),
            )
            _speech_meta(m, resp)
            data = resp.content if hasattr(resp, "content") else bytes(resp)
        return AudioBytes(data=data, mime="audio/mpeg", chars=len(text), backend=self.name, model=self.model)


class VoxtralTTS:
    name = "voxtral_tts"
    model = "mistral/voxtral-mini-tts"

    def synthesize(self, text: str, *, session_id: str | None, learner_id: str | None) -> AudioBytes:
        import litellm

        api_key = credentials.require_workspace_key(learner_id, "mistral")
        with meter(
            "tts",
            "mistral",
            self.model,
            "chars",
            session_id=session_id,
            learner_id=learner_id,
            meta={"operation": "speech"},
        ) as m:
            m.quantity = len(text)
            resp = litellm.speech(
                model="mistral/voxtral-mini-tts-latest",
                voice="default",
                input=text,
                response_format="mp3",
                **({"api_key": api_key} if api_key else {}),
            )
            _speech_meta(m, resp)
            data = resp.content if hasattr(resp, "content") else bytes(resp)
        return AudioBytes(data=data, mime="audio/mpeg", chars=len(text), backend=self.name, model=self.model)


class PiperTTS:
    """Optional GPL extra (ADR-0015): only loaded when TTS_BACKEND=piper and piper-tts is installed."""

    name = "piper"
    model = "local/piper"

    def __init__(self, speed: float | None = None) -> None:
        s = get_settings()
        self.speed = speed if speed is not None else s.tts_speed
        self.model_path = s.data_path / "models" / "piper" / "de_DE-thorsten-medium.onnx"

    def synthesize(self, text: str, *, session_id: str | None, learner_id: str | None) -> AudioBytes:
        import io
        import wave

        if not self.model_path.is_file() or not self.model_path.with_suffix(".onnx.json").is_file():
            raise RuntimeError("Lokale Stimme fehlt. Bitte unter Einstellungen → Lokale Stimme einrichten.")
        from piper import PiperVoice, SynthesisConfig  # type: ignore[import-not-found]

        with meter("tts", "local", self.model, "chars", session_id=session_id, learner_id=learner_id, local=True) as m:
            m.quantity = len(text)
            voice = PiperVoice.load(str(self.model_path))
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                voice.synthesize_wav(text, w, syn_config=SynthesisConfig(length_scale=1.0 / self.speed))
            data = buf.getvalue()
        return AudioBytes(data=data, mime="audio/wav", chars=len(text), backend=self.name, model=self.model)


class FakeTTS:
    name = "fake"
    model = "google/wavenet-de"

    def synthesize(self, text: str, *, session_id: str | None, learner_id: str | None) -> AudioBytes:
        with meter(
            "tts",
            "google",
            self.model,
            "chars",
            session_id=session_id,
            learner_id=learner_id,
            local=True,
            meta={"fake": True},
        ) as m:
            m.quantity = len(text)
            return AudioBytes(
                data=silent_wav(min(0.3 + len(text) / 400, 3.0)),
                mime="audio/wav",
                chars=len(text),
                backend=self.name,
                model=self.model,
            )


class NoTTS:
    name = "none"
    model = "none"

    def synthesize(self, text: str, *, session_id: str | None, learner_id: str | None) -> AudioBytes:
        return AudioBytes(data=b"", mime="audio/wav", chars=0, backend="none", model="none")


def resolve_tts_backend_name() -> str:
    """Configured backend, downgraded to what is actually usable with the configured credentials."""
    s = get_settings()
    name = speech_config().tts_backend
    if name in ("fake", "none", "piper"):
        return name
    have_google = current_workspace() == s.default_learner_id and bool(
        s.google_application_credentials or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    )
    # env key OR the default learner's encrypted credential (ADR-0019)
    have_openai = credentials.has_key(current_workspace(), "openai")
    have_mistral = credentials.has_key(current_workspace(), "mistral")
    if name == "google_wavenet" and not have_google:
        if have_openai and not s.eu_strict_mode:
            log.info("Google TTS credentials missing → using openai_mini_tts")
            return "openai_mini_tts"
        if have_mistral:
            return "voxtral_tts"
        return "none"
    if name == "openai_mini_tts" and not have_openai:
        return "google_wavenet" if have_google else ("voxtral_tts" if have_mistral else "none")
    if name == "voxtral_tts" and not have_mistral:
        return "none"
    return name


TTS_BACKENDS = ("google_wavenet", "openai_mini_tts", "voxtral_tts", "piper")


class HookedTTS:
    """Backend proxy: ``synthesize`` runs through ``engine.execute`` (pre-hooks may deny)."""

    def __init__(self, inner: TTSBackend) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model

    def synthesize(
        self, text: str, *, session_id: str | None, learner_id: str | None, state: SessionState | None = None
    ) -> AudioBytes:
        from app.services.speech_text import prepare

        text = prepare(text)
        call = TierCall(
            kind="tts",
            tier="tts",
            model=self.inner.model,
            session_id=session_id,
            learner_id=learner_id,
            payload_summary={"backend": self.inner.name, "chars": len(text)},
        )
        res = engine.execute(
            call,
            state or SessionState(),
            lambda _c: self.inner.synthesize(text, session_id=session_id, learner_id=learner_id),
        )
        if not res.success:
            raise HookDenied(res)
        return res.result  # type: ignore[no-any-return]


def _raw_tts() -> TTSBackend:
    name = resolve_tts_backend_name()
    backend: TTSBackend = {
        "google_wavenet": GoogleWavenetTTS,
        "openai_mini_tts": OpenAIMiniTTS,
        "voxtral_tts": VoxtralTTS,
        "piper": PiperTTS,
        "fake": FakeTTS,
        "none": NoTTS,
    }[name]()
    return backend


def get_tts() -> TTSBackend:
    return HookedTTS(_raw_tts())


def synthesize(
    text: str, *, session_id: str | None, learner_id: str | None, state: SessionState | None = None
) -> AudioBytes:
    return HookedTTS(_raw_tts()).synthesize(text, session_id=session_id, learner_id=learner_id, state=state)


def tts_alternatives(current: str) -> list[str]:
    return [f"TTS_BACKEND={b}" for b in TTS_BACKENDS if b != current] + ["TTS_BACKEND=none"]


def synthesize_safe(
    text: str, *, session_id: str | None, learner_id: str | None, state: SessionState | None = None
) -> AudioBytes | FailureContext:
    """``synthesize`` with errors as values (the hub tolerates TTS failures: text without audio)."""
    backend = resolve_tts_backend_name()
    try:
        return synthesize(text, session_id=session_id, learner_id=learner_id, state=state)
    except HookDenied as exc:
        return exc.result.error or FailureContext(category="business", message=str(exc))
    except Exception as exc:  # noqa: BLE001
        log.exception("TTS failed (backend=%s)", backend)
        return FailureContext(
            category="transient",
            message=f"Sprachausgabe fehlgeschlagen ({backend}): {str(exc)[:200]}",
            alternatives=tts_alternatives(backend),
        )
