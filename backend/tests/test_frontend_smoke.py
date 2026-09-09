"""Smoke test: every Streamlit page renders without an exception against a fake ``lernapp_ui.api``.

The fake returns contract-shaped payloads (docs/api.md). No HTTP, no backend import — the UI only
talks to ``lernapp_ui.api`` (ADR-0009), so replacing that module's functions is enough.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import lernapp_ui.api as api
import lernapp_ui.review_api as review_api
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest
from streamlit.testing.v1.element_tree import Block, Expander, Node

ROOT = Path(__file__).resolve().parents[2]
UI_DIR = ROOT / "frontend" / "lernapp_ui"
PAGES_DIR = UI_DIR / "pages"
PAGE_FILES = sorted(PAGES_DIR.glob("*.py"))
EXPECTED_PAGES = {
    "start",
    "einrichtung",
    "ueben",
    "sprechen",
    "schreiben",
    "lesen_hoeren",
    "modelltests",
    "lernen",
    "vokabeln",
    "materialien",
    "verhandeln",
    "dokumente",
    "fortschritt",
    "pruefen",
    "kosten",
    "einstellungen",
}

NOW = "2026-09-01T10:00:00+00:00"
LATER = "2026-09-05T10:00:00+00:00"
FAKE_AUDIO = base64.b64encode(b"RIFF....WAVEfmt fake").decode()


def _rubric(task_type: str = "kurztext") -> dict[str, Any]:
    return {
        "rubric_version": "schreiben_v1",
        "blueprint_id": "testdaf_digital_schreiben",
        "task_type": task_type,
        "scores": [
            {"criterion": c, "score": 3, "evidence": ["Beleg"], "comment_de": "Gut."}
            for c in (
                "aufgabenbezug",
                "sprachfunktionen",
                "quellennutzung",
                "eigenformulierung",
                "praezision",
                "variation",
                "korrektheit",
            )
        ],
        "error_tags": ["grammatik/kasus", "wortschatz/kollokation"],
        "better_formulations": [["ich denke dass", "Ich bin der Auffassung, dass"]],
        "new_vocabulary": ["die Abwägung"],
        "summary_de": "Insgesamt ein solider Text.",
        "model_version": "fake/model",
        "prompt_version": "1.0.0",
        "validator": None,
        "needs_review": False,
        "routing": "auto_accept",
        "routing_reasons": [],
        "review_handoff": None,
        "confidence_missing": False,
        "dropped_evidence": [],
    }


def _assessment() -> dict[str, Any]:
    return {
        "result_id": "res-1",
        "rubric": _rubric(),
        "total": 21,
        "total_max": 28,
        "internal_score_20": 15,
        "needs_review": False,
        "routing": "auto_accept",
        "review_handoff": None,
        "label": "interne Übungsbewertung",
        "cost_eur": 0.0123,
    }


def _handoff(validator: bool) -> dict[str, Any]:
    crits = [s["criterion"] for s in _rubric()["scores"]]
    return {
        "task_text": "Fassen Sie die Grafik zusammen.",
        "learner_text": "Die Grafik zeigt einen Anstieg. Meiner Meinung nach ist das ein Problem.",
        "assessment_scores": {c: 3 for c in crits},
        "validator_scores": {**{c: 3 for c in crits}, "korrektheit": 1} if validator else None,
        "disagreeing_criteria": ["korrektheit"] if validator else [],
        "evidence": {"aufgabenbezug": ["Die Grafik zeigt einen Anstieg"]},
        "reason": "Zur Prüfung vorgelegt, weil die Zweitmeinung bei korrektheit um mindestens zwei Punkte abweicht."
        if validator
        else "Stichprobe: Jede zehnte automatisch akzeptierte Bewertung dieser Fertigkeit wird zur Prüfung vorgelegt.",
        "reasons": ["disagreement:korrektheit:3vs1"] if validator else ["spot_check:stratified_sample:schreiben"],
        "created_at": NOW,
    }


SPOTCHECKS: list[dict[str, Any]] = [
    {
        "result_id": "res-spot",
        "learner_id": "default",
        "skill": "schreiben",
        "task_type": "kurztext",
        "blueprint_id": "testdaf_digital_schreiben",
        "rubric_version": "schreiben_v1",
        "routing": "spot_check",
        "reasons": ["spot_check:stratified_sample:schreiben"],
        "criteria": [s["criterion"] for s in _rubric()["scores"]],
        "review_handoff": _handoff(validator=False),
        "error_tags": ["grammatik/kasus"],
        "score": 21,
        "score_max": 28,
        "is_progress_point": False,
        "created_at": NOW,
    },
    {
        "result_id": "res-review",
        "learner_id": "default",
        "skill": "sprechen",
        "task_type": "adhoc",
        "blueprint_id": "adhoc_sprechen",
        "rubric_version": "sprechen_v1",
        "routing": "needs_review",
        "reasons": ["disagreement:korrektheit:3vs1"],
        "criteria": [
            "aufgabenbezug",
            "sprachfunktionen",
            "situationsangemessenheit",
            "fluessigkeit_verstaendlichkeit",
            "praezision",
            "variation",
            "korrektheit",
        ],
        "review_handoff": _handoff(validator=True),
        "error_tags": [],
        "score": 19,
        "score_max": 28,
        "is_progress_point": True,
        "created_at": LATER,
    },
]


def _blueprint(skill: str, task_types: list[str], scoring: str) -> dict[str, Any]:
    return {
        "id": f"testdaf_digital_{skill}",
        "skill": skill,
        "format": "digital",
        "source_url": "https://www.testdaf.de/",
        "version": "0.1.0",
        "verified_on": None,
        "total_time_seconds": 3600,
        "n_tasks": len(task_types),
        "n_items": None,
        "input_modality": "text",
        "output_modality": "selection" if scoring == "deterministic" else "typed_text",
        "scoring": scoring,
        "rubric_ref": None if scoring == "deterministic" else f"{skill}_v1",
        "score_scale": {"min": 0, "max": 20},
        "tasks": [{"task_type": t, "description": f"Beschreibung {t}", "n_items": 5} for t in task_types],
        "language_functions": [],
        "is_verified": False,
        "task_types": task_types,
    }


BLUEPRINTS = [
    _blueprint("lesen", ["leseverstehen_1"], "deterministic"),
    _blueprint("hoeren", ["hoerverstehen_1"], "deterministic"),
    _blueprint("schreiben", ["textproduktion_mit_quellen", "kurztext"], "rubric"),
    _blueprint("sprechen", ["sprechaufgabe_1"], "rubric"),
]

SCENARIO = {
    "id": "gehalt_1_jahresgespraech",
    "title_de": "Gehaltsgespräch nach dem ersten Jahr",
    "category": "gehalt",
    "difficulty": 1,
    "description_de": "Sie möchten eine Gehaltsanpassung erreichen.",
    "learner": {
        "role": "Junior-Projektmanager:in",
        "goals": ["Gehaltserhöhung auf 57.000 €"],
        "constraints": ["Sachlich bleiben"],
        "batna": "In sechs Monaten erneut ansprechen.",
    },
    "max_turns": 12,
    "voice": False,
}


# ------------------------------------------------------------------ cost tracking v2 fakes (docs/api.md)
FAKE_TOKENS_IN = 123456  # distinctive numbers: must appear only inside the advanced expander
FAKE_TOKENS_OUT = 65432
PERIOD = {"from": "2026-09-01", "to": "2026-10-01", "label_de": "September 2026"}


def _usage_event(idx: int, provider: str, service: str, status: str, expected: float, **units: Any) -> dict[str, Any]:
    return {
        "id": f"ev-{idx}",
        "ts": NOW,
        "session_id": "sess-1",
        "provider": provider,
        "service": service,
        "model": "gpt-5-mini" if provider == "openai" else "gemini-2.5-flash",
        "tier_name": "conversation",
        "outcome": "ok",
        "input_tokens": units.get("input_tokens", 0),
        "cached_input_tokens": 0,
        "output_tokens": units.get("output_tokens", 0),
        "reasoning_tokens": 0,
        "audio_input_seconds": units.get("audio_input_seconds", 0.0),
        "audio_output_seconds": 0.0,
        "characters": units.get("characters", 0),
        "pricing_tier": "free" if provider == "gemini" else "paid",
        "cost_status": status,
        "expected_cost_eur": expected,
        "list_cost_eur": expected if status != "free" else 0.02,
        "pricing_rule_id": None if status == "unknown" else f"{provider}-{service}",
        "pricing_version": "2026-09",
        "fx_rate": 0.92,
        "meta": {},
    }


USAGE_EVENTS: list[dict[str, Any]] = [
    _usage_event(1, "openai", "llm", "estimated", 0.08, input_tokens=FAKE_TOKENS_IN, output_tokens=FAKE_TOKENS_OUT),
    _usage_event(2, "openai", "stt", "estimated", 0.03, audio_input_seconds=42.0),
    _usage_event(3, "gemini", "llm", "free", 0.0, input_tokens=900, output_tokens=120),
    _usage_event(4, "openai", "llm", "unknown", 0.0, input_tokens=500, output_tokens=50),
]

_PROVIDER_OPENAI = {
    "provider": "openai",
    "label_de": "OpenAI",
    "expected_eur": 0.11,
    "list_eur": 0.11,
    "pricing_tier": "paid",
    "free_tier_label_de": None,
    "requests": 14,
    "unknown": 5,
}
_PROVIDER_GEMINI = {
    "provider": "gemini",
    "label_de": "Gemini",
    "expected_eur": 0.0,
    "list_eur": 0.21,
    "pricing_tier": "free",
    "free_tier_label_de": "Kostenlose Stufe",
    "requests": 8,
    "unknown": 0,
}
_MODEL_OPENAI = {
    "provider": "openai",
    "service": "llm",
    "model": "gpt-5-mini",
    "requests": 9,
    "input_tokens": FAKE_TOKENS_IN,
    "output_tokens": FAKE_TOKENS_OUT,
    "audio_seconds": 0.0,
    "characters": 0,
    "expected_eur": 0.08,
    "list_eur": 0.08,
    "unknown": 0,
}
_MODEL_GEMINI = {
    "provider": "gemini",
    "service": "llm",
    "model": "gemini-2.5-flash",
    "requests": 8,
    "input_tokens": 9000,
    "output_tokens": 1200,
    "audio_seconds": 0.0,
    "characters": 0,
    "expected_eur": 0.0,
    "list_eur": 0.21,
    "unknown": 0,
}


def usage_summary_for(variant: str) -> dict[str, Any]:
    """Contract-shaped GET /usage/summary payloads: empty | free | mixed | budget96."""
    base: dict[str, Any] = {
        "period": dict(PERIOD),
        "expected_cost_eur": 0.0,
        "list_cost_eur": 0.0,
        "learning_seconds": 0.0,
        "cost_per_learning_hour_eur": None,
        "rate_available": False,
        "rate_hint_de": None,
        "budget": {
            "monthly_budget_eur": None,
            "used_eur": 0.0,
            "share": None,
            "warning_level": "none",
            "stop_on_limit": False,
        },
        "requests": {"total": 0, "priced": 0, "free": 0, "unknown": 0, "failed": 0},
        "providers": [],
        "services": [],
        "models": [],
        "unknown_models": [],
        "status_note_de": "Noch keine Anfragen in diesem Monat.",
        "pricing_version": "2026-09",
        "fx_rate": 0.92,
    }
    if variant == "empty":
        return base
    if variant == "free":
        return {
            **base,
            "list_cost_eur": 0.21,
            "learning_seconds": 180.0,
            "rate_hint_de": "Ab 5 Minuten Lernzeit wird der Wert berechnet.",
            "requests": {"total": 8, "priced": 0, "free": 8, "unknown": 0, "failed": 0},
            "providers": [dict(_PROVIDER_GEMINI)],
            "services": [{"service": "llm", "label_de": "Sprachmodell", "expected_eur": 0.0, "requests": 8}],
            "models": [dict(_MODEL_GEMINI)],
            "status_note_de": "8 von 8 Anfragen vollständig berechnet",
        }
    mixed: dict[str, Any] = {
        **base,
        "expected_cost_eur": 0.11,
        "list_cost_eur": 0.32,
        "learning_seconds": 4320.0,
        "cost_per_learning_hour_eur": 0.0917,
        "rate_available": True,
        "requests": {"total": 22, "priced": 9, "free": 8, "unknown": 5, "failed": 0},
        "providers": [dict(_PROVIDER_OPENAI), dict(_PROVIDER_GEMINI)],
        "services": [
            {"service": "llm", "label_de": "Sprachmodell", "expected_eur": 0.08, "requests": 17},
            {"service": "stt", "label_de": "Spracherkennung", "expected_eur": 0.03, "requests": 5},
        ],
        "models": [
            dict(_MODEL_OPENAI),
            {
                **_MODEL_OPENAI,
                "service": "stt",
                "model": "gpt-4o-transcribe",
                "requests": 5,
                "input_tokens": 0,
                "output_tokens": 0,
                "audio_seconds": 210.0,
                "expected_eur": 0.03,
                "list_eur": 0.03,
            },
            dict(_MODEL_GEMINI),
            {
                **_MODEL_OPENAI,
                "model": "gpt-5-mini-2026",
                "requests": 5,
                "input_tokens": 500,
                "output_tokens": 50,
                "expected_eur": 0.0,
                "list_eur": 0.0,
                "unknown": 5,
            },
        ],
        "unknown_models": [
            {
                "provider": "openai",
                "model": "gpt-5-mini-2026",
                "service": "llm",
                "requests": 5,
                "missing_units": ["input_tokens", "output_tokens"],
            }
        ],
        "status_note_de": "17 von 22 Anfragen vollständig berechnet",
    }
    if variant == "mixed":
        return mixed
    if variant == "budget96":
        return {
            **mixed,
            "expected_cost_eur": 4.8,
            "providers": [{**_PROVIDER_OPENAI, "expected_eur": 4.8, "list_eur": 4.8}, dict(_PROVIDER_GEMINI)],
            "budget": {
                "monthly_budget_eur": 5.0,
                "used_eur": 4.8,
                "share": 0.96,
                "warning_level": 95,
                "stop_on_limit": False,
            },
        }
    raise ValueError(variant)


PROVIDER_CREDENTIALS: list[dict[str, Any]] = [
    {
        "provider": "openai",
        "label_de": "OpenAI",
        "connected": True,
        "source": "encrypted",
        "masked": "••••••••••••3xQ",
        "pricing_tier": "paid",
        "last_test_at": NOW,
        "last_test_ok": True,
        "last_test_message": "OpenAI verbunden",
    },
    {
        "provider": "gemini",
        "label_de": "Google Gemini",
        "connected": False,
        "source": "none",
        "masked": None,
        "pricing_tier": "unknown",
        "last_test_at": None,
        "last_test_ok": None,
        "last_test_message": None,
    },
    {
        "provider": "anthropic",
        "label_de": "Anthropic",
        "connected": True,
        "source": "env",
        "masked": "••••••••••••H7k",
        "pricing_tier": "paid",
        "last_test_at": None,
        "last_test_ok": None,
        "last_test_message": None,
    },
    {
        "provider": "mistral",
        "label_de": "Mistral",
        "connected": False,
        "source": "none",
        "masked": None,
        "pricing_tier": "unknown",
        "last_test_at": None,
        "last_test_ok": None,
        "last_test_message": None,
    },
]


class FakeApi:
    @staticmethod
    def list_workspaces() -> list[dict[str, Any]]:
        return [{"id": "default", "name": "Mein Deutsch", "level": "B2"}]

    @staticmethod
    def create_workspace(name: str, level: str = "B2") -> dict[str, Any]:
        return {"id": "new-workspace", "name": name, "level": level}

    """Contract-shaped stand-in for every public function of ``lernapp_ui.api``."""

    calls: list[str] = []
    usage_variant: str = "mixed"

    # health / settings
    @staticmethod
    def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": "0.1.0",
            "env": "test",
            "eu_strict_mode": False,
            "keep_audio": False,
            "llm_backend": "litellm",
            "stt_backend": "faster-whisper",
            "stt_model": "medium",
            "tts_backend": "openai",
            "tts_configured": "google",
            "pron_backend": "prosody_mvp",
            "pricing_version": "2026-09",
            "providers_configured": {"openai": True, "anthropic": True, "mistral": False, "gemini": False},
            "tiers": {},
        }

    @staticmethod
    def backend_ready() -> bool:
        return True

    @staticmethod
    def get_settings() -> dict[str, Any]:
        return {
            "env_file": "/tmp/x/.env",
            "data_dir": "/tmp/x",
            "secrets": {
                "OPENAI_API_KEY": "sk-a…xyz",
                "ANTHROPIC_API_KEY": None,
                "MISTRAL_API_KEY": None,
                "GEMINI_API_KEY": None,
            },
            "google_credentials_file": None,
            "providers_configured": {"openai": True, "anthropic": False, "mistral": False, "gemini": False},
            "speech": {
                "stt_backend": "faster-whisper",
                "stt_model": "medium",
                "tts_backend_configured": "google",
                "tts_backend_active": "openai",
                "tts_voice": "de-DE-Wavenet-C",
                "pron_backend": "prosody_mvp",
            },
            "keep_audio": False,
            "eu_strict_mode": False,
            "fx_usd_eur": 0.92,
            "llm_backend": "litellm",
            "default_level": "C1",
            "tiers": {},
        }

    @staticmethod
    def local_speech_status() -> dict[str, Any]:
        return {"recognition": {"job": {"state": "idle"}, "models": {
            name: {"installed": name == "medium", "download": "ca. 500 MB"}
            for name in ("small", "medium", "large-v3-turbo")}}, "voice": {"ready": False, "state": "idle"}}

    @staticmethod
    def list_links() -> list[dict[str, str]]:
        return [{"id": "testdaf", "title": "Fit für den TestDaF", "url": "https://www.testdaf.de/fit-fuer-testdaf/?id=1", "description": "Lückentest"}]

    @staticmethod
    def save_link(values: dict[str, str], link_id: str | None = None) -> list[dict[str, str]]:
        return FakeApi.list_links()

    @staticmethod
    def remove_link(link_id: str) -> list[dict[str, str]]:
        return []

    @staticmethod
    def put_settings(values: dict[str, str | None]) -> dict[str, Any]:
        return FakeApi.get_settings()

    @staticmethod
    def test_provider(provider: str) -> dict[str, Any]:
        return {"ok": True, "detail": "antwortet."}

    # blueprints
    @staticmethod
    def list_blueprints() -> list[dict[str, Any]]:
        return BLUEPRINTS

    @staticmethod
    def get_blueprint(blueprint_id: str) -> dict[str, Any]:
        return next(b for b in BLUEPRINTS if b["id"] == blueprint_id)

    # costs
    @staticmethod
    def costs_summary(
        from_: str | None = None, to: str | None = None, group_by: str = "stage", learner_id: str | None = None
    ) -> dict[str, Any]:
        keys = {
            "stage": ["llm", "stt", "tts"],
            "day": ["2026-09-01", "2026-09-02"],
            "model": ["openai/gpt", "local/whisper"],
        }
        groups = [
            {"key": k, "rows": 3, "quantity": 1200.0, "cost_usd": 0.01, "cost_eur": 0.0092, "unpriced_rows": 0}
            for k in keys.get(group_by, ["x"])
        ]
        return {
            "groups": groups,
            "total_eur": 0.0276,
            "total_usd": 0.03,
            "rows": 9,
            "unpriced_rows": 1,
            "learning_seconds": 600.0,
            "eur_per_learning_minute": 0.00276,
            "pricing_version": "2026-09",
            "fx_rate": 0.92,
        }

    @staticmethod
    def costs_counterfactual(variant: str, from_: str | None = None, to: str | None = None) -> dict[str, Any]:
        return {"actual_eur": 0.0276, "counterfactual_eur": 0.08, "saving_eur": 0.0524, "by_stage": {}, "mapping": {}}

    @staticmethod
    def costs_session(session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "rows": [], "total_eur": 0.0}

    @staticmethod
    def costs_pricing() -> dict[str, Any]:
        return {"version": "2026-09", "entries": []}

    # sessions
    @staticmethod
    def create_session(kind: str = "tutor", task_id: str | None = None, learner_id: str = "default") -> dict[str, Any]:
        return {"session_id": "sess-1", "kind": kind, "started_at": NOW}

    @staticmethod
    def session_turn_text(session_id: str, text: str, defer_audio: bool = False) -> dict[str, Any]:
        return FakeApi._turn(session_id, text)

    @staticmethod
    def session_turn_audio(
        session_id: str, audio: bytes, filename: str = "a.wav", mime: str = "audio/wav"
    ) -> dict[str, Any]:
        return FakeApi._turn(session_id, "Hallo")

    @staticmethod
    def _turn(session_id: str, text: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "turn_ord": 1,
            "learner_text": text,
            "reply_text": "Sehr gut, erzähl mir mehr.",
            "reply_audio_b64": "",
            "reply_audio_mime": "audio/mpeg",
            "prosody": None,
            "pronunciation_tip": None,
            "citations": [{"title": "Notizen", "ord": 2}],
            "cost_eur_turn": 0.001,
            "cost_eur_session": 0.002,
            "duration_seconds": 12.0,
        }

    @staticmethod
    def end_session(session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "duration_seconds": 30.0, "cost_eur": 0.002}

    @staticmethod
    def get_session(session_id: str) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "kind": "tutor",
            "started_at": NOW,
            "ended_at": None,
            "duration_seconds": 0,
            "turns": [],
        }

    @staticmethod
    def list_sessions(kind: str | None = None, limit: int = 20, learner_id: str = "default") -> list[dict[str, Any]]:
        return []

    # tasks
    @staticmethod
    def generate_task(
        blueprint_id: str, task_type: str, level: str = "C1", topic: str | None = None, learner_id: str = "default"
    ) -> dict[str, Any]:
        skill = next(b["skill"] for b in BLUEPRINTS if b["id"] == blueprint_id)
        task: dict[str, Any] = {
            "title": "Aufgabe: Homeoffice",
            "instructions_de": "Lesen Sie den Text und beantworten Sie die Fragen.",
            "source_text": "Homeoffice ist beliebt. Viele Firmen bieten es an.",
            "graphic_description": "Ein Balkendiagramm zeigt die Verbreitung von Homeoffice."
            if skill == "schreiben"
            else None,
            "items": [
                {"id": "q1", "question": "Homeoffice ist beliebt.", "options": []},
                {"id": "q2", "question": "Wer bietet Homeoffice an?", "options": ["Schulen", "Firmen", "Behörden"]},
            ]
            if skill in ("lesen", "hoeren")
            else [],
            "expected_content": ["Vorteile nennen"] if skill == "schreiben" else [],
            "rubric_ref": None,
            "language_functions": [],
            "preparation_seconds": 30,
            "response_seconds": 60,
            "level": level,
        }
        if skill == "hoeren":
            task["audio_b64"] = FAKE_AUDIO
            task["audio_mime"] = "audio/wav"
        return {
            "task_id": "task-1",
            "status": "ok",
            "skill": skill,
            "task_type": task_type,
            "level": level,
            "task": task,
            "validation": {"ok": True, "issues": [], "agreement": 1.0},
            "blueprint": {
                "id": blueprint_id,
                "version": "0.1.0",
                "preparation_seconds": 30,
                "response_seconds": 60,
                "total_time_seconds": 3600,
            },
            "models": {"generator": "fake/a", "validator": "fake/b"},
            "prompt_versions": {"generator": "1.0.0", "validator": "1.0.0"},
            "cost_eur": 0.004,
        }

    @staticmethod
    def get_task(task_id: str) -> dict[str, Any]:
        return FakeApi.generate_task("testdaf_digital_lesen", "leseverstehen_1")

    @staticmethod
    def list_tasks(skill: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        return []

    @staticmethod
    def score_task(
        task_id: str, answers: dict[str, str], session_id: str | None = None, learner_id: str = "default"
    ) -> dict[str, Any]:
        return {
            "result_id": "res-2",
            "score": 1,
            "score_max": 2,
            "internal_score_20": 10,
            "correct": ["q1"],
            "wrong": [
                {
                    "id": "q2",
                    "given": answers.get("q2", ""),
                    "expected": "Firmen",
                    "explanation_de": "Im Text steht: Firmen.",
                }
            ],
            "explanations_de": "Achte auf die genaue Formulierung im Text.",
            "source_text": "Homeoffice ist beliebt. Viele Firmen bieten es an.",
            "cost_eur": 0.001,
        }

    # assessment
    @staticmethod
    def assess_writing(learner_text: str, **kwargs: Any) -> dict[str, Any]:
        return _assessment()

    @staticmethod
    def assess_speaking(transcript: str, **kwargs: Any) -> dict[str, Any]:
        return {**_assessment(), "pronunciation_tip": {"tip_de": "Sprich das ‚ch' weicher.", "practice_words": ["ich"]}}

    @staticmethod
    def assess_speaking_audio(audio: bytes, **kwargs: Any) -> dict[str, Any]:
        return {
            **FakeApi.assess_speaking("Hallo"),
            "transcript": "Hallo",
            "prosody": {"tempo_label_de": "angemessen", "n_fillers": 1},
        }

    # roleplay
    @staticmethod
    def roleplay_scenarios(category: str | None = None, difficulty: int | None = None) -> list[dict[str, Any]]:
        return [
            SCENARIO,
            {**SCENARIO, "id": "einkauf_1", "title_de": "Rahmenvertrag", "category": "einkauf", "difficulty": 2},
        ]

    @staticmethod
    def roleplay_start(scenario_id: str, voice: bool = False, learner_id: str = "default") -> dict[str, Any]:
        return {
            "session_id": "rp-1",
            "scenario": SCENARIO,
            "opening_line": "Schön, dass Sie da sind. Worum geht es?",
            "opening_audio_b64": "",
            "opening_audio_mime": "audio/mpeg",
            "turn": 0,
            "max_turns": 12,
        }

    @staticmethod
    def roleplay_turn_text(session_id: str, text: str) -> dict[str, Any]:
        ended = "ROLLENSPIEL ENDE" in text.upper()
        return {
            "session_id": session_id,
            "learner_text": text,
            "reply_text": "" if ended else "Das Budget ist knapp.",
            "reply_audio_b64": "",
            "reply_audio_mime": "audio/mpeg",
            "turn": 1,
            "max_turns": 12,
            "ended": ended,
            "prosody": None,
            "coach": {
                "outcome_vs_goals": "Ziel teilweise erreicht.",
                "moves_used": ["anker", "fragen_stellen"],
                "moves_missed": ["batna_nutzen", "schweigen"],
                "language_feedback": "Register weitgehend passend.",
                "error_tags": ["verhandlung/kein_ziel_genannt"],
                "missing_redemittel": ["Ich schlage vor, dass …"],
                "better_formulations": [["ich will mehr Geld", "Ich möchte über eine Anpassung sprechen"]],
                "next_focus": "BATNA klar benennen.",
                "rubric_result": _rubric("verhandlung"),
            }
            if ended
            else None,
            "hidden_targets": {"max_gehalt": "57.000 €"} if ended else None,
            "result_id": "res-3" if ended else None,
            "cost_eur_turn": 0.001,
            "cost_eur_session": 0.003,
        }

    @staticmethod
    def roleplay_turn_audio(
        session_id: str, audio: bytes, filename: str = "a.wav", mime: str = "audio/wav"
    ) -> dict[str, Any]:
        return FakeApi.roleplay_turn_text(session_id, "Hallo")

    @staticmethod
    def roleplay_end(session_id: str) -> dict[str, Any]:
        return FakeApi.roleplay_turn_text(session_id, "ROLLENSPIEL ENDE")

    @staticmethod
    def roleplay_state(session_id: str) -> dict[str, Any]:
        return {"session_id": session_id, "turns": []}

    # documents / vocab
    @staticmethod
    def upload_document(
        file_bytes: bytes,
        filename: str,
        mime: str,
        tags: list[str] | None = None,
        use_in: list[str] | None = None,
        learner_id: str = "default",
    ) -> dict[str, Any]:
        return {
            "document_id": "doc-2",
            "title": filename,
            "status": "indexiert",
            "n_chunks": 3,
            "n_vocab_items": 0,
            "cost_eur": 0.0002,
        }

    @staticmethod
    def list_documents(learner_id: str = "default") -> list[dict[str, Any]]:
        return [
            {
                "id": "doc-1",
                "title": "Meine Notizen",
                "filename": "notizen.md",
                "mime": "text/markdown",
                "tags": ["verhandlung"],
                "use_in": {"uebungen": True, "rollenspiel": True, "tutor": False},
                "n_chunks": 4,
                "n_chars": 1800,
                "status": "indexiert",
                "error": None,
                "created_at": NOW,
            }
        ]

    @staticmethod
    def patch_document(document_id: str, **kwargs: Any) -> dict[str, Any]:
        return FakeApi.list_documents()[0]

    @staticmethod
    def delete_document(document_id: str) -> dict[str, Any]:
        return {"deleted": True}

    @staticmethod
    def search_documents(q: str, **kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "chunk_id": "c1",
                "document_id": "doc-1",
                "document_title": "Meine Notizen",
                "ord": 1,
                "heading": "Ziele",
                "text": "Treffer",
                "score": 0.5,
            }
        ]

    @staticmethod
    def list_vocab(due_only: bool = True, limit: int = 20, learner_id: str = "default") -> list[dict[str, Any]]:
        return [
            {
                "id": "v1",
                "wort": "die Abwägung",
                "bedeutung": "consideration",
                "beispiel": "…",
                "due_at": NOW,
                "repetitions": 0,
                "interval_days": 1,
            }
        ]

    @staticmethod
    def add_vocab(
        wort: str, bedeutung: str, beispiel: str | None = None, learner_id: str = "default"
    ) -> dict[str, Any]:
        return {"id": "v2", "wort": wort, "bedeutung": bedeutung, "beispiel": beispiel}

    @staticmethod
    def review_vocab(vocab_id: str, quality: int) -> dict[str, Any]:
        return {"id": vocab_id}

    @staticmethod
    def vocab_stats(learner_id: str = "default") -> dict[str, Any]:
        return {"total": 12, "due": 1}

    @staticmethod
    def delete_vocab(vocab_id: str) -> dict[str, Any]:
        return {"deleted": True}

    # learners
    @staticmethod
    def get_learner(learner_id: str = "default") -> dict[str, Any]:
        return {
            "id": "default",
            "display_name": "Sara",
            "level": "C1",
            "exam_date": "2026-11-15",
            "weekly_focus": "Konjunktiv II",
            "activity_status": "aktiv",
            "created_at": NOW,
        }

    @staticmethod
    def patch_learner(values: dict[str, Any], learner_id: str = "default") -> dict[str, Any]:
        return {**FakeApi.get_learner(), **values}

    @staticmethod
    def learner_progress(skill: str | None = None, learner_id: str = "default") -> dict[str, Any]:
        points = [
            {
                "result_id": "r1",
                "created_at": NOW,
                "skill": "schreiben",
                "task_type": "kurztext",
                "score": 18,
                "score_max": 28,
                "internal_score_20": 13,
                "rubric_version": "schreiben_v1",
                "prompt_version": "1.0.0",
                "model_version": "fake",
                "needs_review": False,
                "is_progress_point": True,
            },
            {
                "result_id": "r2",
                "created_at": LATER,
                "skill": "schreiben",
                "task_type": "kurztext",
                "score": 21,
                "score_max": 28,
                "internal_score_20": 15,
                "rubric_version": "schreiben_v2",
                "prompt_version": "1.1.0",
                "model_version": "fake",
                "needs_review": True,
                "is_progress_point": True,
            },
            {
                "result_id": "r3",
                "created_at": LATER,
                "skill": "lesen",
                "task_type": "leseverstehen_1",
                "score": 4,
                "score_max": 5,
                "internal_score_20": 16,
                "rubric_version": None,
                "prompt_version": None,
                "model_version": None,
                "needs_review": False,
                "is_progress_point": False,
            },
        ]
        return {
            "points": points,
            "weekly_focus": "Konjunktiv II",
            "top_error_tags": [{"tag": "grammatik/kasus", "count": 5}, {"tag": "wortschatz/kollokation", "count": 2}],
            "activity_status": "aktiv",
            "version_breaks": [LATER],
            "pending_spotchecks": len(SPOTCHECKS),
        }

    # spot-checks (lernapp_ui.review_api)
    @staticmethod
    def learner_spotchecks(learner_id: str = "default") -> list[dict[str, Any]]:
        return SPOTCHECKS

    @staticmethod
    def label_spotcheck(
        result_id: str, scores: dict[str, int], note: str | None = None, learner_id: str = "default"
    ) -> dict[str, Any]:
        FakeApi.calls.append(f"label:{result_id}:{sorted(scores.items())}")
        return {"result_id": result_id, "routing": "labelled", "payload": {"human_label": {"scores": scores}}}

    @staticmethod
    def learner_result(result_id: str, learner_id: str = "default") -> dict[str, Any]:
        return _assessment()

    @staticmethod
    def learner_results(skill: str | None = None, limit: int = 10, learner_id: str = "default") -> list[dict[str, Any]]:
        return FakeApi.learner_progress()["points"][:limit]

    @staticmethod
    def export_learner(learner_id: str = "default") -> bytes:
        return b"PK\x03\x04fake"

    @staticmethod
    def delete_learner(learner_id: str = "default") -> dict[str, Any]:
        return {"deleted": True}

    @staticmethod
    def run_evals(suite: str = "all") -> dict[str, Any]:
        return {"suite": suite}

    # usage / budget / BYOK (docs/api.md "Cost tracking v2")
    @staticmethod
    def usage_summary(learner_id: str = "default", month: str | None = None) -> dict[str, Any]:
        FakeApi.calls.append(f"usage_summary:{month}")
        return usage_summary_for(FakeApi.usage_variant)

    @staticmethod
    def usage_events(**filters: Any) -> dict[str, Any]:
        limit = int(filters.get("limit") or 50)
        offset = int(filters.get("offset") or 0)
        FakeApi.calls.append(f"usage_events:{offset}:{limit}")
        return {"items": USAGE_EVENTS[offset : offset + limit], "total": len(USAGE_EVENTS)}

    @staticmethod
    def usage_session(session_id: str, learner_id: str = "default") -> dict[str, Any]:
        return {
            "session_id": session_id,
            "kind": "tutor",
            "started_at": NOW,
            "duration_seconds": 720.0,
            "expected_cost_eur": 0.04,
            "list_cost_eur": 0.04,
            "by_provider": [{"provider": "openai", "label_de": "OpenAI", "expected_eur": 0.04}],
            "by_service": [
                {"service": "stt", "label_de": "Spracherkennung", "expected_eur": 0.03},
                {"service": "llm", "label_de": "Sprachmodell", "expected_eur": 0.01},
            ],
            "events": [
                _usage_event(10, "openai", "stt", "estimated", 0.03, audio_input_seconds=30.0),
                _usage_event(11, "openai", "llm", "estimated", 0.01, input_tokens=300, output_tokens=80),
            ],
        }

    @staticmethod
    def get_usage_settings(learner_id: str = "default") -> dict[str, Any]:
        return {"monthly_budget_eur": None, "stop_on_limit": False, "warn_levels": [80, 95, 100]}

    @staticmethod
    def put_usage_settings(
        monthly_budget_eur: float | None, stop_on_limit: bool, learner_id: str = "default"
    ) -> dict[str, Any]:
        FakeApi.calls.append(f"put_usage:{monthly_budget_eur}:{stop_on_limit}")
        return {"monthly_budget_eur": monthly_budget_eur, "stop_on_limit": stop_on_limit, "warn_levels": [80, 95, 100]}

    @staticmethod
    def provider_credentials(learner_id: str = "default") -> list[dict[str, Any]]:
        return [dict(c) for c in PROVIDER_CREDENTIALS]

    @staticmethod
    def put_provider_credential(
        provider: str, api_key: str | None = None, pricing_tier: str | None = None, learner_id: str = "default"
    ) -> dict[str, Any]:
        # Record whether a key was sent, never the key itself.
        FakeApi.calls.append(f"put_cred:{provider}:key={'yes' if api_key else 'no'}:tier={pricing_tier}")
        view = next(dict(c) for c in PROVIDER_CREDENTIALS if c["provider"] == provider)
        if api_key:
            view.update(connected=True, source="encrypted", masked="••••••••••••" + api_key[-3:])
        if pricing_tier:
            view["pricing_tier"] = pricing_tier
        return view

    @staticmethod
    def delete_provider_credential(provider: str, learner_id: str = "default") -> dict[str, Any]:
        FakeApi.calls.append(f"delete_cred:{provider}")
        return {"deleted": True}

    @staticmethod
    def test_provider_credential(provider: str, learner_id: str = "default") -> dict[str, Any]:
        FakeApi.calls.append(f"test_cred:{provider}")
        label = next(c["label_de"] for c in PROVIDER_CREDENTIALS if c["provider"] == provider)
        return {"ok": True, "message_de": f"{label} verbunden"}


def _public_api_functions(module: Any = api) -> list[str]:
    import inspect

    return [
        n
        for n, obj in vars(module).items()
        if not n.startswith("_") and inspect.isfunction(obj) and obj.__module__ == module.__name__
    ]


@pytest.fixture()
def fake_api(monkeypatch: pytest.MonkeyPatch) -> type[FakeApi]:
    from lernapp_ui import exam_api

    monkeypatch.setattr(exam_api, "listing", lambda: [])
    missing: list[str] = []
    for module in (api, review_api):
        for name in _public_api_functions(module):
            fake = getattr(FakeApi, name, None)
            if fake is None:
                missing.append(name)
                continue
            monkeypatch.setattr(module, name, fake)
    assert not missing, f"FakeApi lacks stand-ins for: {missing}"
    FakeApi.calls.clear()
    FakeApi.usage_variant = "mixed"
    st.cache_data.clear()  # app.py caches the sidebar usage summary for 60 s
    return FakeApi


def _run(path: Path, timeout: float = 20.0) -> AppTest:
    at = AppTest.from_file(str(path), default_timeout=timeout)
    at.run()
    return at


def _fail_message(at: AppTest) -> str:
    return "\n".join(str(e.value) + "\n" + str(getattr(e, "stack_trace", "")) for e in at.exception)


def test_all_expected_pages_exist() -> None:
    assert {p.stem for p in PAGE_FILES} >= EXPECTED_PAGES
    app_src = (UI_DIR / "app.py").read_text(encoding="utf-8")
    for name in EXPECTED_PAGES:
        assert f"pages/{name}.py" in app_src, f"app.py does not reference pages/{name}.py"


@pytest.mark.parametrize("page", PAGE_FILES, ids=[p.stem for p in PAGE_FILES])
def test_page_renders(fake_api: type[FakeApi], page: Path) -> None:
    at = _run(page)
    assert not at.exception, _fail_message(at)
    assert at.title, f"{page.name}: page has no title"


def test_no_forbidden_labels_in_pages() -> None:
    """Product rules 1 and 6 — belt and braces next to test_product_rules.py."""
    import re

    tdn = re.compile(r"TDN\s?[3-5]")
    pct = re.compile(r"\d{1,3}\s?%\s?(korrekt|richtig)", re.I)
    for f in [*PAGE_FILES, UI_DIR / "components.py", UI_DIR / "app.py"]:
        text = f.read_text(encoding="utf-8")
        assert not tdn.search(text), f.name
        assert not pct.search(text), f.name


def test_lesen_flow_generate_and_score(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "lesen_hoeren.py")
    at.button[0].click().run()  # "Aufgabe erstellen"
    assert not at.exception, _fail_message(at)
    radios = [r for r in at.radio if r.key and str(r.key).startswith("lh_answer_")]
    assert len(radios) == 2
    for r in radios:
        r.set_value(r.options[1])
    at.run()
    score_button = next(b for b in at.button if b.label == "Auswerten")
    score_button.click().run()
    assert not at.exception, _fail_message(at)
    body = " ".join(m.value for m in at.markdown) + " ".join(str(m.value) for m in at.metric)
    assert "interne Übungsbewertung" in body or any("interne Übungsbewertung" in str(m.label) for m in at.metric)


def test_verhandeln_flow_until_coach_report(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "verhandeln.py")
    pick = next(b for b in at.button if str(b.key).startswith("rp_pick_"))
    pick.click().run()
    assert not at.exception, _fail_message(at)
    start = next(b for b in at.button if b.label == "Rollenspiel starten")
    start.click().run()
    assert not at.exception, _fail_message(at)
    assert any("Zug 0 von 12" in str(c.value) for c in at.caption)
    # No coach content while the roleplay is running (product rule 7)
    text_running = " ".join(str(m.value) for m in at.markdown)
    assert "Anker setzen" not in text_running
    end = next(b for b in at.button if "Rollenspiel beenden" in b.label)
    end.click().run()
    assert not at.exception, _fail_message(at)
    text_ended = " ".join(str(m.value) for m in at.markdown)
    assert "Anker setzen" in text_ended and "BATNA nutzen" in text_ended
    # session cost summary after the coach report (usage_session, never "tatsächlich abgerechnet")
    assert any("Dieses Rollenspiel: 12 Min." in str(x.value) for x in at.success)


def test_schreiben_own_task_assess(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "schreiben.py")
    at.radio(key="write_source_business_default").set_value("Eigene Aufgabe").run()
    at.text_area(key="write_own_task").set_value("Schreiben Sie eine E-Mail.").run()
    at.text_area(key="write_text").set_value("Sehr geehrte Damen und Herren, hiermit bitte ich um Reparatur.").run()
    assess = next(b for b in at.button if b.label == "Bewerten lassen")
    assess.click().run()
    assert not at.exception, _fail_message(at)
    assert any("interne Übungsbewertung" in str(m.label) for m in at.metric)


def test_pruefen_lists_queue_and_submits_label(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "pruefen.py")
    assert not at.exception, _fail_message(at)
    body = " ".join(str(m.value) for m in at.markdown) + " ".join(str(c.value) for c in at.caption)
    assert "2 Bewertung(en)" in body
    assert "Stichprobe – bitte prüfen" in body and "Bewertung bitte prüfen" in body
    infos = " ".join(str(i.value) for i in at.info)
    assert "Stichprobe" in infos and "Zweitmeinung" in infos  # one sentence per item explaining the selection
    sliders = [s for s in at.slider if str(s.key).startswith("slider_res-spot_")]
    assert len(sliders) == 7
    assert {s.label for s in sliders} >= {"Aufgabenbezug", "Korrektheit", "Quellennutzung"}
    sliders[-1].set_value(1)
    at.text_area(key="note_res-spot").set_value("Quellen nur angedeutet")
    submit = next(b for b in at.button if b.label == "Prüfung speichern")
    submit.click().run()
    assert not at.exception, _fail_message(at)
    assert any(c.startswith("label:res-spot:") and "('korrektheit', 1)" in c for c in fake_api.calls), fake_api.calls


def test_fortschritt_shows_pending_spotchecks(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "fortschritt.py")
    assert not at.exception, _fail_message(at)
    assert any("warten auf deine Prüfung" in str(w.value) for w in at.warning)
    assert any(b.label == "Prüfen" for b in at.button)


def test_feedback_view_routing_badges(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "schreiben.py")
    at.radio(key="write_source_business_default").set_value("Eigene Aufgabe").run()
    at.text_area(key="write_own_task").set_value("Aufgabe").run()
    at.text_area(key="write_text").set_value("Ein Text.").run()
    next(b for b in at.button if b.label == "Bewerten lassen").click().run()
    assert not at.exception, _fail_message(at)
    assert any("automatisch akzeptiert" in str(m.value) for m in at.markdown)


# ================================================================== cost tracking v2 (kosten, einstellungen, einrichtung)


def _walk(block: Block, inside: bool = False) -> list[tuple[bool, str]]:
    """(inside_expander, text) for every element of a block; dataframes are flattened to text."""
    out: list[tuple[bool, str]] = []
    for child in block.children.values():
        node: Node = child
        if isinstance(node, Block):
            out.extend(_walk(node, inside or isinstance(node, Expander)))
            continue
        try:
            value = node.value
        except Exception:  # noqa: BLE001 – elements without a value (audio, images …)
            value = ""
        text = value.to_string() if hasattr(value, "to_string") else str(value)
        label = getattr(node, "label", "")
        out.append((inside, f"{label} {text}"))
    return out


def _texts(at: AppTest, inside: bool | None = None) -> str:
    parts = _walk(at.main) + _walk(at.sidebar)
    return "\n".join(text for flag, text in parts if inside is None or flag == inside)


def _state(at: AppTest, key: str) -> Any:
    try:
        return at.session_state[key]
    except KeyError:
        return None


FORBIDDEN_BILLING_WORDING = "tatsächlich abgerechnet"


def test_no_actual_billing_wording_in_frontend() -> None:
    """The UI only ever says 'geschätzt' / 'voraussichtlich' (ADR-0019)."""
    for f in (ROOT / "frontend").rglob("*.py"):
        assert FORBIDDEN_BILLING_WORDING not in f.read_text(encoding="utf-8"), f


def test_money_formats_zero_and_cents() -> None:
    from lernapp_ui.components import duration_de, money

    assert money(0) == "0,00 €"
    assert money(0.0) == "0,00 €"
    assert money(0.11) == "0,11 €"
    assert duration_de(2280) == "38 Min."
    assert duration_de(4320) == "1 Std. 12 Min."


@pytest.mark.parametrize("variant", ["empty", "free", "mixed", "budget96"])
def test_billing_page_never_loads_local_estimates(
    fake_api: type[FakeApi], monkeypatch: pytest.MonkeyPatch, variant: str
) -> None:
    fake_api.usage_variant = variant

    def forbidden(*args, **kwargs):
        pytest.fail("Billing page must not request local estimates or cloud comparisons")

    monkeypatch.setattr(api, "usage_summary", forbidden)
    monkeypatch.setattr(api, "usage_events", forbidden)
    monkeypatch.setattr(api, "costs_counterfactual", forbidden)
    at = _run(PAGES_DIR / "kosten.py")
    assert not at.exception, _fail_message(at)
    assert not at.metric
    assert not at.dataframe
    assert any(e.label == "Anbieterbeträge direkt abrufen" for e in at.expander)
    text = _texts(at)
    for removed in (
        "Lokale Schätzungen",
        "Geschätzte AI-Kosten",
        "Kosten pro Lernstunde",
        "Vergleich: alles in der Cloud",
        "Ersparnis",
        "Preisliste Version",
    ):
        assert removed not in text


def test_app_sidebar_hides_amounts_even_near_budget(fake_api: type[FakeApi]) -> None:
    at = _run(UI_DIR / "app.py")
    assert not at.exception, _fail_message(at)
    sidebar = " ".join(str(m.value) for m in at.sidebar.markdown)
    assert "€" not in sidebar and "geschätzt" not in sidebar
    assert "Heute:" not in sidebar
    fake_api.usage_variant = "budget96"
    st.cache_data.clear()
    at = _run(UI_DIR / "app.py")
    assert not at.exception, _fail_message(at)
    sidebar = " ".join(str(m.value) for m in at.sidebar.markdown)
    assert "€" not in sidebar and "geschätzt" not in sidebar


def test_einstellungen_provider_section(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "einstellungen.py")
    assert not at.exception, _fail_message(at)
    markdown = " ".join(str(m.value) for m in at.markdown)
    assert "● Verbunden" in markdown and "○ Nicht verbunden" in markdown
    assert "● Verbunden (aus Konfigurationsdatei)" in markdown  # anthropic from env
    assert any("API-Schlüssel ••••••••••••3xQ" in str(c.value) for c in at.caption)
    assert any("zuletzt geprüft" in str(c.value) for c in at.caption)
    assert not any(str(t.key).startswith("key_") for t in at.text_input)  # legacy raw-key form is gone
    radio = at.radio(key="cred_tier_gemini")
    assert radio.options == ["Kostenlose Stufe", "Kostenpflichtige API", "Nicht sicher"]
    assert radio.value == "Nicht sicher"
    assert "Kostenlose Stufe" in radio.help
    radio.set_value("Kostenlose Stufe").run()
    assert not at.exception, _fail_message(at)
    assert "put_cred:gemini:key=no:tier=free" in fake_api.calls
    # test connection
    at.button(key="cred_test_openai").click().run()
    assert "test_cred:openai" in fake_api.calls
    assert any(str(x.value) == "OpenAI verbunden" for x in at.success)


def test_einstellungen_change_key_never_keeps_it(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "einstellungen.py")
    at.button(key="cred_chg_openai").click().run()
    assert not at.exception, _fail_message(at)
    at.text_input(key="cred_newkey_openai").set_value("sk-test-secret-XYZ").run()
    at.button(key="cred_save_openai").click().run()
    assert not at.exception, _fail_message(at)
    assert "put_cred:openai:key=yes:tier=None" in fake_api.calls
    assert "sk-test-secret-XYZ" not in _texts(at)
    assert _state(at, "cred_newkey_openai") != "sk-test-secret-XYZ"  # popped in the callback
    assert not any(str(t.key) == "cred_newkey_openai" for t in at.text_input)  # input hidden again
    assert any("Schlüssel für OpenAI gespeichert." in str(x.value) for x in at.success)


def test_einstellungen_remove_connection_needs_confirmation(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "einstellungen.py")
    assert at.button(key="cred_rm_anthropic").disabled  # env keys cannot be removed here
    at.button(key="cred_rm_openai").click().run()
    assert at.button(key="cred_rm_go_openai").disabled
    at.checkbox(key="cred_sure_openai").check().run()
    at.button(key="cred_rm_go_openai").click().run()
    assert not at.exception, _fail_message(at)
    assert "delete_cred:openai" in fake_api.calls


def test_einstellungen_cost_limit(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "einstellungen.py")
    assert at.number_input(key="usage_budget_eur").value == 0.0
    assert at.toggle(key="usage_stop_on_limit").value is False
    at.button(key="usage_budget_save").click().run()
    assert "put_usage:None:False" in fake_api.calls  # 0 → no limit
    at.number_input(key="usage_budget_eur").set_value(5.0)
    at.toggle(key="usage_stop_on_limit").set_value(True)
    at.button(key="usage_budget_save").click().run()
    assert not at.exception, _fail_message(at)
    assert "put_usage:5.0:True" in fake_api.calls
    assert any("Kostenlimit gespeichert: 5,00 €." in str(x.value) for x in at.success)


def test_einrichtung_step1_uses_provider_credentials(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "einrichtung.py")
    at.text_input(key="setup_openai_key").set_value("sk-new-key-ABC").run()
    next(b for b in at.button if b.label == "Speichern und prüfen").click().run()
    assert not at.exception, _fail_message(at)
    assert "put_cred:openai:key=yes:tier=None" in fake_api.calls
    assert "test_cred:openai" in fake_api.calls
    assert "sk-new-key-ABC" not in _texts(at)
    assert _state(at, "setup_openai_key") != "sk-new-key-ABC"  # popped in the callback
    assert any("OpenAI verbunden" in str(x.value) for x in at.success)
    assert not next(b for b in at.button if b.label == "Weiter →").disabled


def test_einrichtung_step2_gemini_tariff(fake_api: type[FakeApi]) -> None:
    at = AppTest.from_file(str(PAGES_DIR / "einrichtung.py"), default_timeout=20.0)
    at.session_state["setup_step"] = 1
    at.run()
    assert not at.exception, _fail_message(at)
    radio = at.radio(key="setup_gemini_tier")
    assert radio.value == "Nicht sicher"
    radio.set_value("Kostenlose Stufe").run()
    at.text_input(key="setup_gemini_key").set_value("AIza-test-123").run()
    at.button(key="g_save").click().run()
    assert not at.exception, _fail_message(at)
    assert "put_cred:gemini:key=yes:tier=free" in fake_api.calls
    assert "AIza-test-123" not in _texts(at)


def test_ueben_session_summary_after_new_conversation(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / "ueben.py")
    at.chat_input[0].set_value("Hallo Tutor").run()
    assert not at.exception, _fail_message(at)
    next(b for b in at.button if b.label == "Neues Gespräch").click().run()
    assert not at.exception, _fail_message(at)
    assert any("Diese Sitzung: 12 Min." in str(x.value) for x in at.success)
    assert not any(e.label == "Wofür die Kosten entstanden sind" for e in at.expander)
    assert "€" not in _texts(at)


def test_sprechen_shows_last_session_summary(fake_api: type[FakeApi]) -> None:
    at = AppTest.from_file(str(PAGES_DIR / "sprechen.py"), default_timeout=20.0)
    at.session_state["speak_last_session_id"] = "sess-1"
    at.run()
    assert not at.exception, _fail_message(at)
    assert any("Dein letztes Gespräch: 12 Min." in str(x.value) for x in at.success)


def test_speech_settings_have_one_setup_path_and_explicit_tests(fake_api: type[FakeApi]) -> None:
    at = _run(PAGES_DIR / 'einstellungen.py')
    assert not at.exception, _fail_message(at)
    labels = [b.label for b in at.button]
    assert 'Aufnahme lokal prüfen' in labels
    assert 'Stimme anhören' in labels
    assert 'Einrichtungsstatus prüfen' not in labels
    assert 'Sprache speichern' not in labels
    assert not any(m.label in ('Spracherkennung', 'Genauigkeit', 'Stimme') for m in at.metric)
    local_choice = next(r for r in at.radio if r.key == 'speech_voice_choice')
    local_choice.set_value('piper').run()
    assert not at.exception, _fail_message(at)
    assert len([b for b in at.button if b.label == 'Stimme herunterladen und einrichten']) == 1
    assert not any(b.label == 'Auswahl verwenden' for b in at.button)
    assert any(e.label == 'Weitere Spracheinstellungen' for e in at.expander)
    assert next(s for s in at.selectbox if s.key == 'speech_recognition_choice').value == 'medium'


def test_external_links_tab_with_empty_library(fake_api: type[FakeApi], monkeypatch) -> None:
    from lernapp_ui import api, exam_api
    monkeypatch.setattr(api, 'list_documents', lambda: [])
    monkeypatch.setattr(exam_api, 'listing', lambda: [])
    at = _run(PAGES_DIR / 'materialien.py')
    assert not at.exception, _fail_message(at)
    assert [tab.label for tab in at.tabs] == ['Meine Dateien', 'Externe Links']
    assert next(b for b in at.get('link_button')).label == 'Fit für den TestDaF'
    saved = []
    monkeypatch.setattr(api, 'save_link', lambda body, link_id=None: saved.append((body, link_id)))
    next(t for t in at.text_input if t.label == 'Name').set_value('My page')
    next(t for t in at.text_input if t.label == 'Internetadresse').set_value('https://example.org')
    next(b for b in at.button if b.label == 'Link hinzufügen').click().run()
    assert not at.exception, _fail_message(at)
    assert saved[0] == ({'title': 'My page', 'url': 'https://example.org', 'description': ''}, None)
