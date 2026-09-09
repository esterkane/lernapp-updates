"""Prüfen – human labels for spot-checks and disputed assessments (ADR-0017 §2, §5).

Each item is self-contained (ReviewHandoff): task, learner text, both score sets, disagreeing criteria,
evidence, and one sentence explaining why it was selected. The label is calibration data
(evals/calibration.py) — it never changes the score the learner saw.
"""

from __future__ import annotations

from typing import Any

import streamlit as st
from lernapp_ui import api, review_api
from lernapp_ui.components import (
    LABELS_DE,
    SCORE_ANCHORS_DE,
    criterion_label,
    datetime_de,
    empty_state,
    error_tag_label,
    routing_badge,
    show_error,
    skill_label,
)

st.title("📝 Prüfen")
st.caption(
    "Hier prüfst du Stichproben und strittige Bewertungen selbst. Deine Einschätzung fließt in die "
    "Kalibrierung ein (Golden Set) und ändert die angezeigte Bewertung nicht."
)

items: list[dict[str, Any]] = []
try:
    items = review_api.learner_spotchecks()
except api.ApiError as exc:
    show_error(exc, api.HINT_CONNECTION)

if not items:
    empty_state("Nichts zu prüfen – alle Stichproben sind bearbeitet.", icon="✅")
    if st.button("Zurück zum Fortschritt"):
        st.switch_page("pages/fortschritt.py")
    st.stop()

st.markdown(f"**{len(items)} Bewertung(en) warten auf deine Prüfung.**")


def _score_table(item: dict[str, Any], handoff: dict[str, Any]) -> None:
    assessment = handoff.get("assessment_scores") or {}
    validator = handoff.get("validator_scores")
    disagreeing = set(handoff.get("disagreeing_criteria") or [])
    rows: list[dict[str, str]] = []
    for criterion in item.get("criteria") or list(assessment):
        label = criterion_label(criterion)
        if criterion in disagreeing:
            label = f"⚠️ {label}"
        row = {"Kriterium": label, "Bewertung": str(assessment.get(criterion, "–"))}
        if validator is not None:
            row["Zweitmeinung"] = str(validator.get(criterion, "–"))
        rows.append(row)
    st.table(rows)
    if disagreeing:
        st.caption("⚠️ = Zweitmeinung weicht um mindestens zwei Punkte ab.")


for item in items:
    handoff: dict[str, Any] = item.get("review_handoff") or {}
    rid = str(item.get("result_id"))
    routing = str(item.get("routing") or "")
    header = f"{skill_label(item.get('skill'))} · {item.get('task_type') or ''} · {datetime_de(item.get('created_at'))}"
    with st.expander(header, expanded=len(items) == 1):
        routing_badge(routing)
        reason = handoff.get("reason") or "Diese Bewertung wurde zur Prüfung vorgelegt."
        st.info(reason, icon="ℹ️")

        st.markdown("**Aufgabe**")
        st.write(handoff.get("task_text") or "–")
        st.markdown("**Text der Lernenden**")
        st.text_area("Text", value=handoff.get("learner_text") or "", height=180, disabled=True, key=f"text_{rid}")

        st.markdown("**Bewertung des Modells**")
        _score_table(item, handoff)

        evidence: dict[str, list[str]] = handoff.get("evidence") or {}
        if evidence:
            with st.expander("Belege aus dem Text"):
                for criterion, quotes in evidence.items():
                    st.markdown(f"*{criterion_label(criterion)}*")
                    for q in quotes:
                        st.markdown(f"> {q}")
        tags = item.get("error_tags") or []
        if tags:
            st.caption("Fehlermuster: " + ", ".join(error_tag_label(str(t)) for t in tags))

        st.markdown("**Deine Bewertung (0–4 je Kriterium)**")
        with st.form(key=f"label_form_{rid}"):
            human: dict[str, int] = {}
            assessment = handoff.get("assessment_scores") or {}
            for criterion in item.get("criteria") or list(assessment):
                default = int(assessment.get(criterion, 2) or 0)
                value = st.slider(
                    LABELS_DE.get(criterion, criterion_label(criterion)),
                    min_value=0,
                    max_value=4,
                    value=min(4, max(0, default)),
                    key=f"slider_{rid}_{criterion}",
                    help=" · ".join(f"{k} = {v}" for k, v in SCORE_ANCHORS_DE.items()),
                )
                human[criterion] = int(value)
            note = st.text_area("Anmerkung (optional)", key=f"note_{rid}", height=80)
            submitted = st.form_submit_button("Prüfung speichern", type="primary")
        if submitted:
            try:
                review_api.label_spotcheck(rid, human, note)
                st.success("Gespeichert – danke! Deine Einschätzung fließt in die Kalibrierung ein.")
                st.rerun()
            except api.ApiError as exc:
                show_error(exc, "Die Prüfung konnte nicht gespeichert werden. Bitte noch einmal versuchen.")
