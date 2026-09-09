"""Imported tests: originals and solutions never enter the general document index."""

from __future__ import annotations

import base64
import hashlib
import io
import json
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import select

from app.core.db import db_session
from app.core.prompts import load_prompt
from app.db.base import Learner, now_utc
from app.db.exams import Exam, ExamAttempt
from app.services import llm
from app.services.exam_schemas import ExamDraft

PDF_LIMIT = 25 * 1024 * 1024
AUDIO_LIMIT = 100 * 1024 * 1024


def owned(db: Any, model: Any, identifier: str, owner: str, *, lock: bool = False) -> Any:
    stmt = select(model).where(model.id == identifier, model.owner_id == owner)
    if lock:
        stmt = stmt.with_for_update()
    row = db.execute(stmt).scalar_one_or_none()
    if row is None:
        raise HTTPException(404, "Modelltest oder Versuch in diesem Lernbereich nicht gefunden.")
    return row


def audio_duration(data: bytes) -> float:
    import av

    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            if not container.streams.audio:
                raise ValueError("Keine Audiospur gefunden.")
            declared = float(container.duration or 0) / av.time_base
            if declared > 3 * 3600:
                raise ValueError("Die Audiodauer muss zwischen 0 und 180 Minuten liegen.")
            duration = 0.0
            for frame in container.decode(audio=0):
                duration += frame.samples / frame.sample_rate
                if duration > 3 * 3600:
                    raise ValueError("Die Audiodauer muss zwischen 0 und 180 Minuten liegen.")
            if duration <= 0 or declared - duration > 1:
                raise ValueError("Audiodatei unvollständig.")
            return duration
    except Exception as exc:
        raise ValueError("Audiodatei ist unvollständig oder konnte nicht gelesen werden. Bitte die vollständige MP3-, WAV- oder WEBM-Datei auswählen (höchstens 180 Minuten).") from exc


def create(owner: str, filename: str, pdf: bytes, audio: bytes | None = None, source: str = "") -> dict[str, Any]:
    if not pdf.startswith(b"%PDF-") or len(pdf) > PDF_LIMIT:
        raise ValueError("Bitte eine gültige PDF-Datei bis 25 MB hochladen.")
    if audio is not None and (not audio or len(audio) > AUDIO_LIMIT):
        raise ValueError("Audio muss zwischen 1 Byte und 100 MB groß sein.")
    try:
        reader = PdfReader(io.BytesIO(pdf))
        if reader.is_encrypted or not 1 <= len(reader.pages) <= 150:
            raise ValueError("PDF muss unverschlüsselt sein und 1–150 Seiten enthalten.")
        pages = [(p.extract_text() or "").replace("\x00", " ") for p in reader.pages]
    except Exception as exc:
        raise ValueError("PDF konnte nicht gelesen werden (unverschlüsselt, höchstens 150 Seiten).") from exc
    title = filename.rsplit(".", 1)[0][:255] or "Modelltest"
    duration = audio_duration(audio) if audio else 0
    draft = ExamDraft(title=title, warnings=["Import prüfen: Aufgaben und Lösungen wurden noch nicht übernommen."])
    with db_session() as db:
        if db.get(Learner, owner) is None:
            raise HTTPException(404, "Lernbereich nicht gefunden.")
        row = Exam(
            owner_id=owner,
            title=title,
            pdf=pdf,
            audio=audio,
            payload={
                "draft": draft.model_dump(),
                "pages": pages,
                "sha256": hashlib.sha256(pdf).hexdigest(),
                "audio_sha256": hashlib.sha256(audio).hexdigest() if audio else None,
                "reviewed": False,
                "source": source,
                "audio_seconds": duration,
                "revision": 1,
            },
        )
        db.add(row)
        db.flush()
        return summary(row)


