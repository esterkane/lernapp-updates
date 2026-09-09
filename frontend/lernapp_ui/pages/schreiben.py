"""Schreiben – TestDaF-style writing task (blueprint) or own task, rubric feedback via /assess/writing."""

from __future__ import annotations

from typing import Any

import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import feedback_view, seconds_de, show_error

st.title("✍️ Schreiben")
st.caption("Schreib einen Text auf Deutsch – du bekommst eine Bewertung nach Kriterien und bessere Formulierungen.")

source = st.radio(
    "Welche Aufgabe möchtest du bearbeiten?",
    ["Aufgabe im TestDaF-Stil", "Eigene Aufgabe"],
    index=1,
    horizontal=True,
    key="write_source_business_default",
)


def _task_type_label(bp: dict[str, Any], task_type: str) -> str:
    for t in bp.get("tasks") or []:
        if t.get("task_type") == task_type and t.get("description"):
            return f"{task_type.replace('_', ' ').capitalize()} – {t['description']}"
    return task_type.replace("_", " ").capitalize()


task_id: str | None = None
task_text: str | None = None
expected_content: list[str] = []

# ------------------------------------------------------------------ blueprint task
if source == "Aufgabe im TestDaF-Stil":
    try:
        blueprints = [b for b in api.list_blueprints() if b.get("skill") == "schreiben"]
    except api.ApiError as exc:
        show_error(exc, api.HINT_CONNECTION)
        blueprints = []

    if not blueprints:
        st.info(
            "Es sind noch keine Schreibaufgaben-Vorlagen vorhanden. Du kannst trotzdem eine eigene Aufgabe eingeben."
        )
    else:
        bp = blueprints[0]
        if len(blueprints) > 1:
            bp = st.selectbox("Prüfungsformat", blueprints, format_func=lambda b: b.get("title") or b.get("id", "").replace("_", " ").replace("testdaf", "TestDaF").replace("hoeren", "Hören").replace("digital", "digital ·"), key="write_bp")
        task_types: list[str] = bp.get("task_types") or [t.get("task_type") for t in bp.get("tasks") or []]
        task_type = st.selectbox(
            "Aufgabentyp",
            task_types or ["textproduktion_mit_quellen"],
            format_func=lambda t: _task_type_label(bp, t),
            key="write_task_type",
        )
        topic = st.text_input(
            "Thema (optional)", placeholder="z. B. Homeoffice, Studiengebühren, Nachhaltigkeit", key="write_topic"
        )
        st.caption(
            "Neue Prüfungsaufgaben benötigen eine unabhängige Qualitätsprüfung; dabei kann Gemini verwendet werden. Eigene und gespeicherte Aufgaben vermeiden diese Erstellungskosten."
        )
        if st.button("Aufgabe erstellen", type="primary"):
            with st.spinner("Die Aufgabe wird erstellt und geprüft… das dauert einen Moment."):
                try:
                    st.session_state["write_task"] = api.generate_task(bp["id"], task_type, topic=topic or None)
                    st.session_state.pop("write_result", None)
                except api.ApiError as exc:
                    show_error(exc, api.HINT_KEY)

        task_resp: dict[str, Any] | None = st.session_state.get("write_task")
        if task_resp:
            task = task_resp.get("task") or {}
            if task_resp.get("status") == "rejected" or not task:
                st.warning("Diese Aufgabe hat die Qualitätsprüfung nicht bestanden. Bitte erstelle eine neue.")
                st.session_state.pop("write_task", None)
                st.stop()
            st.subheader(task.get("title") or "Schreibaufgabe")
            st.markdown(task.get("instructions_de") or "")
            if task.get("graphic_description"):
                st.info(task["graphic_description"], icon="📊")
            if task.get("source_text"):
                with st.expander("Ausgangstext", expanded=True):
                    st.write(task["source_text"])
            bp_info = task_resp.get("blueprint") or {}
            total = bp_info.get("total_time_seconds")
            if total:
                st.caption(f"In der Prüfung hättest du dafür etwa {seconds_de(total)} Zeit.")
            task_id = task_resp.get("task_id")
            expected_content = list(task.get("expected_content") or [])

# ------------------------------------------------------------------ own task
else:
    own = st.text_area(
        "Deine Aufgabe",
        placeholder="z. B. Schreiben Sie eine freundliche Zahlungserinnerung und bitten Sie um einen verbindlichen Zahlungstermin…",
        height=100,
        key="write_own_task",
    )
    task_text = own.strip() or None

st.divider()

# ------------------------------------------------------------------ learner text
learner_text = st.text_area("Dein Text", height=320, placeholder="Schreib hier deinen Text…", key="write_text")
n_words = len(learner_text.split())
st.caption(f"{n_words} Wörter" if n_words != 1 else "1 Wort")

progress_point = st.checkbox(
    "Zusätzliche Gegenprüfung mit Gemini",
    help="Optional, standardmäßig aus. Gemini erhält Aufgabe und Antwort für eine unabhängige Bewertung. Dauert länger und kann zusätzlich kosten. Das Ergebnis wird als Fortschrittsmessung gespeichert.",
    key="write_gemini_check",
)
if progress_point:
    st.caption(
        "Gemini prüft zusätzlich. Ohne diese Option erfolgt nur die erste Bewertung; dein Ergebnis wird trotzdem gespeichert."
    )

ready = bool(learner_text.strip()) and (task_id is not None or task_text is not None)
if not learner_text.strip():
    st.caption("Schreib zuerst deinen Text.")
elif task_id is None and task_text is None:
    st.caption("Erstelle zuerst eine Aufgabe oder gib eine eigene Aufgabe ein.")

if st.button("Bewerten lassen", type="primary", disabled=not ready):
    with st.spinner("Dein Text wird bewertet…" + (" Das zweite Sprachmodell prüft nach." if progress_point else "")):
        try:
            st.session_state["write_result"] = api.assess_writing(
                learner_text.strip(),
                task_id=task_id,
                task_text=task_text,
                expected_content=expected_content or None,
                is_progress_point=progress_point,
            )
        except api.ApiError as exc:
            show_error(
                exc,
                "Die Bewertung konnte nicht abgeschlossen werden. Dein Text bleibt im Eingabefeld erhalten. Bereits ausgeführte KI-Aufrufe können Kosten verursacht haben.",
            )

result = st.session_state.get("write_result")
if result:
    st.divider()
    st.subheader("Dein Feedback")
    feedback_view(result)
