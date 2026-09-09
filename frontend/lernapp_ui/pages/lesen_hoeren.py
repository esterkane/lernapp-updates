"""Lesen & Hören – deterministic tasks from blueprints, scored against the answer key (product rule 4)."""

from __future__ import annotations

from typing import Any

import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import audio_player, seconds_de, show_error

st.title("📖 Lesen & Hören")

FALLBACK_OPTIONS = ["richtig", "falsch", "Text sagt dazu nichts"]
SKILLS = {"Lesen": "lesen", "Hören": "hoeren"}

skill_choice = st.radio("Was möchtest du üben?", list(SKILLS), horizontal=True, key="lh_skill")
skill = SKILLS[skill_choice]

try:
    blueprints = [b for b in api.list_blueprints() if b.get("skill") == skill]
except api.ApiError as exc:
    show_error(exc, api.HINT_CONNECTION)
    blueprints = []

if not blueprints:
    st.info(f"Es sind noch keine Vorlagen für „{skill_choice}“ vorhanden.")
    st.stop()


def _task_type_label(bp: dict[str, Any], task_type: str) -> str:
    for t in bp.get("tasks") or []:
        if t.get("task_type") == task_type and t.get("description"):
            return f"{task_type.replace('_', ' ').capitalize()} – {t['description']}"
    return task_type.replace("_", " ").capitalize()


bp = blueprints[0]
if len(blueprints) > 1:
    bp = st.selectbox("Prüfungsformat", blueprints, format_func=lambda b: b.get("title") or b.get("id", "").replace("_", " ").replace("testdaf", "TestDaF").replace("hoeren", "Hören").replace("digital", "digital ·"), key=f"lh_bp_{skill}")
task_types: list[str] = bp.get("task_types") or [t.get("task_type") for t in bp.get("tasks") or []]
task_type = st.selectbox(
    "Aufgabentyp",
    task_types or ["standard"],
    format_func=lambda t: _task_type_label(bp, t),
    key=f"lh_task_type_{skill}",
)
topic = st.text_input("Thema (optional)", placeholder="z. B. Bildung, Arbeitswelt, Umwelt", key="lh_topic")

if st.button("Aufgabe erstellen", type="primary"):
    with st.spinner("Die Aufgabe wird erstellt und unabhängig geprüft… das dauert einen Moment."):
        try:
            st.session_state["lh_task"] = api.generate_task(bp["id"], task_type, topic=topic or None)
            st.session_state["lh_task_skill"] = skill
            st.session_state.pop("lh_result", None)
        except api.ApiError as exc:
            show_error(exc, api.HINT_KEY)

task_resp: dict[str, Any] | None = st.session_state.get("lh_task")
if not task_resp or st.session_state.get("lh_task_skill") != skill:
    st.info("Erstelle eine Aufgabe – dann erscheinen hier Text bzw. Hörbeitrag und die Fragen.", icon="💡")
    st.stop()

task = task_resp.get("task") or {}
task_id = str(task_resp.get("task_id") or "")
items: list[dict[str, Any]] = task.get("items") or []
result: dict[str, Any] | None = st.session_state.get("lh_result")

if task_resp.get("status") == "rejected" or not task:
    st.warning("Diese Aufgabe hat die Qualitätsprüfung nicht bestanden. Bitte erstelle eine neue.")
    st.session_state.pop("lh_task", None)
    st.stop()

st.divider()
st.subheader(task.get("title") or f"{skill_choice}-Aufgabe")
st.markdown(task.get("instructions_de") or "")
bp_info = task_resp.get("blueprint") or {}
if bp_info.get("total_time_seconds"):
    st.caption(f"Prüfungsdauer für den ganzen Teil: {seconds_de(bp_info['total_time_seconds'])}")

# ------------------------------------------------------------------ source
if skill == "lesen":
    if task.get("source_text"):
        with st.container(border=True):
            st.markdown("**Lesetext**")
            st.write(task["source_text"])
else:
    audio_b64 = task.get("audio_b64")
    if audio_b64:
        st.markdown("**Hörbeitrag** – du kannst ihn so oft anhören, wie du möchtest.")
        audio_player(audio_b64, task.get("audio_mime") or "audio/mpeg")
        if result:
            with st.expander("Hörtext anzeigen"):
                st.write(result.get("source_text") or task.get("source_text") or "")
    else:
        st.warning(
            "Für diese Aufgabe ist keine Stimme aktiv, deshalb gibt es keinen Hörbeitrag. "
            "Der Text wird stattdessen angezeigt – unter „Einstellungen“ kannst du eine Stimme einrichten."
        )
        if task.get("source_text"):
            with st.container(border=True):
                st.write(task["source_text"])

# ------------------------------------------------------------------ items
if not items:
    st.info("Diese Aufgabe enthält keine Fragen. Bitte erstelle eine neue Aufgabe.")
    st.stop()

st.markdown("### Fragen")
answers: dict[str, str] = {}
correct_ids = set(result.get("correct") or []) if result else set()
wrong_by_id = {str(w.get("id")): w for w in (result.get("wrong") or [])} if result else {}

for n, item in enumerate(items, 1):
    item_id = str(item.get("id") or n)
    options = [str(o) for o in (item.get("options") or [])] or FALLBACK_OPTIONS
    choice = st.radio(
        f"{n}. {item.get('question', '')}",
        options,
        index=None,
        key=f"lh_answer_{task_id}_{item_id}",
        disabled=result is not None,
    )
    if choice is not None:
        answers[item_id] = choice
    if result:
        if item_id in correct_ids:
            st.success("Richtig!", icon="✅")
        elif item_id in wrong_by_id:
            w = wrong_by_id[item_id]
            msg = f"Leider falsch. Richtig wäre: **{w.get('expected', '')}**"
            if w.get("explanation_de"):
                msg += f"\n\n{w['explanation_de']}"
            st.error(msg, icon="❌")

# ------------------------------------------------------------------ scoring
if result is None:
    missing = len(items) - len(answers)
    if missing:
        st.caption(f"Noch {missing} Frage(n) offen." if missing > 1 else "Noch 1 Frage offen.")
    if st.button("Auswerten", type="primary", disabled=missing > 0):
        with st.spinner("Deine Antworten werden ausgewertet…"):
            try:
                st.session_state["lh_result"] = api.score_task(task_id, answers)
                st.rerun()
            except api.ApiError as exc:
                show_error(exc, api.HINT_KEY)
else:
    st.divider()
    st.subheader("Dein Ergebnis")
    c1, c2 = st.columns(2)
    c1.metric("interne Übungsbewertung", f"{result.get('internal_score_20', 0)}/20")
    c2.metric("Richtig beantwortet", f"{result.get('score', 0)} von {result.get('score_max', len(items))}")
    if result.get("explanations_de"):
        st.info(result["explanations_de"], icon="💡")
    if st.button("Neue Aufgabe", type="primary"):
        st.session_state.pop("lh_task", None)
        st.session_state.pop("lh_result", None)
        st.rerun()
