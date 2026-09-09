"""Speech-to-text adapters (ADR-0002). Every backend meters ``audio_seconds``.

``get_stt()`` returns the configured backend wrapped in ``HookedSTT`` so every transcription goes
through the hook engine (ADR-0016). ``transcribe_safe`` returns ``Transcript | FailureContext``
for the session hub (errors as values).
"""

from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path
from typing import Any, Protocol

from app.core import credentials
from app.core.config import get_settings
from app.core.errors import FailureContext
from app.core.hooks import HookDenied, SessionState, TierCall, engine
from app.core.ledger import meter
from app.core.models import speech_config
from app.services.fakes import fake_transcript
from app.services.schemas import Transcript, Word

log = logging.getLogger(__name__)


def wav_duration(path: Path) -> float:
    try:
        with wave.open(str(path), "rb") as w:
            return w.getnframes() / float(w.getframerate() or 16000)
    except Exception:  # noqa: BLE001
        try:
            import av

            with av.open(str(path)) as c:
                return float(c.duration / 1_000_000) if c.duration else 0.0
        except Exception:  # noqa: BLE001
            return 0.0


class STTBackend(Protocol):
    name: str
    model: str

    def transcribe(self, wav_path: Path, *, session_id: str | None, learner_id: str | None) -> Transcript: ...


class FasterWhisperSTT:
    """Local CTranslate2 Whisper. Model files are cached in <data_dir>/models/whisper."""

    name = "faster_whisper"
    _lock = threading.Lock()
    _model: Any = None
    _loaded_name: str | None = None

    def __init__(self, model: str | None = None) -> None:
        self.model = model or speech_config().stt_model

    def _load(self) -> Any:
        from faster_whisper import WhisperModel

        from app.services.local_speech import cached_path

        compute = get_settings().stt_compute_type or "int8"
        cache_name = f"{self.model}:{compute}"
        with FasterWhisperSTT._lock:
            if FasterWhisperSTT._model is not None and FasterWhisperSTT._loaded_name == cache_name:
                return FasterWhisperSTT._model
            path = cached_path(self.model)
            if path is None:
                raise RuntimeError(
                    "Spracherkennung fehlt. Bitte unter Einstellungen → Stimme herunterladen und einrichten."
                )
            FasterWhisperSTT._model = WhisperModel(str(path), device="cpu", compute_type=compute, local_files_only=True)
            FasterWhisperSTT._loaded_name = cache_name
            return FasterWhisperSTT._model

    def transcribe(self, wav_path: Path, *, session_id: str | None, learner_id: str | None) -> Transcript:
        model = self._load()
        with meter(
            "stt",
            "local",
            "local/faster-whisper",
            "audio_seconds",
            session_id=session_id,
            learner_id=learner_id,
            local=True,
            meta={"model": self.model},
        ) as m:
            segments, info = model.transcribe(
                str(wav_path), language="de", word_timestamps=True, vad_filter=True, beam_size=5
            )
            words: list[Word] = []
            texts: list[str] = []
            for seg in segments:
                texts.append(seg.text.strip())
                for w in seg.words or []:
                    words.append(
                        Word(text=w.word.strip(), start=float(w.start), end=float(w.end), prob=float(w.probability))
                    )
            duration = float(getattr(info, "duration", 0.0) or wav_duration(wav_path))
            m.quantity = duration
            return Transcript(
                text=" ".join(t for t in texts if t),
                words=words,
                duration_s=duration,
                language="de",
                backend=self.name,
                model=f"faster-whisper/{self.model}",
            )


def provider_request_id(resp: Any) -> str | None:
    """Provider request id of a LiteLLM response (body ``id`` or the ``x-request-id`` response header)."""
    rid = getattr(resp, "id", None)
    if rid:
        return str(rid)
    hidden = getattr(resp, "_hidden_params", None) or {}
    headers = hidden.get("additional_headers") if isinstance(hidden, dict) else None
    if isinstance(headers, dict):
        for k in ("x-request-id", "llm_provider-x-request-id", "request-id"):
            if headers.get(k):
                return str(headers[k])
    raw = getattr(resp, "response", None)
    raw_headers = getattr(raw, "headers", None)
    if raw_headers is not None:
        try:
            v = raw_headers.get("x-request-id")
            return str(v) if v else None
        except Exception:  # noqa: BLE001
            return None
    return None


def provider_usage(resp: Any) -> dict[str, Any]:
    """Whatever usage block a speech response carries (numbers only — sanitized again in ``record_usage``)."""
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    if isinstance(u, dict):
        return dict(u)
    dump = getattr(u, "model_dump", None)
    if callable(dump):
        try:
            return dict(dump())
        except Exception:  # noqa: BLE001
            return {}
    return {k: v for k, v in vars(u).items() if not k.startswith("_")} if hasattr(u, "__dict__") else {}


