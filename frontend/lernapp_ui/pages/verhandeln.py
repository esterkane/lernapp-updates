"""Verhandeln – negotiation roleplay. No corrections during the roleplay (product rule 7);
the coach report appears only after "ROLLENSPIEL ENDE" or the turn limit."""

from __future__ import annotations

from typing import Any

import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import (
    CATEGORY_LABELS,
    MOVE_LABELS,
    audio_player,
    better_formulations_table,
    chips,
    difficulty_dots,
    error_tag_label,
    feedback_view,
    session_cost_summary,
    show_error,
)

st.title("🤝 Verhandeln")

END_PHRASE = "ROLLENSPIEL ENDE"


def _audio_id(uploaded: Any) -> str:
    return str(getattr(uploaded, "file_id", None) or hash(uploaded.getvalue()))


def _move_label(move: str) -> str:
    return MOVE_LABELS.get(move, move.replace("_", " ").capitalize())


def _reset() -> None:
    for key in ("rp_session", "rp_selected", "rp_last_audio"):
        st.session_state.pop(key, None)


def _apply_turn(state: dict[str, Any], reply: dict[str, Any]) -> None:
    """Merge a turn/end response into the session state."""
    if reply.get("learner_text"):
        state["messages"].append({"role": "user", "content": reply["learner_text"]})
    if reply.get("reply_text"):
        state["messages"].append(
            {
                "role": "assistant",
                "content": reply["reply_text"],
                "audio_b64": reply.get("reply_audio_b64") or "",
                "audio_mime": reply.get("reply_audio_mime") or "audio/mpeg",
            }
        )
    state["turn"] = int(reply.get("turn") or state.get("turn") or 0)
    state["max_turns"] = int(reply.get("max_turns") or state.get("max_turns") or 0)
    state["ended"] = bool(reply.get("ended"))
    if reply.get("cost_eur_session") is not None:
        state["cost_session"] = reply.get("cost_eur_session")
    if reply.get("coach"):
        state["coach"] = reply["coach"]
    if reply.get("hidden_targets"):
        state["hidden_targets"] = reply["hidden_targets"]
    if reply.get("result_id"):
        state["result_id"] = reply["result_id"]


def _role_card(learner: dict[str, Any]) -> None:
    with st.container(border=True):
        st.markdown(f"**Deine Rolle:** {learner.get('role', '')}")
        goals = learner.get("goals") or []
        if goals:
            st.markdown("**Deine Ziele**")
            for g in goals:
                st.markdown(f"- {g}")
        constraints = learner.get("constraints") or []
        if constraints:
            st.markdown("**Darauf musst du achten**")
            for c in constraints:
                st.markdown(f"- {c}")
        if learner.get("batna"):
            st.markdown(f"**Deine Alternative, falls es nicht klappt (BATNA):** {learner['batna']}")


# ================================================================== scenario picker
state: dict[str, Any] | None = st.session_state.get("rp_session")

