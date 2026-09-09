"""Sprechen – free voice conversation or a TestDaF-style speaking task with rubric feedback."""

from __future__ import annotations

import time
from typing import Any

import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import (
    audio_player,
    feedback_view,
    pronunciation_tip,
    prosody_chips,
    seconds_de,
    session_cost_summary,
    show_error,
    timing_details,
)

st.title("🎤 Sprechen")

mode = st.radio(
    "Was möchtest du machen?",
    ["Freies Gespräch", "Sprechaufgabe (TestDaF-Stil)"],
    horizontal=True,
    key="sprechen_mode",
)


def _audio_id(uploaded: Any) -> str:
    return str(getattr(uploaded, "file_id", None) or hash(uploaded.getvalue()))


# ------------------------------------------------------------------ free conversation
if mode == "Freies Gespräch":
    st.caption("Sprich einfach drauflos – die App hört zu, antwortet und gibt dir Hinweise zur Sprechweise.")
    if "speak_turns" not in st.session_state:
        st.session_state["speak_turns"] = []
    if "speak_session_id" not in st.session_state:
        st.session_state["speak_session_id"] = None

    turns: list[dict[str, Any]] = st.session_state["speak_turns"]
    for i, t in enumerate(turns):
        with st.chat_message("user"):
            st.write(t.get("learner_text") or "*(nichts verstanden)*")
        with st.chat_message("assistant"):
            st.write(t.get("reply_text") or "")
            audio_player(t.get("reply_audio_b64"), t.get("reply_audio_mime"), autoplay=(i == len(turns) - 1))
            if t.get("prosody"):
                prosody_chips(t["prosody"])
            pronunciation_tip(t.get("pronunciation_tip"))

    if not turns:
        if st.session_state.get("speak_last_session_id"):
            session_cost_summary(str(st.session_state["speak_last_session_id"]), title="Dein letztes Gespräch")
        st.info("Nimm eine kurze Aufnahme auf – zum Beispiel: „Ich möchte heute über meine Arbeit sprechen.“", icon="🎙️")

    recording = st.audio_input("Aufnahme (auf den Knopf drücken, sprechen, wieder drücken)", key="speak_audio")
    if recording is not None and st.session_state.get("speak_last_audio") != _audio_id(recording):
        st.session_state["speak_last_audio"] = _audio_id(recording)
        try:
            if not st.session_state["speak_session_id"]:
                with st.spinner("Gespräch wird gestartet…"):
                    created = api.create_session(kind="sprechen")
                st.session_state["speak_session_id"] = created["session_id"]
                st.session_state.pop("speak_last_session_id", None)
            with st.spinner("Die App hört zu und antwortet…"):
                reply = api.session_turn_audio(
                    st.session_state["speak_session_id"],
                    recording.getvalue(),
                    filename=getattr(recording, "name", None) or "aufnahme.wav",
                    mime=getattr(recording, "type", None) or "audio/wav",
                )
        except api.ApiError as exc:
            show_error(exc, api.HINT_KEY)
            st.stop()
        turns.append(reply)
        st.rerun()

    if turns:
        last = turns[-1]
        timing_details(last.get("timings_ms") or {})
        st.caption(
            f"Dauer: {seconds_de(last.get('duration_seconds'))}"
        )
        if st.button("Gespräch beenden"):
            ended_sid = str(st.session_state["speak_session_id"])
            try:
                api.end_session(ended_sid)
            except api.ApiError as exc:
                show_error(exc)
            st.session_state["speak_last_session_id"] = ended_sid
            st.session_state["speak_turns"] = []
            st.session_state["speak_session_id"] = None
            st.session_state.pop("speak_last_audio", None)
            st.rerun()

