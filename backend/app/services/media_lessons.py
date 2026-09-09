"""Private, timestamped listening lessons from user supplied WebVTT and audio."""
from __future__ import annotations

import hashlib
import html
import io
import random
import re
from collections import OrderedDict
from threading import Lock
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.core.db import db_session
from app.core.prompts import load_prompt
from app.db.base import Learner
from app.db.exams import Exam
from app.services import exams, llm
from app.services.exam_schemas import ExamDraft

VTT_LIMIT = 2 * 1024 * 1024
_TIME = r"(?:\d{2,}:)?[0-5]\d:[0-5]\d\.\d{3}"
_TIMING = re.compile(rf"^({_TIME})\s+-->\s+({_TIME})(?:\s+.*)?$")
_INLINE = re.compile(rf"<{_TIME}>")


def seconds(value: str) -> float:
    result = 0.0
    for part in value.split(":"):
        result = result * 60 + float(part)
    return result


def parse_vtt(data: bytes) -> list[dict[str, Any]]:
    if not data or len(data) > VTT_LIMIT:
        raise ValueError("Bitte eine VTT-Datei bis 2 MB auswählen.")
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    if not re.match(r"^WEBVTT(?:[ \t].*)?\n", text):
        raise ValueError("Die Datei muss ein UTF-8-WebVTT-Transkript sein.")
    cues: list[dict[str, Any]] = []
    lines = text.splitlines()
    index = 1
    previous_display = ""
    previous_fresh = ""
    rolling = bool(_INLINE.search(text))
    while index < len(lines):
        line = lines[index].strip()
        if line in ("STYLE", "REGION") or line == "NOTE" or line.startswith("NOTE "):
            index += 1
            while index < len(lines) and lines[index].strip():
                index += 1
            continue
        match = _TIMING.match(line)
        index += 1
        if not match:
            if "-->" in line:
                raise ValueError("Ungültige Zeitmarke im VTT-Transkript.")
            continue
        start, end = map(seconds, match.groups())
        if end <= start or (cues and start < cues[-1]["start"]):
            raise ValueError("VTT-Zeitmarken müssen aufsteigend sein und eine positive Dauer haben.")
        body = []
        # A space-only display line is meaningful in YouTube's rolling captions.
        while index < len(lines) and lines[index] != "":
            if _TIMING.match(lines[index].strip()):
                break
            body.append(lines[index])
            index += 1
        def clean(value: str) -> str:
            return " ".join(html.unescape(re.sub(r"<[^>]*>", "", value)).replace("\x00", " ").split())
        display = clean(" ".join(body))
        if rolling and len(body) > 1 and clean(body[0]) in (previous_display, previous_fresh):
            body = body[1:]
        fresh = [line for line in body if _INLINE.search(line)]
        value = clean(" ".join(fresh)) if fresh else clean(" ".join(body))
        # Tiny echo cues repaint previous subtitles, not newly spoken words.
        echo = end - start <= 0.021 and display in (previous_display, previous_fresh)
        previous_display = display
        if value and not echo:
            previous_fresh = value
            cues.append({"start": start, "end": end, "text": value})
    if not cues or len(cues) > 30000:
        raise ValueError("Keine nutzbaren Untertitel oder zu viele Untertitel (höchstens 30.000).")
    return cues


