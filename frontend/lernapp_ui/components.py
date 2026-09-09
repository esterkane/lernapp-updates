"""Shared Streamlit widgets and German formatting helpers.

Copied label tables (never imported from ``backend.app`` — HTTP only, ADR-0009).
Product rules: "interne Übungsbewertung" / "Rubrik-Score (0–28)", never a TDN level,
pronunciation feedback is qualitative only.
"""

from __future__ import annotations

import base64
from datetime import date, datetime
from typing import Any

import streamlit as st

from lernapp_ui import api

# Copy of backend/app/core/rubrics.py::LABELS_DE (keep in sync by hand).
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

SCORE_ANCHORS_DE: dict[int, str] = {
    0: "Noch nicht gezeigt",
    1: "Erste Ansätze – hier brauchst du noch Unterstützung",
    2: "Teilweise erfüllt – mehrere Punkte brauchen noch Übung",
    3: "Gut erfüllt – einzelne Punkte fehlen noch",
    4: "Sehr gut erfüllt – entspricht dem Ziel dieser Übung",
}

CRITERION_EXPLANATIONS_DE: dict[str, str] = {
    "aufgabenbezug": "Hast du die Frage beantwortet und alle wichtigen Punkte der Aufgabe behandelt?",
    "sprachfunktionen": "Hast du wie gefordert erklärt, begründet, nachgefragt oder einen Vorschlag gemacht?",
    "quellennutzung": "Hast du die bereitgestellten Informationen und Zahlen richtig verwendet?",
    "eigenformulierung": "Drückst du die Inhalte in eigenen Worten aus?",
    "praezision": "Sind deine Aussagen genau und die Formulierungen passend für dein Gegenüber?",
    "variation": "Verwendest du unterschiedliche passende Wörter und Satzformen?",
    "korrektheit": "Stimmen Grammatik, Satzbau und beim Schreiben auch die Rechtschreibung?",
    "situationsangemessenheit": "Passt deine Ausdrucksweise zur Situation und zur Person, mit der du sprichst?",
    "fluessigkeit_verstaendlichkeit": "Kann man dir gut folgen und dich gut verstehen?",
}

SKILL_LABELS: dict[str, str] = {
    "lesen": "Lesen",
    "hoeren": "Hören",
    "schreiben": "Schreiben",
    "sprechen": "Sprechen",
    "verhandlung": "Verhandeln",
}

MOVE_LABELS: dict[str, str] = {
    "anker": "Anker setzen",
    "fragen_stellen": "Fragen stellen",
    "paketloesung": "Paketlösung",
    "schweigen": "Schweigen aushalten",
    "batna_nutzen": "BATNA nutzen",
    "zusammenfassen": "Zusammenfassen",
    "bedenken_aeussern": "Bedenken äußern",
    "zeit_gewinnen": "Zeit gewinnen",
}

CATEGORY_LABELS: dict[str, str] = {
    "gehalt": "Gehalt",
    "einkauf": "Einkauf",
    "vertrieb": "Vertrieb",
    "projektumfang": "Projektumfang",
    "beschwerde": "Beschwerde",
    "meeting": "Meeting",
}

TAG_LABELS: dict[str, str] = {
    "vokabeln": "Vokabeln",
    "verhandlung": "Verhandlung",
    "testdaf": "TestDaF",
    "sonstiges": "Sonstiges",
}

USE_IN_LABELS: dict[str, str] = {
    "uebungen": "Übungen",
    "rollenspiel": "Rollenspiel",
    "tutor": "Tutor",
}

_ERROR_CATEGORY_DE: dict[str, str] = {
    "grammatik": "Grammatik",
    "wortschatz": "Wortschatz",
    "text": "Text",
    "sprechfunktion": "Sprachfunktion",
    "verhandlung": "Verhandlung",
    "aussprache": "Aussprache",
}