def summary(row: Exam) -> dict[str, Any]:
    return {
        "id": row.id,
        "title": row.title,
        "created_at": row.created_at.isoformat(),
        "reviewed": row.payload.get("reviewed", False),
        "level": row.payload["draft"].get("level"),
        "question_count": len(row.payload["draft"].get("questions", [])),
        "audio_seconds": row.payload.get("audio_seconds", 0),
        "source": row.payload.get("source", ""),
        "source_format": row.payload.get("source_format", "pdf"),
        "material_kind": row.payload.get("material_kind", "test"),
        "source_materials": row.payload.get("source_materials", []),
        "rag_document_id": row.payload.get("rag_document_id"),
        "sha256": row.payload.get("sha256"),
        "audio_sha256": row.payload.get("audio_sha256"),
    }


def list_exams(owner: str) -> list[dict[str, Any]]:
    with db_session() as db:
        return [
            summary(r)
            for r in db.execute(select(Exam).where(Exam.owner_id == owner).order_by(Exam.created_at.desc())).scalars()
        ]


def editor(identifier: str, owner: str) -> dict[str, Any]:
    with db_session() as db:
        row = owned(db, Exam, identifier, owner)
        return {**summary(row), **row.payload}


def save(
    identifier: str, owner: str, draft: ExamDraft, reviewed: bool, revision: int,
    *, extracted_pages: list[int] | None = None,
) -> dict[str, Any]:
    with db_session() as db:
        row = owned(db, Exam, identifier, owner, lock=True)
        if revision != row.payload.get("revision", 1):
            raise HTTPException(409, "Der Entwurf wurde geändert. Bitte neu laden.")
        if row.payload.get("source_format") == "webvtt":
            if any(q.source_pages for q in draft.questions):
                raise ValueError("Lesungen haben Transkriptabschnitte, keine PDF-Seiten.")
            if any(n.section > len(row.payload["pages"]) for n in draft.learning_notes):
                raise ValueError("Der Transkriptabschnitt für eine Erklärung existiert nicht.")
            if reviewed and len(row.payload.get("extracted_pages", [])) < len(row.payload["pages"]):
                raise ValueError("Bitte zuerst alle Transkriptabschnitte vorbereiten.")
        for q in draft.questions:
            if q.page > len(row.payload["pages"]) or any(p > len(row.payload["pages"]) for p in q.source_pages):
                raise ValueError(f"Frage {q.id}: PDF-Seite existiert nicht.")
            if q.audio_start is not None or q.audio_end is not None:
                duration = row.payload.get("audio_seconds", 0)
                if not duration or (q.audio_start or 0) >= duration or (q.audio_end or 0) > duration:
                    raise ValueError(f"Frage {q.id}: Audio-Zeitbereich liegt außerhalb der Aufnahme.")
            if reviewed and q.kind in ("choice", "text") and not q.answers:
                raise ValueError(f"Frage {q.id}: Für automatische Auswertung fehlt ein bestätigter Lösungsschlüssel.")
        if reviewed and not draft.questions:
            raise ValueError("Bitte zuerst Fragen anlegen.")
        row.title = draft.title
        row.payload = {**row.payload, "draft": draft.model_dump(), "reviewed": reviewed, "revision": revision + 1}
        if extracted_pages is not None:
            row.payload = {**row.payload, "extracted_pages": extracted_pages}
        return {**summary(row), "revision": revision + 1}


