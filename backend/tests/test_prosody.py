from __future__ import annotations

from app.services.pronunciation import analyze_prosody
from app.services.schemas import Transcript, Word


def _t(tokens: list[tuple[str, float, float, float]], duration: float) -> Transcript:
    return Transcript(
        text=" ".join(t[0] for t in tokens),
        words=[Word(text=a, start=b, end=c, prob=d) for a, b, c, d in tokens],
        duration_s=duration,
    )


def test_wpm_and_tempo() -> None:
    words = [(f"w{i}", i * 0.5, i * 0.5 + 0.4, 0.9) for i in range(20)]
    r = analyze_prosody(_t(words, 10.0))
    assert abs(r.wpm - 120) < 1 and r.tempo_label_de == "angemessen"
    slow = analyze_prosody(_t(words, 30.0))
    assert slow.tempo_label_de == "eher langsam"


def test_long_pauses_and_ratio() -> None:
    words = [("ich", 0.0, 0.3, 0.9), ("bin", 0.4, 0.6, 0.9), ("müde", 2.5, 2.9, 0.9)]
    r = analyze_prosody(_t(words, 3.0))
    assert len(r.long_pauses) == 1 and r.long_pauses[0]["seconds"] == 1.9
    assert r.pause_ratio > 0.5


def test_repetitions_fillers_self_corrections() -> None:
    words = [
        ("ich", 0, 0.2, 0.9),
        ("ich", 0.3, 0.5, 0.9),
        ("gehe", 0.6, 0.9, 0.9),
        ("ähm", 1.0, 1.2, 0.9),
        ("gehe", 1.3, 1.6, 0.9),
        ("also", 1.7, 1.9, 0.9),
    ]
    r = analyze_prosody(_t(words, 2.0))
    assert "ich" in r.repetitions and "gehe" in r.repetitions
    assert r.fillers.get("ähm") == 1 and r.fillers.get("also") == 1 and r.n_fillers == 2
    assert any("ähm" in s for s in r.self_corrections)


def test_low_confidence_flags_are_qualitative() -> None:
    words = [
        ("Prüfung", 0, 0.5, 0.4),
        ("ist", 0.6, 0.7, 0.99),
        ("äh", 0.8, 0.9, 0.2),
        ("Verhandlung", 1.0, 1.6, 0.5),
        ("Übung", 1.7, 2.0, 0.3),
        ("Tag", 2.1, 2.3, 0.1),
    ]
    r = analyze_prosody(_t(words, 2.5))
    assert [f.word for f in r.pronunciation_flags] == ["Prüfung", "Verhandlung", "Übung"]  # max 3, fillers excluded
    assert all(f.reason == "möglicherweise undeutlich" for f in r.pronunciation_flags)
    assert "%" not in r.summary_de()


def test_empty_transcript() -> None:
    r = analyze_prosody(Transcript(text="", words=[], duration_s=0))
    assert r.n_words == 0 and r.wpm == 0