class LiteLLMCloudSTT:
    """Cloud STT via litellm.transcription (OpenAI gpt-4o-mini-transcribe or Mistral Voxtral).

    Billing unit: audio seconds measured from the WAV (pricing.yaml prices per minute); the provider's
    request id and usage block are attached to the usage event (``request_id`` / ``raw_usage``).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        if name == "voxtral":
            self.model = "mistral/voxtral-mini-transcribe-v2"
            self.litellm_model = "mistral/voxtral-mini-transcribe-realtime-latest"  # TODO(verify) batch endpoint id
            self.provider = "mistral"
        else:
            self.model = "openai/gpt-4o-mini-transcribe"
            self.litellm_model = "gpt-4o-mini-transcribe"
            self.provider = "openai"

    def transcribe(self, wav_path: Path, *, session_id: str | None, learner_id: str | None) -> Transcript:
        import litellm

        duration = wav_duration(wav_path)
        api_key = credentials.require_workspace_key(learner_id, self.provider)
        with meter(
            "stt",
            self.provider,
            self.model,
            "audio_seconds",
            session_id=session_id,
            learner_id=learner_id,
            meta={"operation": "transcription"},
        ) as m:
            m.quantity = duration
            with open(wav_path, "rb") as f:
                resp = litellm.transcription(
                    model=self.litellm_model,
                    file=f,
                    language="de",
                    response_format="verbose_json",
                    timestamp_granularities=["word"],
                    **({"api_key": api_key} if api_key else {}),
                )
            rid = provider_request_id(resp)
            if rid:
                m.meta["request_id"] = rid
            raw = provider_usage(resp)
            if raw:
                m.meta["raw_usage"] = raw
            words = [
                Word(text=w.get("word", ""), start=float(w.get("start", 0)), end=float(w.get("end", 0)), prob=1.0)
                for w in (getattr(resp, "words", None) or [])
            ]
            return Transcript(
                text=str(getattr(resp, "text", "")),
                words=words,
                duration_s=duration,
                language="de",
                backend=self.name,
                model=self.model,
            )


class FakeSTT:
    name = "fake"
    model = "fake"

    def transcribe(self, wav_path: Path, *, session_id: str | None, learner_id: str | None) -> Transcript:
        duration = wav_duration(wav_path) or 6.0
        with meter(
            "stt",
            "local",
            "local/faster-whisper",
            "audio_seconds",
            session_id=session_id,
            learner_id=learner_id,
            local=True,
            meta={"fake": True},
        ) as m:
            m.quantity = duration
            return fake_transcript(duration)


STT_BACKENDS = ("faster_whisper", "openai", "voxtral")


class HookedSTT:
    """Backend proxy: ``transcribe`` runs through ``engine.execute`` (pre-hooks may deny)."""

    def __init__(self, inner: STTBackend) -> None:
        self.inner = inner
        self.name = inner.name
        self.model = inner.model

    def transcribe(
        self, wav_path: Path, *, session_id: str | None, learner_id: str | None, state: SessionState | None = None
    ) -> Transcript:
        call = TierCall(
            kind="stt",
            tier="stt",
            model=self.inner.model,
            session_id=session_id,
            learner_id=learner_id,
            payload_summary={"backend": self.inner.name},
        )
        res = engine.execute(
            call,
            state or SessionState(),
            lambda _c: self.inner.transcribe(wav_path, session_id=session_id, learner_id=learner_id),
        )
        if not res.success:
            raise HookDenied(res)
        return Transcript.model_validate(res.result) if isinstance(res.result, dict) else res.result  # type: ignore[no-any-return]


def _raw_stt() -> STTBackend:
    backend = speech_config().stt_backend
    if backend == "fake":
        return FakeSTT()
    if backend in ("openai", "voxtral"):
        return LiteLLMCloudSTT(backend)
    return FasterWhisperSTT()


def get_stt() -> STTBackend:
    return HookedSTT(_raw_stt())


def transcribe(
    wav_path: Path, *, session_id: str | None, learner_id: str | None, state: SessionState | None = None
) -> Transcript:
    return HookedSTT(_raw_stt()).transcribe(wav_path, session_id=session_id, learner_id=learner_id, state=state)


def stt_alternatives(current: str) -> list[str]:
    return [f"STT_BACKEND={b}" for b in STT_BACKENDS if b != current]


def transcribe_safe(
    wav_path: Path, *, session_id: str | None, learner_id: str | None, state: SessionState | None = None
) -> Transcript | FailureContext:
    """``transcribe`` with errors as values: hook denials are ``business``, provider errors ``transient``."""
    backend = speech_config().stt_backend
    try:
        return transcribe(wav_path, session_id=session_id, learner_id=learner_id, state=state)
    except HookDenied as exc:
        return exc.result.error or FailureContext(category="business", message=str(exc))
    except Exception as exc:  # noqa: BLE001 — the hub decides whether this is fatal
        log.exception("STT failed (backend=%s)", backend)
        return FailureContext(
            category="transient",
            message=f"Spracherkennung fehlgeschlagen ({backend}): {str(exc)[:200]}",
            alternatives=stt_alternatives(backend),
        )