def extract(identifier: str, owner: str, first: int, last: int) -> dict[str, Any]:
    data = editor(identifier, owner)
    if data.get("source_format") == "webvtt":
        from app.services import media_lessons
        return media_lessons.extract(identifier, owner, data, first, last)
    pages = data["pages"]
    if not 1 <= first <= last <= len(pages) or last - first >= 8:
        raise ValueError("Bitte 1 bis 8 zusammenhängende Aufgabenseiten wählen.")
    source = "\n\n".join(f"[PDF-Seite {i}]\n{text}" for i, text in enumerate(pages, 1))
    if not source.strip() or sum(map(len, pages)) < 30:
        raise ValueError("Kein lesbarer Text: Diese PDF benötigt OCR. Fragen können manuell angelegt werden.")
    if len(source) > 180000:
        raise ValueError("Zu viel Text für einen Import. Bitte eine kleinere PDF-Datei verwenden.")
    prompt = load_prompt("exam_import")
    draft = llm.complete(
        prompt.tier or "generator",
        [
            {"role": "system", "content": prompt.body},
            {"role": "user", "content": f"Aufgabenseiten {first} bis {last}.\n\nAUSSCHLIESSLICH HIER AUFGABEN EXTRAHIEREN:\n"
                + "\n\n".join(f"[PDF-Seite {i}]\n{pages[i - 1]}" for i in range(first, last + 1))
                + f"\n\nGESAMTDOKUMENT NUR ALS KONTEXT (keine zusätzlichen Aufgaben):\n{source}"},
        ],
        ExamDraft,
        prompt_version=prompt.version,
        prompt_name=prompt.name,
        session_id=f"exam:{identifier}",
        learner_id=owner,
        max_tokens=14000,
    )
    # Append selected pages, replacing only previous questions from those pages.
    current = ExamDraft.model_validate(data["draft"])
    kept = [q for q in current.questions if not first <= q.page <= last]
    for index, q in enumerate(draft.questions, 1):
        if not first <= q.page <= last:
            raise ValueError("Import enthält Fragen außerhalb der ausgewählten Seiten. Entwurf bleibt erhalten.")
        q.id = f"p{q.page}-{index}"
        q.audio_start = q.audio_end = None
        q.source_pages = sorted(set(q.source_pages or [q.page]))
    current.questions = sorted(kept + draft.questions, key=lambda q: q.page)
    current.warnings = list(dict.fromkeys(
        current.warnings + draft.warnings
        + ["KI-Import: Lesetexte, Optionen und Lösungen mit dem Original abgleichen."]
    ))[-50:]
    completed = sorted(set(data.get("extracted_pages", [])) | set(range(first, last + 1)))
    save(identifier, owner, ExamDraft.model_validate(current.model_dump()), False, data["revision"],
         extracted_pages=completed)
    return editor(identifier, owner)


def asset(identifier: str, owner: str, kind: str) -> bytes:
    with db_session() as db:
        row = owned(db, Exam, identifier, owner)
        if kind == "transcript":
            if not row.payload.get("vtt"):
                raise HTTPException(404, "Kein VTT-Transkript vorhanden.")
            return str(row.payload["vtt"]).encode("utf-8")
        value = row.pdf if kind == "pdf" else row.audio
        if not value:
            raise HTTPException(404, "Datei nicht vorhanden.")
        return bytes(value)


def start(identifier: str, owner: str, timed: bool) -> dict[str, Any]:
    with db_session() as db:
        row = owned(db, Exam, identifier, owner, lock=True)
        if not row.payload.get("reviewed"):
            raise HTTPException(409, "Bitte den Entwurf zuerst prüfen und freigeben.")
        snapshot = {**row.payload["draft"], "audio_seconds": row.payload.get("audio_seconds", 0),
                    "source_format": row.payload.get("source_format", "pdf")}
        if not timed:
            snapshot["duration_minutes"] = 0
        attempt = ExamAttempt(exam_id=row.id, owner_id=owner, snapshot=snapshot)
        db.add(attempt)
        db.flush()
        return attempt_dict(attempt)


def attempt_dict(row: ExamAttempt) -> dict[str, Any]:
    safe = {
        **row.snapshot,
        "questions": [
            {k: v for k, v in q.items() if k not in ("answers", "explanation", "audio_reference")}
            for q in row.snapshot["questions"]
        ],
    }
    safe.pop("warnings", None)
    progress = safe.pop("_progress", {"answers": {}, "position": 0, "revision": 0})
    return {
        "id": row.id,
        "exam_id": row.exam_id,
        "test": safe,
        "started_at": row.started_at.isoformat(),
        "submitted_at": row.submitted_at.isoformat() if row.submitted_at else None,
        "result": row.result,
        "progress": progress,
    }


