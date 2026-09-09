"""Deterministic fake backends for tests and offline demos (LLM_BACKEND=fake etc.).

They return schema-valid German content and go through the SAME ledger paths as real backends
(with the real model strings, so pricing is exercised).
"""

from __future__ import annotations

import hashlib
import io
import json
import math
import struct
import wave
from typing import Any

from pydantic import BaseModel

from app.core.rubrics import criteria
from app.services.schemas import (
    CriterionScore,
    GeneratedTask,
    LLMCoachOutput,
    LLMRubricOutput,
    PronunciationTip,
    TaskItem,
    Transcript,
    ValidationReport,
    Word,
)

FAKE_TUTOR_REPLY = (
    "Gern! Eine kleine Korrektur: „Ich habe gestern ein Meeting gehabt.“ → „Ich hatte gestern ein "
    "Meeting.“ – im Erzählkontext wirkt das Präteritum hier natürlicher. Worum ging es in dem Meeting?"
)


LOW_CONFIDENCE_MARKER = "unsicher"  # a learner text containing this word gets one criterion rated < 0.85


def _learner_text(prompt: str) -> str | None:
    """The learner's text as delimited in the feedback prompts (``<text>`` / ``<transcript>``)."""
    for tag in ("text", "transcript"):
        start, end = f"<{tag}>", f"</{tag}>"
        if start in prompt and end in prompt:
            return prompt[prompt.index(start) + len(start) : prompt.index(end)].strip()
    return None


def _rubric(rubric_version: str, seed: str, learner_text: str | None = None) -> LLMRubricOutput:
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    words = (learner_text or "").split()
    evidence = [" ".join(words[:4])] if len(words) >= 4 else ([learner_text] if learner_text else [])
    low = LOW_CONFIDENCE_MARKER in (learner_text or "").lower()
    scores = []
    for i, c in enumerate(criteria(rubric_version)):
        # deterministic self-rated confidence in [0.86, 0.99]; the marker drops criterion 0 and 4 below 0.85
        confidence = 0.86 + ((h >> (i * 4)) % 14) / 100
        if low and i in (0, 4):
            confidence = 0.55 + i / 100
        scores.append(
            CriterionScore(
                criterion=c,
                score=2 + ((h >> (i * 3)) % 3),
                evidence=evidence,
                comment_de=f"{c}: weitgehend gelungen, mit kleinen Lücken.",
                confidence=round(confidence, 2),
            )
        )
    return LLMRubricOutput(
        scores=scores,
        error_tags=["text/konnektoren", "wortschatz/wiederholung"],
        better_formulations=[("Ich denke, dass das ein Problem ist.", "Meines Erachtens stellt dies ein Problem dar.")],
        new_vocabulary=["abwägen", "in Anbetracht", "gleichwohl"],
        summary_de=(
            "Du hast die Aufgabe insgesamt gut bearbeitet. Deine Argumente sind nachvollziehbar, "
            "an einigen Stellen fehlen Konnektoren. Achte beim nächsten Mal auf Variation im Wortschatz. "
            "Weiter so!"
        ),
    )