if state is None:
    try:
        scenarios: list[dict[str, Any]] = api.roleplay_scenarios()
    except api.ApiError as exc:
        show_error(exc, api.HINT_CONNECTION)
        scenarios = []

    selected: dict[str, Any] | None = st.session_state.get("rp_selected")

    if selected is None:
        st.caption(
            "Wähle eine Situation. Du verhandelst mit einer Gegenseite, die vom Sprachmodell gespielt wird. "
            "Während des Gesprächs wirst du nicht korrigiert – das Feedback kommt am Ende."
        )
        if not scenarios:
            st.info("Es sind noch keine Verhandlungssituationen vorhanden.", icon="🌱")
            st.stop()
        by_category: dict[str, list[dict[str, Any]]] = {}
        for sc in scenarios:
            by_category.setdefault(str(sc.get("category") or "sonstiges"), []).append(sc)
        for category, group in by_category.items():
            st.subheader(CATEGORY_LABELS.get(category, category.capitalize()))
            cols = st.columns(3)
            for i, sc in enumerate(sorted(group, key=lambda s: int(s.get("difficulty") or 1))):
                with cols[i % 3], st.container(border=True):
                    st.markdown(f"**{sc.get('title_de', sc.get('id', ''))}**")
                    st.caption(
                        f"Schwierigkeit {difficulty_dots(sc.get('difficulty'))} · {sc.get('max_turns', 16)} Züge"
                    )
                    desc = str(sc.get("description_de") or "")
                    st.write(desc if len(desc) <= 180 else desc[:177] + "…")
                    if st.button("Auswählen", key=f"rp_pick_{sc.get('id')}", width="stretch"):
                        st.session_state["rp_selected"] = sc
                        st.rerun()
        st.stop()

    # ---------------------------------------------------------- selected: role card + start
    st.subheader(selected.get("title_de") or "Rollenspiel")
    st.caption(
        f"{CATEGORY_LABELS.get(str(selected.get('category')), '')} · "
        f"Schwierigkeit {difficulty_dots(selected.get('difficulty'))} · bis zu {selected.get('max_turns', 16)} Züge"
    )
    if selected.get("description_de"):
        st.write(selected["description_de"])
    _role_card(selected.get("learner") or {})
    voice = st.toggle(
        "Mit Stimme antworten",
        value=bool(selected.get("voice")),
        help="Du sprichst deine Antworten ein statt zu tippen. Die Gegenseite antwortet dann auch mit Stimme.",
        key="rp_voice",
    )
    c1, c2 = st.columns(2)
    if c1.button("Rollenspiel starten", type="primary", width="stretch"):
        with st.spinner("Die Gegenseite bereitet sich vor…"):
            try:
                started = api.roleplay_start(str(selected.get("id")), voice=voice)
            except api.ApiError as exc:
                show_error(exc, api.HINT_KEY)
                st.stop()
        new_state: dict[str, Any] = {
            "session_id": started.get("session_id"),
            "scenario": started.get("scenario") or selected,
            "turn": int(started.get("turn") or 0),
            "max_turns": int(started.get("max_turns") or selected.get("max_turns") or 16),
            "ended": False,
            "voice": voice,
            "messages": [],
            "coach": None,
            "hidden_targets": None,
            "cost_session": None,
        }
        if started.get("opening_line"):
            new_state["messages"].append(
                {
                    "role": "assistant",
                    "content": started["opening_line"],
                    "audio_b64": started.get("opening_audio_b64") or "",
                    "audio_mime": started.get("opening_audio_mime") or "audio/mpeg",
                }
            )
        st.session_state["rp_session"] = new_state
        st.rerun()
    if c2.button("Andere Situation wählen", width="stretch"):
        st.session_state.pop("rp_selected", None)
        st.rerun()
    st.stop()

# ================================================================== running roleplay
scenario: dict[str, Any] = state.get("scenario") or {}
st.subheader(scenario.get("title_de") or "Rollenspiel")
with st.expander("Deine Rolle und Ziele", expanded=False):
    _role_card(scenario.get("learner") or {})

messages: list[dict[str, Any]] = state["messages"]
for i, msg in enumerate(messages):
    with st.chat_message("user" if msg["role"] == "user" else "assistant"):
        st.write(msg["content"])
        if msg.get("audio_b64"):
            audio_player(msg["audio_b64"], msg.get("audio_mime"), autoplay=(i == len(messages) - 1))