def get_attempt(identifier: str, owner: str) -> dict[str, Any]:
    with db_session() as db:
        row = owned(db, ExamAttempt, identifier, owner)
        exam = owned(db, Exam, row.exam_id, owner)
        return {**attempt_dict(row), "outdated": row.snapshot.get("questions") != exam.payload["draft"]["questions"]}


def attempts(owner: str) -> list[dict[str, Any]]:
    with db_session() as db:
        return [
            {
                "id": r.id,
                "exam_id": r.exam_id,
                "title": r.snapshot["title"],
                "started_at": r.started_at.isoformat(),
                "submitted_at": r.submitted_at.isoformat() if r.submitted_at else None,
                "score": (r.result or {}).get("score"),
                "score_max": (r.result or {}).get("score_max"),
                "answered": sum(bool(a.strip()) for a in r.snapshot.get("_progress", {}).get("answers", {}).values()),
            }
            for r in db.execute(
                select(ExamAttempt)
                .where(ExamAttempt.owner_id == owner)
                .order_by(ExamAttempt.started_at.desc())
                .limit(100)
            ).scalars()
        ]


def save_progress(identifier: str, owner: str, answers: dict[str, str], position: int, revision: int,
                  reset: bool = False) -> dict[str, Any]:
    with db_session() as db:
        row = owned(db, ExamAttempt, identifier, owner, lock=True)
        if row.submitted_at is not None:
            raise HTTPException(409, "Dieser Versuch ist bereits abgegeben. Bitte eine neue Übung starten.")
        previous = row.snapshot.get("_progress", {})
        if revision != previous.get("revision", 0):
            raise HTTPException(409, "Der Lernstand wurde in einem anderen Fenster geändert. Bitte die Seite neu laden.")
        questions = {q["id"]: q for q in row.snapshot["questions"]}
        if not 0 <= position < len(questions) or set(answers) - set(questions):
            raise ValueError("Unbekannte Aufgabe.")
        for key, answer in answers.items():
            if len(answer) > 20000 or (questions[key]["kind"] == "choice" and answer and answer not in questions[key]["options"]):
                raise ValueError("Ungültige Antwort.")
        progress: dict[str, Any] = {"answers": {} if reset else {**previous.get("answers", {}), **answers},
                    "position": 0 if reset else position, "revision": revision + 1,
                    "saved_at": now_utc().isoformat()}
        progress["checked"] = {} if reset else {
            key: item for key, item in previous.get("checked", {}).items()
            if item["answer"] == progress["answers"].get(key)
        }
        row.snapshot = {**row.snapshot, "_progress": progress}
        if reset:
            row.started_at = now_utc()
        return progress


def check_answer(identifier: str, owner: str, question_id: str, answer: str, revision: int) -> dict[str, Any]:
    """Reveal only the requested answer in an untimed listening lesson, and persist it."""
    with db_session() as db:
        row = owned(db, ExamAttempt, identifier, owner, lock=True)
        if row.submitted_at or row.snapshot.get("source_format") != "webvtt" or row.snapshot.get("duration_minutes"):
            raise ValueError("Einzelprüfung ist nur in einer laufenden Lesungsübung ohne Zeitlimit möglich.")
        previous = row.snapshot.get("_progress", {})
        if revision != previous.get("revision", 0):
            raise HTTPException(409, "Der Lernstand wurde in einem anderen Fenster geändert. Bitte die Seite neu laden.")
        q = next((q for q in row.snapshot["questions"] if q["id"] == question_id), None)
        if q is None or not answer.strip() or len(answer) > 20000:
            raise ValueError("Bitte eine gültige Antwort eingeben.")
        if q["kind"] == "choice" and answer not in q["options"]:
            raise ValueError("Ungültige Antwortoption.")
        automatic = q["kind"] in ("choice", "text") and bool(q["answers"])
        item = {"answer": answer, "correct": answer.strip() in [a.strip() for a in q["answers"]] if automatic else None,
                "expected": q["answers"], "explanation": q["explanation"]}
        progress = {**previous, "answers": {**previous.get("answers", {}), question_id: answer},
                    "checked": {**previous.get("checked", {}), question_id: item},
                    "position": next(i for i, q in enumerate(row.snapshot["questions"]) if q["id"] == question_id),
                    "revision": revision + 1, "saved_at": now_utc().isoformat()}
        row.snapshot = {**row.snapshot, "_progress": progress}
        return progress