def fake_completion(schema: type[BaseModel] | None, messages: list[dict[str, str]]) -> str:
    """Return JSON (for schemas) or German prose (for free-text prompts)."""
    text = " ".join(m.get("content", "") for m in messages)
    if schema is None:
        if "ROLLENSPIEL ENDE" in text.upper() and "Rollenspiel beendet" not in text:
            return "Rollenspiel beendet."
        if "Verhandlungs-Rollenspiel" in text:
            return "Das ist ein interessanter Vorschlag. Welche Laufzeit hätten Sie sich denn vorgestellt?"
        if "Lese-/Höraufgabe" in text:
            return "Item 1: Die richtige Lösung ergibt sich aus dem zweiten Absatz. Tipp: Achte auf Signalwörter wie „jedoch“."
        return FAKE_TUTOR_REPLY
    name = schema.__name__
    if name == "ExamDraft":
        return json.dumps(
            {
                "title": "Testimport (Demo)",
                "questions": [],
                "warnings": ["Demo-Modus: keine echten Aufgaben extrahiert."],
            }
        )
    if name == "LLMRubricOutput":
        rv = "sprechen_v1" if "sprechen_v1" in text else "schreiben_v1"
        return _rubric(rv, text[-400:], _learner_text(text)).model_dump_json()
    if name == "GeneratedTask":
        deterministic = "expected_answers" in text and ("lesen" in text or "hoeren" in text)
        items = []
        if deterministic:
            for i in range(1, 6):
                items.append(
                    TaskItem(
                        id=str(i),
                        question=f"Aussage {i}: Die Universität plant neue Beratungsangebote.",
                        options=["richtig", "falsch", "Text sagt dazu nichts"],
                        answer=["richtig", "falsch", "Text sagt dazu nichts"][i % 3],
                    )
                )
        task = GeneratedTask(
            title="Studienabbruch: Ursachen und Gegenmaßnahmen",
            instructions_de="Lesen Sie den Text und entscheiden Sie bei jeder Aussage, ob sie richtig oder falsch ist oder ob der Text dazu nichts sagt."
            if deterministic
            else "Fassen Sie die Informationen der Grafik zusammen und nehmen Sie begründet Stellung.",
            source_text=(
                "Die Zahl der Studienabbrüche ist in den vergangenen Jahren gestiegen. Als Ursachen "
                "gelten finanzielle Belastungen, falsche Erwartungen an das Fach und fehlende Beratung. "
                "Mehrere Hochschulen reagieren mit Mentoring-Programmen; ob diese wirken, ist noch offen."
            ),
            graphic_description=None if deterministic else "Balkendiagramm: Abbruchquote 2015–2025 in Prozent.",
            items=items,
            expected_content=[]
            if deterministic
            else ["Grafik zusammenfassen", "Ursachen abwägen", "eigene Position begründen"],
            expected_answers={it.id: it.answer for it in items},
            rubric_ref=None if deterministic else ("sprechen_v1" if "sprechen" in text else "schreiben_v1"),
            language_functions=["zusammenfassen", "abwaegen", "stellung_nehmen"],
        )
        return task.model_dump_json()
    if name == "ValidationReport":
        # Solve "blind": reproduce keys from the task JSON in the prompt if present.
        solved: dict[str, str] = {}
        try:
            start = text.index('"expected_answers"')
            frag = text[start:]
            frag = frag[frag.index("{") : frag.index("}") + 1]
            solved = json.loads(frag)
        except Exception:  # noqa: BLE001
            solved = {}
        return ValidationReport(ok=True, issues=[], solved_answers=solved).model_dump_json()
    if name == "LLMCoachOutput":
        rub = _rubric("sprechen_v1", text[-300:])
        return LLMCoachOutput(
            outcome_vs_goals="Du hast dein Hauptziel teilweise erreicht: Die Gegenseite ist beim Preis entgegengekommen, die Laufzeit blieb offen.",
            moves_used=["anker", "fragen_stellen"],
            moves_missed=["paketloesung", "zusammenfassen"],
            language_feedback="Register überwiegend angemessen; einige umgangssprachliche Wendungen.",
            error_tags=["verhandlung/redemittel_fehlen", "wortschatz/register_zu_umgangssprachlich"],
            missing_redemittel=[
                "Halten wir fest: …",
                "Wenn wir beim Preis entgegenkommen, bräuchten wir im Gegenzug …",
            ],
            better_formulations=[("Das geht so nicht.", "Das kann ich in dieser Form nicht zusagen.")],
            next_focus="Beim nächsten Mal ein Paket anbieten statt nur beim Preis zu verhandeln.",
            rubric_scores=rub.scores,
            rubric_summary_de=rub.summary_de,
        ).model_dump_json()
    if name == "PronunciationTip":
        return PronunciationTip(
            tip_de="Sprich Wörter mit „ü“ etwas gerundeter und mach nach Sinnabschnitten bewusst kurze Pausen.",
            practice_words=["Prüfung", "Übung", "Verhandlung"],
        ).model_dump_json()
    # Generic: try to construct an empty-ish instance.
    return json.dumps({})


def fake_transcript(duration_s: float = 6.0) -> Transcript:
    words_txt = [
        "Ich",
        "möchte",
        "ähm",
        "heute",
        "über",
        "meine",
        "Prüfung",
        "sprechen",
        "und",
        "über",
        "die",
        "Verhandlung",
    ]
    n = len(words_txt)
    step = duration_s / n
    words = [
        Word(
            text=w, start=round(i * step, 2), end=round((i + 1) * step - 0.05, 2), prob=0.55 if w == "Prüfung" else 0.95
        )
        for i, w in enumerate(words_txt)
    ]
    return Transcript(
        text=" ".join(words_txt), words=words, duration_s=duration_s, language="de", backend="fake", model="fake"
    )


def silent_wav(seconds: float = 0.5, rate: int = 16000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        n = int(seconds * rate)
        # faint tone so players show something
        frames = b"".join(struct.pack("<h", int(800 * math.sin(2 * math.pi * 440 * i / rate))) for i in range(n))
        w.writeframes(frames)
    return buf.getvalue()


def fake_embedding(text: str, dim: int = 64) -> list[float]:
    h = hashlib.sha256(text.lower().encode()).digest()
    vals = [((h[i % len(h)] / 255.0) - 0.5) for i in range(dim)]
    # add simple lexical signal so similar texts are closer
    for tok in text.lower().split():
        th = hashlib.md5(tok.encode()).digest()  # noqa: S324
        idx = th[0] % dim
        vals[idx] += 0.5
    norm = math.sqrt(sum(v * v for v in vals)) or 1.0
    return [v / norm for v in vals]


def usage_for(messages: list[dict[str, str]], output: str) -> dict[str, int]:
    tin = sum(len(m.get("content", "")) for m in messages) // 4
    return {"prompt_tokens": max(tin, 1), "completion_tokens": max(len(output) // 4, 1)}


_ = Any