def sections(cues: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    for cue in cues:
        if not chunks or cue["end"] - chunks[-1]["start"] > 240 or len(chunks[-1]["text"]) + len(cue["text"]) > 5500:
            chunks.append({"start": cue["start"], "end": cue["end"], "text": cue["text"]})
        else:
            chunks[-1]["text"] += " " + cue["text"]
            chunks[-1]["end"] = cue["end"]
    # A tiny closing fragment needs the preceding context (e.g. a list of film titles).
    if len(chunks) > 1 and len(chunks[-1]["text"]) < 500:
        tail = chunks.pop()
        chunks[-1]["text"] += " " + tail["text"]
        chunks[-1]["end"] = tail["end"]
    if len(chunks) > 40:
        raise ValueError("Bitte eine kürzere Lesung verwenden (höchstens 40 Lernabschnitte).")
    return chunks


def create(owner: str, title: str, transcript: bytes, audio: bytes) -> dict[str, Any]:
    cues = parse_vtt(transcript)
    if not audio or len(audio) > exams.AUDIO_LIMIT:
        raise ValueError("Bitte eine Hördatei bis 100 MB auswählen.")
    duration = exams.audio_duration(audio)
    if cues[-1]["end"] > duration + 1:
        raise ValueError("Das Transkript reicht über die Hördatei hinaus. Bitte zusammengehörige Dateien wählen.")
    for cue in cues:
        cue["end"] = min(cue["end"], duration)
        if cue["start"] >= cue["end"]:
            raise ValueError("Untertitel liegen außerhalb der Hördatei.")
    chunks = sections(cues)
    digest, audio_digest = hashlib.sha256(transcript).hexdigest(), hashlib.sha256(audio).hexdigest()
    title = title.strip()[:255] or "Lesung mit Transkript"
    with db_session() as db:
        if db.execute(select(Learner).where(Learner.id == owner).with_for_update()).scalar_one_or_none() is None:
            raise HTTPException(404, "Lernbereich nicht gefunden.")
        existing = db.execute(select(Exam).where(
            Exam.owner_id == owner, Exam.payload["source_format"].astext == "webvtt",
            Exam.payload["sha256"].astext == digest, Exam.payload["audio_sha256"].astext == audio_digest,
        )).scalar_one_or_none()
        if existing:
            return exams.summary(existing)
        row = Exam(owner_id=owner, title=title, pdf=b"", audio=audio, payload={
            "draft": ExamDraft(title=title, warnings=["Automatische Untertitel können Fehler enthalten. Bitte mit der Aufnahme prüfen."]).model_dump(),
            "source_format": "webvtt", "material_kind": "study", "pages": [c["text"] for c in chunks],
            "media_sections": chunks, "vtt": transcript.decode("utf-8"),
            "sha256": digest, "audio_sha256": audio_digest, "audio_seconds": duration,
            "reviewed": False, "revision": 1, "source": "Privat importierte Lesung mit VTT-Transkript",
        })
        db.add(row)
        db.flush()
        return exams.summary(row)


def extract(identifier: str, owner: str, data: dict[str, Any], first: int, last: int) -> dict[str, Any]:
    if first != last or not 1 <= first <= len(data["media_sections"]):
        raise ValueError("Bitte genau einen Hör-/Leseabschnitt auswählen.")
    chunk = data["media_sections"][first - 1]
    prompt = load_prompt("media_lesson")
    generated = llm.complete(prompt.tier or "generator", [
        {"role": "system", "content": prompt.body},
        {"role": "user", "content": f"Titel: {data['title']}\nAbschnitt {first}:\n{chunk['text']}"},
    ], ExamDraft, prompt_version=prompt.version, prompt_name=prompt.name,
        session_id=f"exam:{identifier}", learner_id=owner, max_tokens=7000)
    if not generated.questions or not generated.learning_notes:
        raise ValueError("Keine vollständige Lerneinheit erzeugt. Bitte diesen Abschnitt erneut versuchen.")
    if len(generated.questions) > 5 or len(generated.learning_notes) > 6:
        raise ValueError("Zu viele Aufgaben oder Erklärungen für einen Lernabschnitt.")
    for note in generated.learning_notes:
        if note.phrase.casefold() not in chunk["text"].casefold():
            raise ValueError("Ein erklärter Ausdruck fehlt im Transkript. Bitte diesen Abschnitt erneut versuchen.")
        note.section = first
    for index, q in enumerate(generated.questions, 1):
        if q.kind != "choice" or len(q.answers) != 1:
            raise ValueError("Verständnisfragen brauchen genau eine bestätigte Auswahlantwort.")
        q.id = f"s{first}-q{index}"
        random.Random(f"{identifier}:{q.id}").shuffle(q.options)
        q.page = first
        q.source_pages = []
        q.audio_start, q.audio_end = chunk["start"], chunk["end"]
        # Always show the actual supplied text, never a model-created substitute.
        q.passage = chunk["text"]
        q.audio_reference = ""
    current = ExamDraft.model_validate(data["draft"])
    current.questions = sorted([q for q in current.questions if q.page != first] + generated.questions, key=lambda q: q.page)
    current.learning_notes = sorted([n for n in current.learning_notes if n.section != first] + generated.learning_notes, key=lambda n: n.section)
    current.warnings = list(dict.fromkeys(current.warnings + generated.warnings))[-50:]
    exams.save(identifier, owner, ExamDraft.model_validate(current.model_dump()), False, data["revision"],
               extracted_pages=sorted(set(data.get("extracted_pages", [])) | {first}))
    return exams.editor(identifier, owner)


_audio_lock = Lock()
_audio_cache: OrderedDict[str, bytes] = OrderedDict()


def audio_clip(data: bytes, start: float, end: float) -> bytes:
    """Return only the question's samples, so playback cannot continue into the next answer."""
    import math

    import av

    if not 0 <= start < end <= 10800:
        raise ValueError("Ungültiger Hörabschnitt.")
    key = f"clip:{hashlib.sha256(data).hexdigest()}:{start}:{end}"
    with _audio_lock:
        if key in _audio_cache:
            _audio_cache.move_to_end(key)
            return _audio_cache[key]
        output = io.BytesIO()
        samples = 0
        with av.open(io.BytesIO(data), mode="r") as source, av.open(output, "w", format="mp3") as target:
            stream = target.add_stream("libmp3lame", rate=44100)
            stream.bit_rate = 64000
            stream.layout = "mono"
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=44100)
            if start > 1:
                source.seek(int((start - 1) * av.time_base))
            def encode(frame: Any) -> None:
                nonlocal samples
                if frame.time is None:
                    raise ValueError("Hördatei enthält keine lesbaren Zeitmarken.")
                first = max(0, math.ceil((start - frame.time) * 44100))
                last = min(frame.samples, math.ceil((end - frame.time) * 44100))
                if first >= last:
                    return
                clipped = av.AudioFrame.from_ndarray(frame.to_ndarray()[:, first:last].copy(), format="fltp", layout="mono")
                clipped.sample_rate = 44100
                samples += last - first
                for packet in stream.encode(clipped):
                    target.mux(packet)
            for frame in source.decode(audio=0):
                if frame.time is not None and frame.time > end + 0.1:
                    break
                for converted in resampler.resample(frame):
                    encode(converted)
            for converted in resampler.resample(None):
                encode(converted)
            if samples / 44100 < end - start - 0.1:
                raise ValueError("Dieser Hörabschnitt fehlt in der Audiodatei. Bitte die vollständige Datei importieren.")
            for packet in stream.encode(None):
                target.mux(packet)
        result = output.getvalue()
        while _audio_cache and sum(map(len, _audio_cache.values())) + len(result) > 100 * 1024 * 1024:
            _audio_cache.popitem(last=False)
        _audio_cache[key] = result
        return result


