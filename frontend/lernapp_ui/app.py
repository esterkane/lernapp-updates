"""Streamlit entry point: ``streamlit run frontend/lernapp_ui/app.py``.

Checks that the backend is reachable, redirects to the setup wizard on first run,
and builds learner navigation. Billing stays under settings.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import streamlit as st

_HERE = Path(__file__).resolve().parent
if str(_HERE.parent) not in sys.path:  # allow `streamlit run` without an installed package
    sys.path.insert(0, str(_HERE.parent))

from lernapp_ui import api  # noqa: E402

st.set_page_config(
    page_title="Lernapp – Deutsch für TestDaF & Verhandlungen",
    page_icon="🇩🇪",
    layout="wide",
    initial_sidebar_state="expanded",
)

MAX_WAIT_SECONDS = 60
RETRY_EVERY_SECONDS = 2


def _waiting_page() -> None:
    attempts = int(st.session_state.get("_startup_attempts", 0))
    st.title("Die App startet gerade…")
    if attempts * RETRY_EVERY_SECONDS < MAX_WAIT_SECONDS:
        st.write("Einen Moment bitte – die Verbindung wird gleich hergestellt.")
        st.progress(min(attempts * RETRY_EVERY_SECONDS / MAX_WAIT_SECONDS, 0.95))
        st.session_state["_startup_attempts"] = attempts + 1
        time.sleep(RETRY_EVERY_SECONDS)
        st.rerun()
    st.error("Die App konnte nicht erreicht werden.")
    st.markdown(
        "**So geht es weiter:**\n\n"
        "1. Prüfe, ob die Lernapp im Hintergrund läuft (Startprogramm oder Terminal).\n"
        "2. Starte die App neu, falls nötig.\n"
        "3. Klicke dann auf „Noch einmal versuchen“."
    )
    if st.button("Noch einmal versuchen", type="primary"):
        st.session_state["_startup_attempts"] = 0
        st.rerun()


def _sidebar() -> None:
    with st.sidebar:
        try:
            learner = api.get_learner()
            name = learner.get("display_name") or "Lernende:r"
            level = learner.get("level") or ""
            st.markdown(f"**{name}**" + (f" · Niveau {level}" if level else ""))
        except api.ApiError:
            st.markdown("**Lernende:r**")


def main() -> None:
    try:
        st.session_state["_health"] = api.health()
    except api.ApiError:
        st.session_state["_health"] = None
    if not st.session_state["_health"]:
        # Always go through st.navigation — otherwise Streamlit falls back to its file-based page list
        # (raw filenames in the sidebar) for deep links while the backend is still starting.
        st.navigation([st.Page(_waiting_page, title="Lernapp startet", icon="⏳")], position="hidden").run()
        return
    st.session_state["_startup_attempts"] = 0
    from lernapp_ui.workspace_ui import workspace_selector

    with st.sidebar.expander("Lernbereich wechseln oder hinzufügen"):
        workspace_selector()

    pages = {
        "start": st.Page("pages/start.py", title="Start", icon="🏠", default=True),
        "einrichtung": st.Page("pages/einrichtung.py", title="Einrichtung", icon="🧭"),
        "vokabeln": st.Page("pages/vokabeln.py", title="Vokabeln", icon="📚"),
        "lernen": st.Page("pages/lernen.py", title="Lernen", icon="🎯"),
        "materialien": st.Page("pages/materialien.py", title="Materialien", icon="📁"),
        "ueben": st.Page("pages/ueben.py", title="Fragen stellen", icon="💬"),
        "sprechen": st.Page("pages/sprechen.py", title="Sprechen", icon="🎤"),
        "schreiben": st.Page("pages/schreiben.py", title="Schreiben", icon="✍️"),
        "lesen_hoeren": st.Page("pages/lesen_hoeren.py", title="Lesen & Hören", icon="📖"),
        "modelltests": st.Page("pages/modelltests.py", title="Modelltests", icon="📚"),
        "verhandeln": st.Page("pages/verhandeln.py", title="Verhandeln", icon="🤝"),
        "dokumente": st.Page("pages/dokumente.py", title="Meine Dokumente", icon="📁"),
        "fortschritt": st.Page("pages/fortschritt.py", title="Fortschritt", icon="📈"),
        "pruefen": st.Page("pages/pruefen.py", title="Prüfen", icon="📝"),
        "kosten": st.Page("pages/kosten.py", title="Kosten", icon="💶"),
        "einstellungen": st.Page("pages/einstellungen.py", title="Einstellungen", icon="⚙️"),
    }
    current = st.navigation(list(pages.values()), position="hidden")
    with st.sidebar:
        st.markdown("### Lernapp")
        for name in ("start", "lernen", "modelltests", "materialien", "fortschritt"):
            st.page_link(pages[name])
        with st.expander("Einstellungen & Hilfe"):
            for name in ("einstellungen", "einrichtung", "pruefen"):
                st.page_link(pages[name])
            st.checkbox("Diagnoseinformationen anzeigen", key="show_diagnostics")

    # First run: nothing configured → go to the setup wizard once.
    if not st.session_state.get("_setup_checked"):
        st.session_state["_setup_checked"] = True
        try:
            settings = api.get_settings()
            configured = settings.get("providers_configured") or {}
            if not any(configured.values()):
                st.session_state["_first_run"] = True
                st.switch_page(pages["einrichtung"])
        except api.ApiError:
            pass

    _sidebar()
    try:
        update = api._get("/updates")
        if update.get("available"):
            with st.sidebar:
                st.info("Ein App-Update ist verfügbar.")
                if st.button("Update ansehen"):
                    st.session_state["show_updates"] = True
                    st.switch_page(pages["einstellungen"])
    except api.ApiError:
        pass
    current.run()


main()