def question_audio(identifier: str, owner: str, question_id: str) -> bytes:
    from app.services.media_lessons import audio_clip

    with db_session() as db:
        attempt = owned(db, ExamAttempt, identifier, owner)
        q = next((q for q in attempt.snapshot["questions"] if q["id"] == question_id), None)
        if q is None or q.get("audio_start") is None or q.get("audio_end") is None:
            raise ValueError("Für diese Aufgabe ist kein Hörabschnitt hinterlegt.")
        exam = owned(db, Exam, attempt.exam_id, owner)
        if not exam.audio:
            raise ValueError("Keine Hördatei vorhanden.")
        data = bytes(exam.audio)
    return audio_clip(data, q["audio_start"], q["audio_end"])


def submit(identifier: str, owner: str, answers: dict[str, str]) -> dict[str, Any]:
    with db_session() as db:
        row = owned(db, ExamAttempt, identifier, owner, lock=True)
        if row.result is not None:
            return attempt_dict(row)
        questions = row.snapshot["questions"]
        if set(answers) - {q["id"] for q in questions}:
            raise ValueError("Unbekannte Frage.")
        if any(len(a) > 20000 for a in answers.values()):
            raise ValueError("Eine Antwort ist zu lang (höchstens 20.000 Zeichen).")
        score = maximum = 0.0
        marked = []
        for q in questions:
            answer = answers.get(q["id"], "")
            if q["kind"] == "choice" and answer and answer not in q["options"]:
                raise ValueError("Ungültige Antwortoption.")
            automatic = q["kind"] in ("choice", "text") and bool(q["answers"])
            correct = (answer.strip() in [a.strip() for a in q["answers"]]) if automatic else None
            if automatic:
                maximum += q["points"]
                score += q["points"] if correct else 0
            marked.append(
                {
                    "id": q["id"],
                    "answer": answer,
                    "correct": correct,
                    "expected": q["answers"],
                    "explanation": q["explanation"],
                    "points": q["points"],
                }
            )
        now = datetime.now(UTC)
        elapsed = (now - row.started_at).total_seconds()
        limit = row.snapshot.get("duration_minutes", 0) * 60
        row.submitted_at = now
        row.result = {
            "score": score,
            "score_max": maximum,
            "items": marked,
            "elapsed_seconds": elapsed,
            "time_exceeded": bool(limit and elapsed > limit),
            "label": "Interne Übungsauswertung",
        }
        return attempt_dict(row)


def delete(identifier: str, owner: str) -> None:
    with db_session() as db:
        db.delete(owned(db, Exam, identifier, owner))


def export_data(owner: str) -> str:
    with db_session() as db:
        exams = [
            {
                "id": r.id,
                "title": r.title,
                "payload": r.payload,
                "pdf_b64": base64.b64encode(r.pdf).decode(),
                "audio_b64": base64.b64encode(r.audio).decode() if r.audio else None,
            }
            for r in db.execute(select(Exam).where(Exam.owner_id == owner)).scalars()
        ]
        history = [
            {**attempt_dict(r), "snapshot": r.snapshot}
            for r in db.execute(select(ExamAttempt).where(ExamAttempt.owner_id == owner)).scalars()
        ]
    return json.dumps({"exams": exams, "attempts": history}, ensure_ascii=False)