_ERROR_LEAF_DE: dict[str, str] = {
    "kasus": "Kasus",
    "genus": "Genus",
    "adjektivdeklination": "Adjektivdeklination",
    "verbstellung_nebensatz": "Verbstellung im Nebensatz",
    "konjunktiv2": "Konjunktiv II",
    "passiv": "Passiv",
    "praeposition": "Präposition",
    "tempus": "Tempus",
    "relativsatz": "Relativsatz",
    "nominalisierung": "Nominalisierung",
    "register_zu_umgangssprachlich": "Register zu umgangssprachlich",
    "falscher_freund": "Falscher Freund",
    "wiederholung": "Wiederholung",
    "kollokation": "Kollokation",
    "fachterminus_fehlt": "Fachbegriff fehlt",
    "kohaerenz": "Kohärenz",
    "konnektoren": "Konnektoren",
    "absatzstruktur": "Absatzstruktur",
    "quellenkopie": "Quelle kopiert",
    "aufgabenteil_fehlt": "Aufgabenteil fehlt",
    "zusammenfassen": "Zusammenfassen",
    "abwaegen": "Abwägen",
    "stellung_nehmen": "Stellung nehmen",
    "begruenden": "Begründen",
    "vorschlagen": "Vorschlagen",
    "kein_ziel_genannt": "Kein Ziel genannt",
    "zu_frueh_nachgegeben": "Zu früh nachgegeben",
    "keine_frage_gestellt": "Keine Frage gestellt",
    "register": "Register",
    "redemittel_fehlen": "Redemittel fehlen",
    "undeutlich_flag": "Undeutlich gesprochen",
    "tempo_hoch": "Tempo zu hoch",
    "lange_pausen": "Lange Pausen",
    "fuellwoerter": "Füllwörter",
}


# ---------------------------------------------------------------- cost tracking v2 (docs/api.md, ADR-0019)
# Wording rule: "geschätzt" / "voraussichtlich" – the app never claims what the provider bills.
COST_TOOLTIP_DE = (
    "Die Kosten werden aus der vom AI-Anbieter gemeldeten Nutzung und der hinterlegten Preisliste berechnet. "
    "Die endgültige Abrechnung deines Anbieters kann geringfügig abweichen."
)

PROVIDER_LABELS: dict[str, str] = {
    "openai": "OpenAI",
    "gemini": "Google Gemini",
    "google": "Google",
    "anthropic": "Anthropic",
    "mistral": "Mistral",
    "azure": "Microsoft Azure",
    "local": "Dein Gerät (lokal)",
}

SERVICE_LABELS: dict[str, str] = {
    "llm": "Sprachmodell",
    "chat": "Sprachmodell",
    "stt": "Spracherkennung",
    "tts": "Stimme",
    "pron": "Aussprache-Analyse",
    "embed": "Dokumentsuche",
    "embedding": "Dokumentsuche",
}

COST_STATUS_LABELS: dict[str, str] = {
    "free": "kostenlos",
    "estimated": "geschätzt",
    "unknown": "unbekannt",
    "confirmed": "bestätigt",
}

PRICING_TIER_LABELS: dict[str, str] = {
    "free": "Kostenlose Stufe",
    "paid": "Kostenpflichtige API",
    "unknown": "Nicht sicher",
}

USAGE_UNIT_LABELS: dict[str, str] = {
    "input_tokens": "Eingabe-Tokens",
    "cached_input_tokens": "zwischengespeicherte Eingabe-Tokens",
    "output_tokens": "Ausgabe-Tokens",
    "reasoning_tokens": "Denk-Tokens",
    "audio_input_seconds": "Audio-Sekunden (Eingabe)",
    "audio_output_seconds": "Audio-Sekunden (Ausgabe)",
    "audio_seconds": "Audio-Sekunden",
    "characters": "Zeichen",
    "requests": "Anfragen",
}


# ---------------------------------------------------------------- formatting


def money(x: float | int | None) -> str:
    """German currency formatting: 1234.5 → '1.234,50 €'; tiny non-zero amounts → '< 0,01 €'."""
    value = float(x or 0.0)
    if 0 < value < 0.005:
        return "< 0,01 €"
    s = f"{value:,.2f}"
    s = s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")
    return f"{s} €"


def number_de(x: float | int | None, decimals: int = 1) -> str:
    value = float(x or 0.0)
    s = f"{value:,.{decimals}f}"
    return s.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def date_de(value: str | date | datetime | None) -> str:
    if not value:
        return "–"
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        parsed = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    return parsed.strftime("%d.%m.%Y")


def datetime_de(value: str | datetime | None) -> str:
    if not value:
        return "–"
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    else:
        parsed = value
    return parsed.strftime("%d.%m.%Y %H:%M")


def seconds_de(seconds: float | int | None) -> str:
    total = int(seconds or 0)
    minutes, secs = divmod(total, 60)
    if minutes and secs:
        return f"{minutes} Min. {secs} s"
    if minutes:
        return f"{minutes} Min."
    return f"{secs} s"


