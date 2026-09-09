"""One library for source PDFs, audio, and documents used by the tutor."""

import streamlit as st
from lernapp_ui import api, exam_api
from lernapp_ui.library import library_items
from lernapp_ui.links_ui import render as render_links

st.title("📁 Materialien")
st.caption("Deine Dateien und hilfreiche Websites an einem Ort.")


def render_files() -> None:
    with st.expander("Dateien hinzufügen"):
        st.write("Was möchtest du mit der Datei machen?")
        if st.button("Aufgaben aus einer Prüfungs-PDF erstellen"):
            st.session_state["exam_mode"] = "Importieren"
            st.switch_page("pages/modelltests.py")
        if st.button("Lesung mit VTT-Transkript hinzufügen"):
            st.session_state.update(exam_mode="Importieren", exam_import_type="Lesung mit Transkript")
            st.switch_page("pages/modelltests.py")
        if st.button("Vokabelkarten aus einer Wortliste erstellen"):
            st.switch_page("pages/vokabeln.py")
        if st.button("Text für Gespräche und Übungen hinzufügen"):
            st.switch_page("pages/dokumente.py")
    try:
        items = library_items(exam_api.listing(), api.list_documents())
        query = st.text_input("Material suchen", placeholder="Titel eingeben")
        items = [item for item in items if query.casefold() in item.get("title", "").casefold()]
        if not items:
            st.info("Keine passenden Materialien. Ändere die Suche oder füge eine Datei hinzu.")
            return
        item = st.selectbox("Material auswählen", items, format_func=lambda value: value["title"])
        if item["entry_type"] == "document":
            st.subheader(item["title"])
            st.caption(
                "Dieser Text steht für Gespräche und Übungen zur Verfügung. Vokabelkarten werden separat aus einer Wortliste übernommen."
            )
            if st.button("Vokabelkarten erstellen"):
                st.switch_page("pages/vokabeln.py")
            if st.button("Dokument verwalten"):
                st.switch_page("pages/dokumente.py")
        else:
            detail = exam_api.editor(item["id"])
            st.subheader(item["title"])
            if (
                item.get("reviewed")
                and item.get("question_count")
                and st.button("Mit diesem Material üben", type="primary")
            ):
                st.session_state["exam_open_practice"] = item["id"]
                st.switch_page("pages/modelltests.py")
            for warning in detail["draft"].get("warnings", []):
                st.warning(warning)
            if detail.get("source_format") == "webvtt":
                section = st.number_input("Transkriptabschnitt", 1, len(detail["pages"]), 1)
                st.write(detail["pages"][section - 1])
                st.download_button("VTT herunterladen", detail["vtt"], file_name="transkript.vtt", mime="text/vtt")
            else:
                if st.button("PDF zum Herunterladen laden"):
                    st.session_state[f"exam_pdf_{item['id']}"] = exam_api.asset(item["id"], "pdf")
                if st.session_state.get(f"exam_pdf_{item['id']}"):
                    st.download_button(
                        "PDF herunterladen",
                        st.session_state[f"exam_pdf_{item['id']}"],
                        file_name=item["title"] + ".pdf",
                        mime="application/pdf",
                    )
                page = st.number_input("Seite", 1, len(detail["pages"]), 1, key=f"material_page_{item['id']}")
                st.text(detail["pages"][page - 1])
                if st.checkbox("Originalseite mit Abbildungen anzeigen", key=f"material_original_{item['id']}"):
                    st.image(exam_api.page_image(item["id"], page), width="stretch")
            if detail.get("audio_seconds") and st.checkbox("Hördatei öffnen", key=f"material_audio_{item['id']}"):
                key = f"exam_audio_{item['id']}"
                if key not in st.session_state:
                    st.session_state[key] = exam_api.asset(item["id"], "audio")
                st.audio(st.session_state[key])
            related = detail.get("source_materials", [])
            if related:
                with st.expander("Zugehörige Lösungen und weitere Dateien"):
                    for source in related:
                        st.write(source["title"])
                    st.caption(
                        "Du findest diese Dateien über die Materialauswahl oben. Lösungen erst nach dem Üben öffnen."
                    )
    except api.ApiError as exc:
        st.error(exc.message_de)


files_tab, links_tab = st.tabs(["Meine Dateien", "Externe Links"])
with files_tab:
    render_files()
with links_tab:
    render_links()