# ------------------------------------------------------------------ speaking task
else:
    st.caption(
        "Eine Sprechaufgabe im TestDaF-Stil: kurze Vorbereitung, dann sprichst du – anschließend bekommst du Feedback."
    )
    try:
        blueprints = [b for b in api.list_blueprints() if b.get("skill") == "sprechen"]
    except api.ApiError as exc:
        show_error(exc, api.HINT_CONNECTION)
        blueprints = []

    if not blueprints:
        st.info("Es sind noch keine Sprechaufgaben-Vorlagen vorhanden.")
        st.stop()

    bp = blueprints[0]
    if len(blueprints) > 1:
        bp = st.selectbox("Prüfungsformat", blueprints, format_func=lambda b: b.get("title") or b.get("id", "").replace("_", " ").replace("testdaf", "TestDaF").replace("hoeren", "Hören").replace("digital", "digital ·"), key="speak_bp")
    task_types: list[str] = bp.get("task_types") or [t.get("task_type") for t in bp.get("tasks") or []]
    task_type = st.selectbox(
        "Aufgabentyp",
        task_types or ["freie_sprechaufgabe"],
        format_func=lambda t: t.replace("_", " ").capitalize(),
        key="speak_task_type",
    )
    topic = st.text_input(
        "Thema (optional)", placeholder="z. B. Homeoffice, Studiengebühren, Nachhaltigkeit", key="speak_topic"
    )

    with st.expander("Gespeicherte Aufgabe wiederholen – ohne neue Erstellungskosten"):
        try:
            saved_tasks = [
                t
                for t in api.list_tasks(skill="sprechen", limit=100)
                if t.get("status") == "ok" and t.get("task_type") == task_type
            ]
            if saved_tasks:
                saved = st.selectbox(
                    "Geprüfte Aufgabe",
                    saved_tasks,
                    format_func=lambda t: t.get("title") or t["task_id"],
                    key="speak_saved",
                )
                if st.button("Gespeicherte Aufgabe öffnen"):
                    st.session_state["speak_task"] = api.get_task(saved["task_id"])
                    for key in ("speak_result", "speak_timer_start", "speak_task_last_audio"):
                        st.session_state.pop(key, None)
                st.caption("Neues KI-Feedback zu deiner Antwort kann weiterhin Kosten verursachen.")
            else:
                st.caption("Noch keine passende geprüfte Aufgabe gespeichert.")
        except api.ApiError as exc:
            show_error(exc, api.HINT_CONNECTION)

    st.caption(
        "Neue Prüfungsaufgaben werden unabhängig geprüft; dabei kann Gemini verwendet werden. Gespeicherte Aufgaben brauchen keine erneute Prüfung."
    )
    if st.button("Aufgabe erstellen", type="primary"):
        with st.spinner("Die Aufgabe wird erstellt und geprüft… das dauert einen Moment."):
            try:
                st.session_state["speak_task"] = api.generate_task(bp["id"], task_type, topic=topic or None)
                st.session_state.pop("speak_result", None)
                st.session_state.pop("speak_timer_start", None)
                st.session_state.pop("speak_task_last_audio", None)
            except api.ApiError as exc:
                show_error(exc, api.HINT_KEY)

    task_resp: dict[str, Any] | None = st.session_state.get("speak_task")
    if task_resp:
        task = task_resp.get("task") or {}
        if task_resp.get("status") == "rejected" or not task:
            st.warning(
                "Diese Aufgabe wurde nicht freigegeben. Die Erstellung kann trotzdem Kosten verursacht haben. Du kannst eine gespeicherte Aufgabe wiederholen."
            )
            st.session_state.pop("speak_task", None)
            st.stop()
        st.subheader(task.get("title") or "Sprechaufgabe")
        st.markdown(task.get("instructions_de") or "")
        if task.get("graphic_description"):
            st.info(task["graphic_description"], icon="📊")
        if task.get("source_text"):
            with st.expander("Ausgangstext"):
                st.write(task["source_text"])

        bp_info = task_resp.get("blueprint") or {}
        prep = task.get("preparation_seconds") or bp_info.get("preparation_seconds")
        resp_secs = task.get("response_seconds") or bp_info.get("response_seconds")
        if prep is None and resp_secs is None:
            st.caption("Für diesen Aufgabentyp ist noch keine Zeitvorgabe hinterlegt (Blueprint noch nicht geprüft).")
        else:
            st.caption(
                f"Vorbereitung: {seconds_de(prep) if prep else 'keine Angabe'} · "
                f"Sprechzeit: {seconds_de(resp_secs) if resp_secs else 'keine Angabe'}"
            )
        prep = prep or 0
        resp_secs = resp_secs or 0

        if st.button("Zeit starten ⏱️"):
            st.session_state["speak_timer_start"] = time.time()

        start = st.session_state.get("speak_timer_start")
        if start:
            elapsed = int(time.time() - float(start))
            if elapsed < prep:
                st.info(f"Vorbereitung: noch {prep - elapsed} s")
            elif elapsed < prep + resp_secs:
                st.success(f"Jetzt sprechen! Noch {prep + resp_secs - elapsed} s")
            else:
                st.warning("Die Zeit ist um. Du kannst trotzdem noch aufnehmen.")
            if st.button("Aktualisieren"):
                st.rerun()

        progress_point = st.checkbox(
            "Zusätzliche Gegenprüfung mit Gemini",
            help="Optional, standardmäßig aus. Gemini erhält Aufgabe und Antwort zur Gegenprüfung. Zusätzliche Wartezeit und mögliche Kosten. Das Ergebnis wird als Fortschrittsmessung gespeichert.",
            key="speak_gemini_check",
        )
        recording = st.audio_input("Deine Antwort aufnehmen", key="speak_task_audio")
        if (
            recording is not None
            and st.button("Bewerten lassen", type="primary")
            and st.session_state.get("speak_task_last_audio") != _audio_id(recording)
        ):
            st.session_state["speak_task_last_audio"] = _audio_id(recording)
            with st.spinner("Deine Antwort wird verstanden und bewertet…"):
                try:
                    st.session_state["speak_result"] = api.assess_speaking_audio(
                        recording.getvalue(),
                        filename=getattr(recording, "name", None) or "aufnahme.wav",
                        mime=getattr(recording, "type", None) or "audio/wav",
                        task_id=task_resp.get("task_id"),
                        is_progress_point=progress_point,
                    )
                except api.ApiError as exc:
                    show_error(exc, api.HINT_KEY)

        result = st.session_state.get("speak_result")
        if result:
            st.divider()
            st.subheader("Dein Feedback")
            feedback_view(result)
