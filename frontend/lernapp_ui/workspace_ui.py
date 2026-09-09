"""Workspace selection lives in this browser session, never in a global API variable."""

import streamlit as st

from lernapp_ui import api


def switch_workspace(workspace_id: str) -> None:
    # Drop chats, tasks, audio, credentials widgets and setup flags from the prior workspace.
    for key in list(st.session_state):
        del st.session_state[key]
    st.session_state["_workspace_id"] = workspace_id


def _selected() -> None:
    switch_workspace(str(st.session_state["_workspace_select"]))


def workspace_selector() -> None:
    if st.session_state.get("_workspace_pending"):
        switch_workspace(str(st.session_state["_workspace_pending"]))
    try:
        workspaces = api.list_workspaces()
    except api.ApiError as exc:
        st.error(exc.message_de)
        st.stop()
    if not workspaces:
        st.error("Kein Lernbereich vorhanden. Bitte die App neu starten.")
        st.stop()
    names = {item["id"]: item["name"] for item in workspaces}
    current = api._current_learner()
    if current not in names:
        switch_workspace(workspaces[0]["id"])
        st.rerun()
    with st.container():
        st.selectbox(
            "Lernbereich",
            list(names),
            index=list(names).index(current),
            format_func=lambda value: names[value],
            key="_workspace_select",
            on_change=_selected,
        )
        with st.expander("Neuer Lernbereich"):
            st.caption("Eigene Gespräche, Dokumente, Schlüssel und Kosten – nur auf diesem Computer.")
            with st.form("new_workspace", clear_on_submit=True):
                name = st.text_input("Name", max_chars=120, placeholder="Zum Beispiel: Business Deutsch")
                level = st.selectbox("Sprachniveau", ["A2", "B1", "B2", "C1", "C2"], index=1)
                if st.form_submit_button("Lernbereich erstellen"):
                    try:
                        item = api.create_workspace(name.strip(), level)
                    except api.ApiError as exc:
                        st.error(exc.message_de)
                    else:
                        # Defer state clearing to the next run: widget values cannot be
                        # changed after their widgets have been instantiated.
                        st.session_state["_workspace_pending"] = item["id"]
                        st.rerun()
