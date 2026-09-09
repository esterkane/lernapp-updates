"""Start – dashboard with the three main actions, weekly focus, exam countdown, due vocab, last results."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import date_de, datetime_de, empty_state, skill_label

st.title("🏠 Start")

learner: dict[str, Any] = {}
try:
    learner = api.get_learner()
except api.ApiError as exc:
    st.warning(f"Dein Profil konnte nicht geladen werden: {exc.message_de}")

name = learner.get("display_name") or ""
st.subheader(f"Hallo{(' ' + name) if name else ''}! Was möchtest du heute üben?")

col1, col2, col3 = st.columns(3)
if col1.button("🎯 Deutsch üben", type="primary", width="stretch"):
    st.switch_page("pages/lernen.py")
if col2.button("📚 Einen Test machen", width="stretch"):
    st.switch_page("pages/modelltests.py")
if col3.button("📁 Meine Materialien", width="stretch"):
    st.switch_page("pages/materialien.py")

st.caption("Kurze Übungen für den Alltag und Beruf oder gezielt auf eine Prüfung vorbereiten.")
st.divider()

left, right = st.columns(2)

with left:
    st.markdown("### Dein Wochenfokus")
    focus = learner.get("weekly_focus")
    if focus:
        st.info(focus, icon="🎯")
    else:
        empty_state("Noch kein Wochenfokus. Nach deinen ersten Übungen erscheint hier ein Vorschlag.")

    st.markdown("### Prüfungstermin")
    exam = learner.get("exam_date")
    if exam:
        try:
            exam_day = datetime.fromisoformat(str(exam).replace("Z", "+00:00")).date()
            days_left = (exam_day - date.today()).days
            if days_left > 1:
                st.metric("Noch", f"{days_left} Tage", help=f"Prüfung am {date_de(exam_day)}")
            elif days_left == 1:
                st.metric("Noch", "1 Tag", help=f"Prüfung am {date_de(exam_day)}")
            elif days_left == 0:
                st.success("Heute ist Prüfungstag – viel Erfolg!")
            else:
                st.caption(f"Der Prüfungstermin ({date_de(exam_day)}) liegt in der Vergangenheit.")
        except ValueError:
            st.caption(f"Prüfung: {exam}")
    else:
        st.caption("Noch kein Termin eingetragen. Du kannst ihn unter „Fortschritt“ festlegen.")

with right:
    st.markdown("### Vokabeln")
    try:
        stats = api.vocab_stats()
        due = int(stats.get("due") or 0)
        total = int(stats.get("total") or 0)
        if due:
            st.metric("Heute fällig", str(due), help=f"{total} Vokabeln insgesamt")
            if st.button("Jetzt wiederholen", width="stretch"):
                st.switch_page("pages/vokabeln.py")
        elif total:
            st.success(f"Alle {total} Vokabeln sind wiederholt. Gut gemacht!")
        else:
            empty_state("Noch keine Lernkarten. Ein hochgeladenes Dokument allein legt keine Vokabeln an.")
            if st.button("Vokabeln aus einer Wortliste übernehmen"):
                st.switch_page("pages/vokabeln.py")
    except api.ApiError as exc:
        st.caption(f"Vokabeln konnten nicht geladen werden: {exc.message_de}")

    st.markdown("### Deine letzten Ergebnisse")
    try:
        results = api.learner_results(limit=3)
    except api.ApiError as exc:
        results = []
        st.caption(f"Ergebnisse konnten nicht geladen werden: {exc.message_de}")
    if not results:
        empty_state("Noch keine Ergebnisse. Deine erste Übung erscheint hier.")
    for r in results[:3]:
        skill = skill_label(r.get("skill"))
        internal = r.get("internal_score_20")
        score_text = f"interne Übungsbewertung: {internal}/20" if internal is not None else ""
        needs = " :orange-badge[Bewertung bitte prüfen]" if r.get("needs_review") else ""
        st.markdown(f"**{skill}** · {r.get('task_type') or ''} — {score_text}{needs}")
        st.caption(datetime_de(r.get("created_at")))