def duration_de(seconds: float | int | None) -> str:
    """Learning time for the cost page: 0 → '0 Min.', 2280 → '38 Min.', 4320 → '1 Std. 12 Min.'."""
    secs = float(seconds or 0.0)
    if 0 < secs < 30:
        return "unter 1 Min."
    total_minutes = int(round(secs / 60.0))
    hours, minutes = divmod(total_minutes, 60)
    if hours and minutes:
        return f"{hours} Std. {minutes} Min."
    if hours:
        return f"{hours} Std."
    return f"{minutes} Min."


def provider_label(provider: str | None, label_de: str | None = None) -> str:
    if label_de:
        return label_de
    key = (provider or "").lower()
    return PROVIDER_LABELS.get(key, (provider or "–").capitalize())


def service_label(service: str | None, label_de: str | None = None) -> str:
    if label_de:
        return label_de
    key = (service or "").lower()
    return SERVICE_LABELS.get(key, (service or "–").capitalize())


def cost_status_label(status: str | None) -> str:
    return COST_STATUS_LABELS.get((status or "").lower(), status or "–")


def pricing_tier_label(tier: str | None) -> str:
    return PRICING_TIER_LABELS.get((tier or "").lower(), "Nicht sicher")


def usage_unit_label(unit: Any) -> str:
    return USAGE_UNIT_LABELS.get(str(unit), str(unit).replace("_", " "))


def budget_warning_level(budget: dict[str, Any] | None) -> int:
    """``warning_level`` is ``none|80|95|100`` (string or int) → 0/80/95/100."""
    raw = (budget or {}).get("warning_level")
    if raw is None:
        return 0
    try:
        return int(str(raw).strip())
    except ValueError:
        return 0


def usage_units_de(event: dict[str, Any]) -> str:
    """Human-readable usage of one event: tokens, audio seconds or characters (advanced view only)."""
    parts: list[str] = []
    tokens_in = int(event.get("input_tokens") or 0)
    tokens_out = int(event.get("output_tokens") or 0)
    if tokens_in or tokens_out:
        parts.append(f"{number_de(tokens_in, 0)} Tokens ein / {number_de(tokens_out, 0)} Tokens aus")
    audio = float(event.get("audio_input_seconds") or 0.0) + float(event.get("audio_output_seconds") or 0.0)
    if audio:
        parts.append(f"{number_de(audio, 0)} s Audio")
    chars = int(event.get("characters") or 0)
    if chars:
        parts.append(f"{number_de(chars, 0)} Zeichen")
    return " · ".join(parts) if parts else "–"


def error_tag_label(tag: str) -> str:
    """'grammatik/kasus' → 'Grammatik: Kasus'."""
    if not tag:
        return ""
    category, _, leaf = tag.partition("/")
    cat_de = _ERROR_CATEGORY_DE.get(category, category.replace("_", " ").capitalize())
    if not leaf:
        return cat_de
    leaf_de = _ERROR_LEAF_DE.get(leaf, leaf.replace("_", " ").capitalize())
    return f"{cat_de}: {leaf_de}"


def criterion_label(criterion: str) -> str:
    return LABELS_DE.get(criterion, criterion.replace("_", " ").capitalize())


def difficulty_dots(difficulty: int | None) -> str:
    return "●" * max(1, min(3, int(difficulty or 1)))


def skill_label(skill: str | None) -> str:
    return SKILL_LABELS.get(skill or "", (skill or "").capitalize())


# ---------------------------------------------------------------- small widgets


def chips(labels: list[str], color: str = "blue") -> None:
    """Render short labels as coloured badges in one markdown line."""
    clean = [str(x).replace("]", ")").replace("[", "(") for x in labels if x]
    if not clean:
        return
    st.markdown(" ".join(f":{color}-badge[{label}]" for label in clean))


def audio_player(b64: str | None, mime: str | None = "audio/mpeg", autoplay: bool = False) -> None:
    """Play base64 audio from an API response; silently skips when empty (no TTS active)."""
    if not b64:
        return
    try:
        data = base64.b64decode(b64)
    except (ValueError, TypeError):
        return
    if not data:
        return
    st.audio(data, format=mime or "audio/mpeg", autoplay=autoplay)


