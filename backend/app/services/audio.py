"""Audio handling (ADR-0011): 16 kHz mono WAV in a temp dir, deleted in ``finally``.

``KEEP_AUDIO=true`` stores an encrypted copy (Fernet) with a retention date instead.
"""

from __future__ import annotations

import io
import logging
import tempfile
import wave
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.db import db_session
from app.db.base import AudioFile

log = logging.getLogger(__name__)
TARGET_RATE = 16000


def to_wav16k(data: bytes) -> bytes:
    """Convert any browser/recorder audio (wav/webm/ogg/mp3/m4a) to 16 kHz mono PCM16 WAV."""
    if _is_pcm16_mono_16k(data):
        return data
    import av
    import numpy as np

    container: Any = av.open(io.BytesIO(data))
    with container:
        stream = next(s for s in container.streams if s.type == "audio")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_RATE)
        chunks: list[bytes] = []
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                arr = out.to_ndarray()
                chunks.append(np.asarray(arr, dtype=np.int16).tobytes())
        for out in resampler.resample(None):
            chunks.append(np.asarray(out.to_ndarray(), dtype=np.int16).tobytes())
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(TARGET_RATE)
        w.writeframes(b"".join(chunks))
    return buf.getvalue()


def _is_pcm16_mono_16k(data: bytes) -> bool:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getnchannels() == 1 and w.getsampwidth() == 2 and w.getframerate() == TARGET_RATE
    except Exception:  # noqa: BLE001
        return False


def wav_seconds(data: bytes) -> float:
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            return w.getnframes() / float(w.getframerate())
    except Exception:  # noqa: BLE001
        return 0.0


@contextmanager
def temp_wav(data: bytes, *, session_id: str | None = None, learner_id: str | None = None) -> Iterator[Path]:
    """Yield a path to a 16 kHz WAV; the file is deleted in ``finally`` (product rule 9)."""
    wav = to_wav16k(data)
    tmpdir = Path(tempfile.mkdtemp(prefix="lernapp-audio-"))
    path = tmpdir / "input.wav"
    path.write_bytes(wav)
    try:
        yield path
    finally:
        try:
            if get_settings().keep_audio:
                _keep_encrypted(wav, session_id=session_id, learner_id=learner_id)
        except Exception:  # noqa: BLE001
            log.exception("could not store encrypted audio copy")
        try:
            path.unlink(missing_ok=True)
            tmpdir.rmdir()
        except OSError:
            log.warning("could not delete temp audio %s", path)


def _keep_encrypted(wav: bytes, *, session_id: str | None, learner_id: str | None) -> None:
    from cryptography.fernet import Fernet

    s = get_settings()
    if not s.audio_encryption_key:
        log.error("KEEP_AUDIO=true but AUDIO_ENCRYPTION_KEY missing — audio NOT kept")
        return
    folder = s.data_path / "audio"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{session_id or 'nosession'}.wav.enc"
    target = folder / name
    target.write_bytes(Fernet(s.audio_encryption_key.encode()).encrypt(wav))
    with db_session() as db:
        db.add(
            AudioFile(
                session_id=session_id,
                learner_id=learner_id,
                path=str(target),
                expires_at=datetime.now(UTC) + timedelta(days=s.audio_retention_days),
            )
        )


def purge_expired_audio() -> int:
    from sqlalchemy import select

    n = 0
    with db_session() as db:
        for f in db.execute(select(AudioFile).where(AudioFile.expires_at < datetime.now(UTC))).scalars():
            Path(f.path).unlink(missing_ok=True)
            db.delete(f)
            n += 1
    return n
