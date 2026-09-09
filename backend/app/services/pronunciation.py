"""Prosody MVP (ADR-0010, pronunciation-analysis skill). Qualitative output only.

Input: ``Transcript`` with word timestamps + probabilities. Output: ``ProsodyReport``.
Thresholds live in ``config/prosody.yaml``. Phone-level analysis (MFA + wav2vec2-GOP) is Phase 3.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from app.core.paths import repo_root
from app.services.schemas import PronunciationFlag, ProsodyReport, Transcript

_PUNCT = re.compile(r"[^\wäöüßÄÖÜ-]+", re.U)


@lru_cache(maxsize=1)
def thresholds() -> dict[str, Any]:
    p: Path = repo_root() / "config" / "prosody.yaml"
    return dict(yaml.safe_load(p.read_text(encoding="utf-8")))


def _norm(w: str) -> str:
    return _PUNCT.sub("", w.lower()).strip("-")


def analyze_prosody(transcript: Transcript) -> ProsodyReport:
    th = thresholds()
    words = [w for w in transcript.words if _norm(w.text)]
    duration = transcript.duration_s or (words[-1].end if words else 0.0)
    n = len(words)
    report = ProsodyReport(duration_s=duration, n_words=n)
    if n == 0 or duration <= 0:
        report.tempo_label_de = "keine Sprache erkannt"
        return report

    # tempo
    report.wpm = n / (duration / 60.0)
    t = th.get("tempo", {})
    if report.wpm < float(t.get("slow_below_wpm", 90)):
        report.tempo_label_de = "eher langsam"
    elif report.wpm > float(t.get("fast_above_wpm", 170)):
        report.tempo_label_de = "eher zügig"
    else:
        report.tempo_label_de = "angemessen"

    # pauses
    long_pause = float(th.get("long_pause_seconds", 1.5))
    pause_total = 0.0
    for a, b in zip(words, words[1:], strict=False):
        gap = b.start - a.end
        if gap > 0:
            pause_total += gap
        if gap > long_pause:
            report.long_pauses.append({"start": round(a.end, 2), "end": round(b.start, 2), "seconds": round(gap, 2)})
    report.pause_ratio = round(pause_total / duration, 3) if duration else 0.0

    # repetitions (same token within window) and self-corrections
    window = int(th.get("repetition_window_words", 2))
    fillers = [f.lower() for f in th.get("fillers", [])]
    markers = {m.lower() for m in th.get("self_correction_markers", [])} | set(fillers)
    toks = [_norm(w.text) for w in words]
    for i, tok in enumerate(toks):
        for j in range(max(0, i - window), i):
            if toks[j] == tok and len(tok) >= 2:
                report.repetitions.append(tok)
                between = toks[j + 1 : i]
                if any(m in between for m in markers):
                    report.self_corrections.append(" ".join(toks[j : i + 1]))
                break

    # fillers
    joined = " " + " ".join(toks) + " "
    for f in fillers:
        c = joined.count(" " + f + " ")
        if c:
            report.fillers[f] = c
    report.n_fillers = sum(report.fillers.values())

    # low-confidence words → qualitative flags
    min_prob = float(th.get("low_confidence_prob", 0.6))
    min_chars = int(th.get("low_confidence_min_chars", 3))
    max_flags = int(th.get("max_flags", 3))
    seen: set[str] = set()
    for w in words:
        tok = _norm(w.text)
        if w.prob < min_prob and len(tok) >= min_chars and tok not in fillers and tok not in seen:
            seen.add(tok)
            report.low_confidence_words.append(w.text.strip())
            if len(report.pronunciation_flags) < max_flags:
                report.pronunciation_flags.append(
                    PronunciationFlag(
                        word=w.text.strip(), reason="möglicherweise undeutlich", position=round(w.start, 2)
                    )
                )
    return report


def analyze_prosody_metered(transcript: Transcript, *, session_id: str | None, learner_id: str | None) -> ProsodyReport:
    """``analyze_prosody`` with a local ``pron`` ledger row (ADR-0006: local stages are logged too)."""
    from app.core.ledger import meter

    with meter(
        "pron", "local", "local/prosody-mvp", "audio_seconds", session_id=session_id, learner_id=learner_id, local=True
    ) as m:
        m.quantity = float(transcript.duration_s or 0.0)
        return analyze_prosody(transcript)


def flags_for_prompt(report: ProsodyReport) -> dict[str, Any]:
    """Compact, score-free dict for prompts (speaking_feedback / pronunciation_tip)."""
    return {
        "wpm": round(report.wpm),
        "tempo": report.tempo_label_de,
        "long_pauses": len(report.long_pauses),
        "repetitions": report.repetitions[:5],
        "self_corrections": report.self_corrections[:3],
        "fillers": report.fillers,
        "low_confidence_words": report.low_confidence_words[:5],
        "flags": [f.model_dump() for f in report.pronunciation_flags],
        "phone_flags": [f.model_dump() for f in report.phone_flags],
    }