def playback(data: bytes) -> bytes:
    """Decode WebM locally once to bounded MP3 for playback across desktop browsers."""
    if not data.startswith(b"\x1aE\xdf\xa3"):
        return data
    digest = hashlib.sha256(data).hexdigest()
    with _audio_lock:
        if digest in _audio_cache:
            _audio_cache.move_to_end(digest)
            return _audio_cache[digest]
        import av
        output = io.BytesIO()
        with av.open(io.BytesIO(data), mode="r") as source, av.open(output, "w", format="mp3") as target:
            stream = target.add_stream("libmp3lame", rate=44100)
            stream.bit_rate = 64000
            stream.layout = "mono"
            resampler = av.AudioResampler(format="fltp", layout="mono", rate=44100)
            def encode(frame: Any) -> None:
                frame.pts = None
                for packet in stream.encode(frame):
                    target.mux(packet)
                if output.tell() > 90 * 1024 * 1024:
                    raise ValueError("Hördatei ist für die Wiedergabe zu groß.")
            for frame in source.decode(audio=0):
                for converted in resampler.resample(frame):
                    encode(converted)
            for converted in resampler.resample(None):
                encode(converted)
            for packet in stream.encode(None):
                target.mux(packet)
        result = output.getvalue()
        while _audio_cache and sum(map(len, _audio_cache.values())) + len(result) > 100 * 1024 * 1024:
            _audio_cache.popitem(last=False)
        _audio_cache[digest] = result
        return result
