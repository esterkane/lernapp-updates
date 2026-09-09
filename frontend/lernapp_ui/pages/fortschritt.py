"""Fortschritt – progress curves per skill (internal_score_20, coloured by rubric version), error tags, exam date."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import (
    SKILL_LABELS,
    date_de,
    datetime_de,
    empty_state,
    error_tag_label,
    show_error,
    skill_label,
)

st.title("📈 Fortschritt")
if st.button("Ergebnisse meiner Modelltests ansehen"):
    st.session_state["exam_mode"] = "Ergebnisse"
    st.switch_page("pages/modelltests.py")

progress: dict[str, Any] = {}
try:
    progress = api.learner_progress()
except api.ApiError as exc:
    show_error(exc, api.HINT_CONNECTION)

learner: dict[str, Any] = {}
try:
    learner = api.get_learner()
except api.ApiError as exc:
    st.caption(f"Dein Profil konnte nicht geladen werden: {exc.message_de}")

points: list[dict[str, Any]] = progress.get("points") or []
breaks: list[str] = [str(b) for b in (progress.get("version_breaks") or [])]

# ------------------------------------------------------------------ status row
c1, c2, c3 = st.columns(3)
status = progress.get("activity_status") or learner.get("activity_status")
c1.metric("Aktivität", "Aktiv" if status == "aktiv" else ("Pausiert" if status == "pausiert" else "–"))
c2.metric("Übungen mit Bewertung", str(len(points)))
progress_points = [p for p in points if p.get("is_progress_point")]
c3.metric("Fortschrittsmessungen", str(len(progress_points)))

pending = int(progress.get("pending_spotchecks") or 0)
p1, p2 = st.columns([3, 1])
with p1:
    if pending:
        st.warning(
            f"**{pending} Bewertung(en) warten auf deine Prüfung** – Stichproben und Fälle, in denen die "
            "Zweitmeinung abweicht.",
            icon="📝",
        )
    else:
        st.caption("Keine offenen Prüfungen. Jede zehnte Bewertung wird als Stichprobe zur Prüfung vorgelegt.")
with p2:
    if st.button("Prüfen", type="primary" if pending else "secondary", width="stretch", key="go_pruefen"):
        st.switch_page("pages/pruefen.py")

focus = progress.get("weekly_focus") or learner.get("weekly_focus")
if focus:
    st.info(f"**Dein Wochenfokus:** {focus}", icon="🎯")
else:
    st.caption("Noch kein Wochenfokus – er entsteht aus deinen häufigsten Fehlern nach den ersten Übungen.")

st.divider()

# ------------------------------------------------------------------ charts
st.markdown("### Deine Kurve")
st.caption(
    "Gezeigt wird die interne Übungsbewertung (0–20). Wechselt die Bewertungsversion, beginnt eine neue Farbe – "
    "Werte aus verschiedenen Versionen lassen sich nicht direkt vergleichen."
)

if not points:
    empty_state("Noch keine Ergebnisse. Nach deiner ersten bewerteten Übung erscheint hier deine Kurve.")
else:
    df = pd.DataFrame(points)
    df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce", utc=True)
    df = df.dropna(subset=["created_at"])
    df["internal_score_20"] = pd.to_numeric(df["internal_score_20"], errors="coerce")
    df["rubric_version"] = df["rubric_version"].fillna("–").astype(str)
    df["Aufgabe"] = df["task_type"].fillna("").astype(str)
    df["Zweitmeinung"] = df["needs_review"].map(lambda v: "weicht ab" if v else "ok")

    skills_present = [s for s in SKILL_LABELS if s in set(df["skill"].astype(str))]
    others = sorted(set(df["skill"].astype(str)) - set(skills_present))
    for skill in skills_present + others:
        sub = df[df["skill"] == skill].sort_values("created_at")
        st.markdown(f"**{skill_label(skill)}**")
        base = (
            alt.Chart(sub)
            .mark_line(point=True)
            .encode(
                x=alt.X("created_at:T", title="Datum"),
                y=alt.Y("internal_score_20:Q", title="interne Übungsbewertung", scale=alt.Scale(domain=[0, 20])),
                color=alt.Color("rubric_version:N", title="Bewertungsversion"),
                tooltip=[
                    alt.Tooltip("created_at:T", title="Datum", format="%d.%m.%Y %H:%M"),
                    alt.Tooltip("internal_score_20:Q", title="Bewertung"),
                    alt.Tooltip("Aufgabe:N"),
                    alt.Tooltip("rubric_version:N", title="Version"),
                    alt.Tooltip("Zweitmeinung:N"),
                ],
            )
        )
        chart: alt.Chart | alt.LayerChart = base
        break_ts = pd.to_datetime(pd.Series(breaks, dtype="object"), utc=True, errors="coerce").dropna()
        skill_breaks = sub[sub["created_at"].isin(set(break_ts))]["created_at"]
        if len(skill_breaks):
            rules = (
                alt.Chart(pd.DataFrame({"created_at": skill_breaks}))
                .mark_rule(strokeDash=[4, 4], color="gray")
                .encode(x="created_at:T")
            )
            chart = base + rules
        st.altair_chart(chart.properties(height=220), width="stretch")
    if breaks:
        st.caption(
            "Gestrichelte Linien markieren einen Wechsel der Bewertungsversion: "
            + ", ".join(datetime_de(b) for b in breaks[:5])
            + ("…" if len(breaks) > 5 else "")
        )

st.divider()

# ------------------------------------------------------------------ error tags + results
left, right = st.columns(2)
with left:
    st.markdown("### Woran du am häufigsten arbeitest")
    tags = progress.get("top_error_tags") or []
    if not tags:
        st.caption("Noch keine Fehlermuster – das ist nach den ersten Bewertungen anders.")
    else:
        tag_df = pd.DataFrame(
            {
                "Fehlerart": [error_tag_label(str(t.get("tag"))) for t in tags],
                "Anzahl": [int(t.get("count") or 0) for t in tags],
            }
        )
        bar = (
            alt.Chart(tag_df)
            .mark_bar()
            .encode(
                x=alt.X("Anzahl:Q", title="Anzahl"),
                y=alt.Y("Fehlerart:N", sort="-x", title=""),
                tooltip=["Fehlerart", "Anzahl"],
            )
            .properties(height=max(120, 28 * len(tag_df)))
        )
        st.altair_chart(bar, width="stretch")

with right:
    st.markdown("### Letzte Ergebnisse")
    if not points:
        st.caption("Noch keine Ergebnisse.")
    for p in sorted(points, key=lambda x: str(x.get("created_at") or ""), reverse=True)[:12]:
        internal = p.get("internal_score_20")
        score_text = f"interne Übungsbewertung: {internal}/20" if internal is not None else "ohne Bewertung"
        badges = ""
        if p.get("needs_review"):
            badges += " :orange-badge[Bewertung bitte prüfen]"
        elif p.get("routing") == "spot_check":
            badges += " :blue-badge[Stichprobe]"
        if p.get("is_progress_point"):
            badges += " :blue-badge[Fortschrittsmessung]"
        st.markdown(f"**{skill_label(p.get('skill'))}** · {p.get('task_type') or ''} — {score_text}{badges}")
        st.caption(datetime_de(p.get("created_at")))

st.divider()

# ------------------------------------------------------------------ exam date
st.markdown("### Prüfungstermin")
current_exam: date | None = None
raw_exam = learner.get("exam_date")
if raw_exam:
    try:
        current_exam = datetime.fromisoformat(str(raw_exam).replace("Z", "+00:00")).date()
    except ValueError:
        current_exam = None
if current_exam:
    days_left = (current_exam - date.today()).days
    if days_left >= 0:
        st.write(f"Deine Prüfung ist am **{date_de(current_exam)}** – noch **{days_left} Tage**.")
    else:
        st.write(f"Der eingetragene Termin ({date_de(current_exam)}) liegt in der Vergangenheit.")
else:
    st.caption("Noch kein Termin eingetragen.")

e1, e2 = st.columns([2, 1])
new_date = e1.date_input(
    "Prüfungstermin", value=current_exam or date.today(), format="DD.MM.YYYY", key="exam_date_input"
)
with e2:
    st.write("")
    st.write("")
    if st.button("Termin speichern", type="primary", width="stretch"):
        try:
            api.patch_learner({"exam_date": new_date.isoformat()})
            st.success("Gespeichert.")
            st.rerun()
        except api.ApiError as exc:
            show_error(exc, "Der Termin konnte nicht gespeichert werden. Bitte noch einmal versuchen.")
    if current_exam and st.button("Termin entfernen", width="stretch"):
        try:
            api.patch_learner({"exam_date": None})
            st.rerun()
        except api.ApiError as exc:
            show_error(exc, "Der Termin konnte nicht entfernt werden.")
