"""One-click desktop update controls; keep technical release details out of learning."""

import streamlit as st

from lernapp_ui import api


@st.fragment(run_every=2)
def _progress():
    try:
        job = api._get("/updates").get("job") or {}
        if job.get("phase") in ("complete", "failed"):
            st.rerun(scope="app")
        st.info(job.get("message", "Update läuft. Die App öffnet sich gleich wieder."))
    except api.ApiError:
        st.info("Die App wird gerade neu gestartet. Bitte kurz warten.")


def render():
    st.subheader("App aktualisieren")
    try:
        info = api._get("/updates")
        st.caption("Installierte Version: " + info["current"])
        if st.button("Nach Updates suchen", key="updates_check"):
            info = api._post("/updates/check")
        job = info.get("job") or {}
        if job.get("phase") in ("downloading", "installing"):
            _progress()
            st.caption("Bitte den Laptop eingeschaltet lassen. Die App startet nach dem Update automatisch neu.")
            return
        if job.get("phase") == "failed":
            st.warning(job["message"])
        elif job.get("phase") == "complete":
            st.success(job["message"])
        if info.get("available"):
            st.info("Version " + info["latest"] + " ist verfügbar.")
            st.write(
                "Deine Lerndaten, Antworten, Materialien und Einstellungen bleiben erhalten. Vor dem Update wird eine lokale Sicherung erstellt. Die App wird kurz geschlossen und startet danach wieder."
            )
            if st.button(
                "Update herunterladen und installieren",
                type="primary",
                disabled=not info.get("supported"),
                key="updates_install",
            ):
                result = api._post("/updates/install")
                st.info(result["message"])
                _progress()
                st.caption("Bitte kurz warten. Die App startet anschließend automatisch neu.")
        else:
            st.write(info.get("message", "Die App prüft im Hintergrund auf neue Versionen."))
        st.caption(
            "App-Updates enthalten keine neuen persönlichen Lernmaterialien. Neue Quellen werden getrennt importiert."
        )
    except api.ApiError:
        st.info("Die Update-Prüfung ist gerade nicht erreichbar. Du kannst normal weiterlernen.")
