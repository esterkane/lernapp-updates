"""Üben – text tutor chat with a due-vocabulary flip-card widget in the sidebar."""

from __future__ import annotations

from typing import Any

import streamlit as st
from lernapp_ui import api
from lernapp_ui.components import audio_player, session_cost_summary, show_error, timing_details

st.title("💬 Fragen stellen")
st.caption(
    "Zum Einstieg: Bitte übe mit mir ein Gespräch über eine überfällige Rechnung – freundlich, klar und verbindlich. Wir verwenden fiktive Fälle."
)

if "tutor_messages" not in st.session_state:
    st.session_state["tutor_messages"] = []  # list[{"role", "content", "citations", "audio_b64", "audio_mime"}]
if "tutor_session_id" not in st.session_state:
    st.session_state["tutor_session_id"] = None


if st.button("Vokabeln wiederholen"):
    st.switch_page("pages/vokabeln.py")

# ------------------------------------------------------------------ header actions
top_left, top_right = st.columns([3, 1])
with top_left:
    st.caption(
        "Schreib dem Tutor auf Deutsch – er antwortet, korrigiert behutsam und nutzt deine hochgeladenen Dokumente."
    )
with top_right:
    if st.button("Neues Gespräch", width="stretch"):
        sid = st.session_state.get("tutor_session_id")
        if sid:
            try:
                api.end_session(sid)
            except api.ApiError as exc:
                st.warning(f"Die Sitzung konnte nicht sauber beendet werden: {exc.message_de}")
            st.session_state["tutor_last_session_id"] = sid
        st.session_state["tutor_messages"] = []
        st.session_state["tutor_session_id"] = None
        st.rerun()

# Summary of the conversation that just ended – stays until the next conversation starts.
if st.session_state.get("tutor_last_session_id") and not st.session_state.get("tutor_session_id"):
    session_cost_summary(str(st.session_state["tutor_last_session_id"]))

# ------------------------------------------------------------------ chat history
messages: list[dict[str, Any]] = st.session_state["tutor_messages"]
if not messages:
    st.info(
        "Noch keine Nachrichten. Stell eine Frage oder erzähl etwas – zum Beispiel: „Erkläre mir den Konjunktiv II.“",
        icon="💡",
    )

for msg in messages:
    with st.chat_message("user" if msg["role"] == "user" else "assistant"):
        st.write(msg["content"])
        timing_details(msg.get("timings_ms") or {})
        if msg.get("citations"):
            cites = ", ".join(dict.fromkeys(c.get("title", "") for c in msg["citations"]))
            st.caption(f"Quellen aus deinen Dokumenten: {cites}")
        if msg.get("audio_b64"):
            audio_player(msg["audio_b64"], msg.get("audio_mime"))

# ------------------------------------------------------------------ input
read_aloud = st.checkbox(
    "Antworten vorlesen",
    value=False,
    help="Text erscheint zuerst. Vorlesen wird danach geladen und kann je nach Anbieter Kosten verursachen.",
)
prompt = st.chat_input("Deine Nachricht an den Tutor…")
if prompt:
    messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.write(prompt)
    try:
        if not st.session_state["tutor_session_id"]:
            with st.spinner("Neues Gespräch wird gestartet…"):
                created = api.create_session(kind="tutor")
            st.session_state["tutor_session_id"] = created["session_id"]
            st.session_state.pop("tutor_last_session_id", None)
        with st.spinner("Der Tutor überlegt…"):
            reply = api.session_turn_text(st.session_state["tutor_session_id"], prompt, defer_audio=True)
    except api.ApiError as exc:
        show_error(exc, api.HINT_KEY)
        st.stop()
    assistant_msg = {
        "timings_ms": reply.get("timings_ms") or {},
        "role": "assistant",
        "content": reply.get("reply_text") or "",
        "citations": reply.get("citations") or [],
        "audio_b64": reply.get("reply_audio_b64") or "",
        "audio_mime": reply.get("reply_audio_mime") or "audio/mpeg",
    }
    messages.append(assistant_msg)
    with st.chat_message("assistant"):
        st.write(assistant_msg["content"])
        timing_details(assistant_msg["timings_ms"])
        if assistant_msg["citations"]:
            cites = ", ".join(dict.fromkeys(c.get("title", "") for c in assistant_msg["citations"]))
            st.caption(f"Quellen aus deinen Dokumenten: {cites}")
        if read_aloud:
            with st.spinner("Stimme wird geladen…"):
                try:
                    speech = api._post(
                        f"/sessions/{st.session_state['tutor_session_id']}/turns/{reply['turn_ord'] + 1}/audio"
                    )
                    assistant_msg["audio_b64"] = speech.get("reply_audio_b64") or ""
                    assistant_msg["audio_mime"] = speech.get("reply_audio_mime")
                    reply["cost_eur_session"] = speech.get("cost_eur_session", reply.get("cost_eur_session"))
                    if speech.get("message"):
                        st.caption(speech["message"])
                except api.ApiError as exc:
                    show_error(exc, api.HINT_CONNECTION)
            audio_player(assistant_msg["audio_b64"], assistant_msg["audio_mime"], autoplay=True)
