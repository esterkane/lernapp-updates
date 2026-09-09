"""Local speech setup and short, explicit tests; no learner conversation is created."""

from __future__ import annotations

import io
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.settings import write_env_file
from app.core import credentials
from app.core.config import get_settings
from app.core.workspaces import current_workspace
from app.services import local_speech, local_voice

router = APIRouter(prefix="/settings/speech", tags=["settings"])
Model = Literal["small", "medium", "large-v3-turbo"]
Voice = Literal["piper", "openai_mini_tts", "google_wavenet", "voxtral_tts", "none"]


class RecognitionChoice(BaseModel):
    model: Model = "small"


class VoiceChoice(BaseModel):
    backend: Voice
    voice: Literal["marin", "cedar", "alloy"] | None = None
    speed: float | None = Field(default=None, ge=0.7, le=1.2)


@router.get("/local")
def local_status() -> dict[str, Any]:
    return {"recognition": local_speech.status(), "voice": local_voice.status()}


@router.post("/recognition/install")
def install_recognition(body: RecognitionChoice) -> dict[str, Any]:
    return local_speech.start(body.model)


@router.post("/recognition/activate")
def activate_recognition(body: RecognitionChoice) -> dict[str, Any]:
    if local_speech.cached_path(body.model) is None:
        raise HTTPException(400, "Bitte die Spracherkennung zuerst herunterladen und einrichten.")
    write_env_file({"STT_BACKEND": "faster_whisper", "STT_MODEL": body.model, "STT_COMPUTE_TYPE": "int8"})
    return {"ok": True}


def _transcribe(data: bytes, model: str, owner: str) -> dict[str, Any]:
    from app.services.stt import FasterWhisperSTT, HookedSTT

    started = time.monotonic()
    # Test recordings are always deleted, even when KEEP_AUDIO is enabled for learning sessions.
    with tempfile.TemporaryDirectory(prefix="lernapp-mic-test-") as folder:
        path = Path(folder) / "test.wav"
        path.write_bytes(data)
        result = HookedSTT(FasterWhisperSTT(model)).transcribe(path, session_id="speech-setup", learner_id=owner)
    return {"text": result.text, "elapsed_seconds": round(time.monotonic() - started, 2), "local": True}


@router.post("/recognition/test")
async def test_recognition(model: Model = Form("small"), file: UploadFile = File(...)) -> dict[str, Any]:
    data = await file.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise HTTPException(400, "Bitte höchstens 20 Sekunden aufnehmen.")
    try:
        with wave.open(io.BytesIO(data), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getframerate() != 16000:
                raise ValueError()
            duration = wav.getnframes() / wav.getframerate()
            if not 0.5 <= duration <= 20:
                raise ValueError()
    except Exception as exc:
        raise HTTPException(400, "Bitte mit dem Mikrofon 1 bis 20 Sekunden aufnehmen.") from exc
    if local_speech.cached_path(model) is None:
        raise HTTPException(400, "Bitte die Spracherkennung zuerst herunterladen und einrichten.")
    try:
        return await run_in_threadpool(_transcribe, data, model, current_workspace())
    except Exception as exc:
        raise HTTPException(
            400, "Die lokale Spracherkennung konnte die Aufnahme nicht verarbeiten. Bitte erneut einrichten und testen."
        ) from exc


def _check_voice(backend: Voice) -> None:
    if backend == "piper" and not local_voice.status()["ready"]:
        raise HTTPException(400, "Bitte die lokale Stimme zuerst herunterladen und einrichten.")
    provider = {"openai_mini_tts": "openai", "voxtral_tts": "mistral"}.get(backend)
    if provider and not credentials.has_key(current_workspace(), provider):
        raise HTTPException(400, "Bitte zuerst den Anbieter unter Verbindungen einrichten.")
    if backend == "google_wavenet" and (
        not get_settings().google_application_credentials or current_workspace() != get_settings().default_learner_id
    ):
        raise HTTPException(400, "Bitte zuerst die Google-Stimme unter Weitere Spracheinstellungen einrichten.")


@router.post("/voice/activate")
def activate_voice(body: VoiceChoice) -> dict[str, Any]:
    _check_voice(body.backend)
    values: dict[str, str | None] = {"TTS_BACKEND": body.backend}
    if body.backend == "openai_mini_tts" and body.voice is not None:
        values["OPENAI_TTS_VOICE"] = body.voice
    if body.backend in ("piper", "openai_mini_tts") and body.speed is not None:
        values["TTS_SPEED"] = str(body.speed)
    write_env_file(values)
    return {"ok": True}


@router.post("/voice/test")
def test_voice(body: VoiceChoice) -> Response:
    from app.services.tts import GoogleWavenetTTS, HookedTTS, OpenAIMiniTTS, PiperTTS, VoxtralTTS

    _check_voice(body.backend)
    factories = {
        "piper": PiperTTS,
        "openai_mini_tts": OpenAIMiniTTS,
        "google_wavenet": GoogleWavenetTTS,
        "voxtral_tts": VoxtralTTS,
    }
    if body.backend not in factories:
        raise HTTPException(400, "Bitte zuerst eine Stimme auswählen.")
    try:
        backend = (
            OpenAIMiniTTS(voice=body.voice, speed=body.speed)
            if body.backend == "openai_mini_tts"
            else PiperTTS(speed=body.speed)
            if body.backend == "piper"
            else factories[body.backend]()
        )
        audio = HookedTTS(backend).synthesize(
            "Wir üben Deutsch für den Beruf. Cash bedeutet Bargeld. Accounts Receivable sind Forderungen aus Lieferungen und Leistungen. Könnten Sie die Rechnung bitte prüfen?",
            session_id="speech-setup",
            learner_id=current_workspace(),
        )
    except Exception as exc:
        raise HTTPException(
            400, "Die Stimme konnte nicht abgespielt werden. Bitte die Einrichtung prüfen und erneut versuchen."
        ) from exc
    return Response(audio.data, media_type=audio.mime, headers={"Cache-Control": "no-store"})
