"""Internal assessment rubrics (assessment-rubrics skill, ADR-0004). Never TDN."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from app.core.paths import repo_root

SCORE_MIN = 0
SCORE_MAX = 4
N_CRITERIA = 7
TOTAL_MAX = N_CRITERIA * SCORE_MAX  # 28
INTERNAL_SCALE_MAX = 20

ANCHORS_DE = {
    0: "nicht erkennbar",
    1: "ansatzweise",
    2: "teilweise / mit Lücken",
    3: "weitgehend",
    4: "durchgängig auf C1-Niveau",
}

RUBRICS: dict[str, list[tuple[str, str]]] = {
    "schreiben_v1": [
        ("aufgabenbezug", "Aufgaben-/Themenbearbeitung, Vollständigkeit"),
        (
            "sprachfunktionen",
            "geforderte Sprachfunktionen (zusammenfassen, abwägen, kritisieren, Stellung nehmen, begründen, vorschlagen)",
        ),
        ("quellennutzung", "Umgang mit Quellen (Wiedergabe, keine Kopie)"),
        ("eigenformulierung", "eigene Formulierungen, Textkohärenz"),
        ("praezision", "präziser, situationsangemessener Ausdruck / Register"),
        ("variation", "Variation in Grammatik, Wortschatz, Redemitteln"),
        ("korrektheit", "grammatische/orthografische Korrektheit"),
    ],
    "sprechen_v1": [
        ("aufgabenbezug", "Aufgaben-/Themenbearbeitung, Vollständigkeit"),
        ("sprachfunktionen", "geforderte Sprachfunktionen"),
        ("situationsangemessenheit", "Situationsangemessenheit, Adressatenbezug"),
        ("fluessigkeit_verstaendlichkeit", "Flüssigkeit, Verständlichkeit, Aussprache/Intonation (nur qualitativ)"),
        ("praezision", "präziser, situationsangemessener Ausdruck / Register"),
        ("variation", "Variation in Grammatik, Wortschatz, Redemitteln"),
        ("korrektheit", "grammatische Korrektheit"),
    ],
}

LABELS_DE: dict[str, str] = {
    "aufgabenbezug": "Aufgabenbezug",
    "sprachfunktionen": "Sprachfunktionen",
    "quellennutzung": "Quellennutzung",
    "eigenformulierung": "Eigene Formulierung",
    "praezision": "Präzision & Register",
    "variation": "Variation",
    "korrektheit": "Korrektheit",
    "situationsangemessenheit": "Situationsangemessenheit",
    "fluessigkeit_verstaendlichkeit": "Flüssigkeit & Verständlichkeit",
}


def criteria(rubric_version: str) -> list[str]:
    if rubric_version not in RUBRICS:
        raise KeyError(f"unknown rubric {rubric_version}")
    return [c for c, _ in RUBRICS[rubric_version]]


def criteria_text(rubric_version: str) -> str:
    return "\n".join(f"- `{c}`: {d}" for c, d in RUBRICS[rubric_version])


def rubric_for_skill(skill: str) -> str:
    return "sprechen_v1" if skill in ("sprechen", "verhandlung") else "schreiben_v1"


def internal_scale(total: int) -> int:
    """0–28 rubric total → 0–20 'interne Übungsbewertung' (never a TDN level)."""
    return round(total / TOTAL_MAX * INTERNAL_SCALE_MAX)


def error_tags_path() -> Path:
    return repo_root() / "config" / "rubrics" / "error_tags.md"


@lru_cache(maxsize=1)
def error_tag_vocabulary() -> list[str]:
    text = error_tags_path().read_text(encoding="utf-8")
    tags = re.findall(r"\b[a-z]+/[a-z0-9_]+\b", text)
    seen: list[str] = []
    for t in tags:
        if t not in seen:
            seen.append(t)
    return seen


def redemittel_path() -> Path:
    return repo_root() / "config" / "rubrics" / "redemittel.md"


@lru_cache(maxsize=1)
def redemittel_text() -> str:
    return redemittel_path().read_text(encoding="utf-8")
