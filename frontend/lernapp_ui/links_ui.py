"""Bookmarks for non-technical learners: open, add, edit or remove."""

from urllib.parse import urlsplit

import streamlit as st

from lernapp_ui import api
from lernapp_ui.components import show_error


def render() -> None:
    st.subheader("Externe Links")
    st.caption(
        "Öffnet die Website in einem neuen Tab. Ergebnisse auf diesen Websites werden nicht in Lernapp gespeichert."
    )
    try:
        links = api.list_links()
    except api.ApiError as exc:
        show_error(exc)
        return
    for link in links:
        with st.container(border=True):
            st.link_button(link["title"], link["url"])
            if link.get("description"):
                st.write(link["description"])
            st.caption(urlsplit(link["url"]).netloc)
    if not links:
        st.info("Noch keine Links gespeichert. Füge unten deinen ersten Link hinzu.")
    with st.expander("Link hinzufügen oder bearbeiten", expanded=not links):
        ids = ["new", *[link["id"] for link in links]]
        by_id = {link["id"]: link for link in links}
        selected = st.selectbox(
            "Was möchtest du bearbeiten?",
            ids,
            format_func=lambda value: "Neuen Link hinzufügen" if value == "new" else by_id[value]["title"],
            key="link_editor_choice",
        )
        entry = by_id.get(selected, {})
        with st.form("link_edit_" + selected + "_" + str(st.session_state.get("link_form_revision", 0))):
            title = st.text_input("Name", value=entry.get("title", ""), max_chars=120)
            url = st.text_input("Internetadresse", value=entry.get("url", ""), placeholder="https://…", max_chars=2048)
            description = st.text_area("Beschreibung (optional)", value=entry.get("description", ""), max_chars=400)
            save = st.form_submit_button(
                "Link hinzufügen" if selected == "new" else "Änderungen speichern", type="primary"
            )
        if save:
            try:
                api.save_link(
                    {"title": title, "url": url, "description": description}, None if selected == "new" else selected
                )
            except api.ApiError as exc:
                show_error(exc)
            else:
                st.session_state["link_form_revision"] = st.session_state.get("link_form_revision", 0) + 1
                st.rerun()
        if selected != "new" and st.button("Diesen Link entfernen", key="link_delete_" + selected):
            try:
                api.remove_link(selected)
            except api.ApiError as exc:
                show_error(exc)
            else:
                st.session_state.pop("link_editor_choice", None)
                st.rerun()