def attach_audio(identifier: str, owner: str, data: bytes) -> dict[str, Any]:
    if not data or len(data) > AUDIO_LIMIT:
        raise ValueError("Bitte eine Audiodatei bis 100 MB auswählen.")
    duration = audio_duration(data)
    with db_session() as db:
        row = owned(db, Exam, identifier, owner, lock=True)
        if db.execute(select(ExamAttempt.id).where(ExamAttempt.exam_id == identifier).limit(1)).first():
            raise HTTPException(
                409, "Dieser Test hat bereits Versuche. Für eine andere Hördatei bitte einen neuen Test importieren."
            )
        if row.payload.get("source_format") == "webvtt":
            raise ValueError("Für eine andere Aufnahme bitte die Lesung mit passendem VTT neu importieren.")
        draft = ExamDraft.model_validate(row.payload["draft"])
        for q in draft.questions:
            q.audio_start = q.audio_end = None
        row.audio = data
        row.payload = {
            **row.payload,
            "draft": draft.model_dump(),
            "audio_seconds": duration,
            "reviewed": False,
            "revision": row.payload["revision"] + 1,
        }
        row.payload.pop("transcript", None)
        row.payload.pop("audio_suggestions", None)
        return summary(row)


def suggest_audio(questions: list[dict[str, Any]], words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Conservative token-overlap suggestions anchored to actual transcribed word times."""
    import re

    def tokens(text: str) -> set[str]:
        return {t for t in re.findall(r"\w+", text.casefold()) if len(t) > 3}

    windows = []
    for i in range(0, len(words), 8):
        segment = words[i : i + 50]
        if segment:
            text = " ".join(w["text"] for w in segment)
            windows.append((tokens(text), segment[0]["start"], segment[-1]["end"], text))
    suggestions = []
    for q in questions:
        reference = q.get("audio_reference") or ""
        if not reference:
            continue
        target = tokens(reference)
        if len(target) < 4:
            continue
        best = max(windows, key=lambda w: len(target & w[0]) / len(target), default=None)
        if best is None:
            continue
        coverage = len(target & best[0]) / len(target)
        if coverage >= 0.6 and len(target & best[0]) >= 4:
            suggestions.append(
                {"id": q["id"], "start": best[1], "end": best[2], "coverage": round(coverage, 2), "excerpt": best[3]}
            )
    return suggestions


def transcribe_audio(identifier: str, owner: str) -> dict[str, Any]:
    from app.services import audio, stt

    info = editor(identifier, owner)
    if not info.get("audio_seconds"):
        raise ValueError("Bitte zuerst eine Hördatei hinzufügen.")
    if info["audio_seconds"] > 3600:
        raise ValueError("Automatische Zuordnung unterstützt bis 60 Minuten. Längere Dateien bitte manuell zuordnen.")
    if info.get("transcript"):
        transcript = info["transcript"]
    else:
        data = asset(identifier, owner, "audio")
        with audio.temp_wav(data, session_id=f"exam:{identifier}", learner_id=owner) as path:
            result = stt.transcribe_safe(path, session_id=f"exam:{identifier}", learner_id=owner)
            from app.core.errors import FailureContext

            if isinstance(result, FailureContext):
                raise HTTPException(502, "Transkription fehlgeschlagen. Bitte Spracherkennung in Einstellungen prüfen.")
        transcript = result.model_dump(mode="json")
    suggestions = suggest_audio(info["draft"]["questions"], transcript.get("words", []))
    with db_session() as db:
        row = owned(db, Exam, identifier, owner, lock=True)
        if row.payload["revision"] != info["revision"]:
            raise HTTPException(409, "Der Entwurf wurde zwischenzeitlich geändert. Bitte erneut zuordnen.")
        row.payload = {
            **row.payload,
            "transcript": transcript,
            "audio_suggestions": suggestions,
            "revision": info["revision"] + 1,
        }
    return {"suggestions": suggestions, "word_count": len(transcript.get("words", []))}