def prosody_chips(prosody: dict[str, Any] | None) -> None:
    """Qualitative speech flags (product rule 6: never a percentage)."""
    if not prosody:
        return
    labels: list[str] = []
    tempo = prosody.get("tempo_label_de")
    if tempo:
        labels.append(f"Sprechtempo: {tempo}")
    pauses = prosody.get("long_pauses") or []
    if pauses:
        labels.append("1 lange Pause" if len(pauses) == 1 else f"{len(pauses)} lange Pausen")
    n_fillers = int(prosody.get("n_fillers") or 0)
    if n_fillers:
        labels.append("1 Füllwort" if n_fillers == 1 else f"{n_fillers} Füllwörter")
    reps = prosody.get("repetitions") or []
    if reps:
        labels.append("1 Wiederholung" if len(reps) == 1 else f"{len(reps)} Wiederholungen")
    corrections = prosody.get("self_corrections") or []
    if corrections:
        labels.append(f"{len(corrections)} Selbstkorrektur(en)")
    flags = prosody.get("pronunciation_flags") or []
    for flag in flags[:3]:
        word = flag.get("word") if isinstance(flag, dict) else str(flag)
        reason = (flag.get("reason") if isinstance(flag, dict) else "") or "möglicherweise undeutlich"
        labels.append(f"{reason}: {word}")
    if labels:
        chips(labels, color="orange")
    else:
        st.caption("Keine Auffälligkeiten beim Sprechen erkannt.")


def pronunciation_tip(tip: dict[str, Any] | None) -> None:
    if not tip or not tip.get("tip_de"):
        return
    words = tip.get("practice_words") or []
    text = f"**Tipp zur Aussprache:** {tip['tip_de']}"
    if words:
        text += "\n\nÜbungswörter: " + ", ".join(f"*{w}*" for w in words)
    st.info(text)


def better_formulations_table(pairs: list[Any]) -> None:
    rows: list[dict[str, str]] = []
    for pair in pairs or []:
        if isinstance(pair, dict):
            rows.append(
                {"Original": str(pair.get("original", "")), "Besser": str(pair.get("improved", pair.get("besser", "")))}
            )
        elif isinstance(pair, list | tuple) and len(pair) >= 2:
            rows.append({"Original": str(pair[0]), "Besser": str(pair[1])})
    if not rows:
        return
    st.markdown("**Bessere Formulierungen**")
    st.table(rows)


ROUTING_LABELS: dict[str, tuple[str, str]] = {
    "auto_accept": ("automatisch akzeptiert", "green"),
    "needs_review": ("Bewertung bitte prüfen", "orange"),
    "spot_check": ("Stichprobe – bitte prüfen", "blue"),
    "labelled": ("von dir geprüft", "violet"),
}


def routing_label(routing: str | None) -> str:
    return ROUTING_LABELS.get(routing or "", ("", ""))[0]


def routing_badge(routing: str | None) -> None:
    """Review-routing badge (ADR-0017): auto_accept / needs_review / spot_check / labelled."""
    label, color = ROUTING_LABELS.get(routing or "", ("", ""))
    if label:
        st.markdown(f":{color}-badge[{label}]")


def review_notice_text(rubric: dict[str, Any]) -> str:
    validator = rubric.get("validator") or {}
    if validator and validator.get("disagreeing_criteria"):
        return "Zweitmeinung weicht ab: Die beiden Bewertungen unterscheiden sich deutlich. Bitte prüfe die betroffenen Kriterien."
    reasons = (rubric.get("review_handoff") or {}).get("reasons") or []
    if any(str(reason).startswith("integration:evidence_in_text:") for reason in reasons):
        return "Bewertung bitte prüfen: Einige angeführte Textbelege wurden in deinem Text nicht gefunden. Das betrifft die Zuverlässigkeit des Feedbacks, nicht automatisch die Qualität deines Textes."
    if any(str(reason).startswith("confidence:") for reason in reasons):
        return "Bewertung bitte prüfen: Das Modell war sich bei einzelnen Kriterien unsicher."
    return "Bewertung bitte prüfen: Eine interne Qualitätsprüfung hat Prüfbedarf festgestellt. Das bedeutet nicht, dass eine zweite KI beteiligt war."


def needs_review_notice(needs_review: bool, rubric: dict[str, Any] | None = None) -> None:
    if needs_review:
        st.warning(review_notice_text(rubric or {}))