if not state.get("ended"):
    st.caption(f"Zug {state.get('turn', 0)} von {state.get('max_turns', 0)}")
    voice_mode = st.toggle("Mit Stimme antworten", value=bool(state.get("voice")), key="rp_voice_running")
    state["voice"] = voice_mode

    def _send_text(text: str) -> None:
        with st.spinner("Die Gegenseite überlegt…"):
            try:
                reply = api.roleplay_turn_text(str(state["session_id"]), text)
            except api.ApiError as exc:
                show_error(exc, api.HINT_KEY)
                st.stop()
        _apply_turn(state, reply)
        st.rerun()

    if voice_mode:
        recording = st.audio_input("Deine Antwort einsprechen", key="rp_audio")
        if recording is not None and st.session_state.get("rp_last_audio") != _audio_id(recording):
            st.session_state["rp_last_audio"] = _audio_id(recording)
            with st.spinner("Die App hört zu – die Gegenseite antwortet gleich…"):
                try:
                    reply = api.roleplay_turn_audio(
                        str(state["session_id"]),
                        recording.getvalue(),
                        filename=getattr(recording, "name", None) or "aufnahme.wav",
                        mime=getattr(recording, "type", None) or "audio/wav",
                    )
                except api.ApiError as exc:
                    show_error(exc, api.HINT_KEY)
                    st.stop()
            _apply_turn(state, reply)
            st.rerun()
    else:
        prompt = st.chat_input("Deine Antwort an die Gegenseite…")
        if prompt:
            _send_text(prompt)

    st.divider()
    if st.button("🏁 Rollenspiel beenden", type="primary", width="stretch"):
        _send_text(END_PHRASE)
    st.caption("Du kannst auch einfach „ROLLENSPIEL ENDE“ schreiben. Danach bekommst du dein Feedback.")

# ================================================================== coach report
else:
    st.success("Das Rollenspiel ist beendet. Hier ist dein Feedback vom Coach.")
    coach: dict[str, Any] = state.get("coach") or {}
    if not coach:
        st.info("Der Coach-Bericht ist noch nicht da. Lade die Seite gleich noch einmal.")
        if st.button("Bericht laden"):
            try:
                _apply_turn(state, api.roleplay_end(str(state["session_id"])))
                st.rerun()
            except api.ApiError as exc:
                show_error(exc)
    else:
        st.markdown("### Ergebnis im Vergleich zu deinen Zielen")
        st.write(coach.get("outcome_vs_goals") or "–")

        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Das hast du eingesetzt**")
            used = [_move_label(m) for m in coach.get("moves_used") or []]
            if used:
                chips(used, color="green")
            else:
                st.caption("Keine typischen Verhandlungszüge erkannt.")
        with c2:
            st.markdown("**Das hättest du noch nutzen können**")
            missed = [_move_label(m) for m in coach.get("moves_missed") or []]
            if missed:
                chips(missed, color="orange")
            else:
                st.caption("Nichts Wesentliches ausgelassen – stark!")

        if coach.get("language_feedback"):
            st.markdown("### Sprache")
            st.write(coach["language_feedback"])
        better_formulations_table(coach.get("better_formulations") or [])
        redemittel = coach.get("missing_redemittel") or []
        if redemittel:
            st.markdown("**Redemittel, die dir gefehlt haben**")
            for r in redemittel:
                st.markdown(f"- {r}")
        tags = coach.get("error_tags") or []
        if tags:
            st.markdown("**Woran du arbeiten kannst**")
            chips([error_tag_label(t) for t in tags], color="red")
        if coach.get("next_focus"):
            st.info(f"**Dein nächster Fokus:** {coach['next_focus']}", icon="🎯")

        rubric = coach.get("rubric_result")
        if rubric:
            st.markdown("### Bewertung nach Kriterien")
            feedback_view({"rubric": rubric, "needs_review": rubric.get("needs_review", False)})

        hidden = state.get("hidden_targets") or {}
        if hidden:
            with st.expander("Was die Gegenseite wirklich wollte"):
                for k, v in hidden.items():
                    st.markdown(f"- **{str(k).replace('_', ' ').capitalize()}:** {v}")

    if coach:
        session_cost_summary(str(state.get("session_id") or ""), title="Dieses Rollenspiel")
    if st.button("Neues Rollenspiel", type="primary"):
        _reset()
        st.rerun()