def criterion_bars(scores: list[dict[str, Any]]) -> None:
    for entry in scores or []:
        criterion = str(entry.get("criterion", ""))
        score = int(entry.get("score") or 0)
        label = criterion_label(criterion)
        anchor = SCORE_ANCHORS_DE.get(score, "")
        st.markdown(f"**{label}: {anchor}**")
        explanation = CRITERION_EXPLANATIONS_DE.get(criterion)
        if explanation:
            st.caption(explanation)
        st.progress(min(max(score / 4.0, 0.0), 1.0), text=f"{score} von 4 Punkten für dieses Kriterium")
        comment = entry.get("comment_de")
        evidence = entry.get("evidence") or []
        if comment:
            st.write(comment)
        if evidence:
            with st.expander(f"Textbelege zu „{label}“"):
                for quote in evidence:
                    st.markdown(f"> {quote}")


def feedback_view(result: dict[str, Any]) -> None:
    """Render an assessment result (POST /assess/writing, /assess/speaking, roleplay rubric)."""
    if not result:
        st.info("Noch keine Bewertung vorhanden.")
        return
    rubric: dict[str, Any] = result.get("rubric") or {}
    scores = rubric.get("scores") or result.get("scores") or []
    total = result.get("total")
    if total is None:
        total = sum(int(s.get("score") or 0) for s in scores)
    total_max = result.get("total_max") or (4 * len(scores) if scores else 28)
    internal = result.get("internal_score_20")
    if internal is None and total_max:
        internal = round(float(total) / float(total_max) * 20)

    col1, col2 = st.columns(2)
    col1.metric("interne Übungsbewertung", f"{internal}/20")
    col2.metric("Rubrik-Score (0–28)", f"{total}/{total_max}")
    routing_badge(result.get("routing") or rubric.get("routing"))
    needs_review_notice(bool(result.get("needs_review") or rubric.get("needs_review")), rubric)

    summary = rubric.get("summary_de") or result.get("summary_de")
    if summary:
        st.markdown(f"**Zusammenfassung:** {summary}")

    if result.get("transcript"):
        with st.expander("So wurde dein Beitrag verstanden"):
            st.write(result["transcript"])
    if result.get("prosody"):
        st.markdown("**Sprechweise**")
        prosody_chips(result.get("prosody"))
    pronunciation_tip(result.get("pronunciation_tip"))

    if scores:
        st.markdown("**Bewertung nach Kriterien**")
        criterion_bars(scores)

    better_formulations_table(rubric.get("better_formulations") or result.get("better_formulations") or [])

    vocab = rubric.get("new_vocabulary") or result.get("new_vocabulary") or []
    if vocab:
        st.markdown("**Neue Wörter für dich**")
        chips(list(vocab), color="green")

    tags = rubric.get("error_tags") or result.get("error_tags") or []
    if tags:
        st.markdown("**Woran du arbeiten kannst**")
        chips([error_tag_label(t) for t in tags], color="red")



def session_cost_summary(session_id: str | None, title: str = "Diese Sitzung") -> None:
    """Keep learning feedback free of billing amounts; details live in settings."""
    if not session_id:
        return
    try:
        data = api.usage_session(session_id)
    except api.ApiError:
        return
    st.success(f"{title}: {duration_de(data.get('duration_seconds'))}")


def show_error(exc: Exception, next_step: str | None = None) -> None:
    """Uniform error box: German message plus a concrete next step."""
    message = getattr(exc, "message_de", None) or str(exc)
    if next_step:
        message = f"{message}\n\n{next_step}"
    st.error(message)


def empty_state(text: str, icon: str = "🌱") -> None:
    st.info(text, icon=icon)


def timing_details(timings: dict[str, Any]) -> None:
    """Optional technical details; values contain durations only."""
    if not timings or not st.session_state.get("show_diagnostics"):
        return
    labels = {
        "speech_input": "Spracherkennung und Audioanalyse",
        "reply_and_context": "Kontextsuche und KI-Antwort",
        "retrieval": "Dokumentsuche insgesamt",
        "query_embedding": "Suchanfrage vorbereiten (Embedding oder Cache)",
        "retrieval_database": "Datenbanksuche und Trefferzusammenführung",
        "answer_generation": "KI-Antwort erstellen",
        "voice_and_tip": "Vorlesen und zusätzlicher Tipp",
        "total": "Verarbeitung insgesamt",
    }
    with st.expander("Technische Laufzeitdetails"):
        for key, label in labels.items():
            value = timings.get(key)
            if isinstance(value, (int, float)):
                st.caption(f"{label}: {value / 1000:.2f} s")
        st.caption("Server-Verarbeitungszeit; ohne Aufnahmezeit, Netzwerk zum Browser und Wiedergabe.")
