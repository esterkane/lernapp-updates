"""Simple import and preparation for listening lessons with supplied subtitles."""
import streamlit as st

from lernapp_ui import exam_api


def upload():
    st.subheader("Lesung mit Transkript")
    st.caption("Hördatei und passende VTT-Untertitel hinzufügen. Die Zeitmarken verbinden Text und Aufnahme.")
    with st.form("media_upload"):
        title = st.text_input("Titel der Lesung")
        transcript = st.file_uploader("VTT-Transkript (bis 2 MB)", type=["vtt"])
        audio = st.file_uploader("Hördatei (WEBM, MP3 oder WAV, bis 100 MB)", type=["webm", "mp3", "wav"])
        if st.form_submit_button("Lesung hinzufügen"):
            if transcript is None or audio is None:
                st.warning("Bitte die Hördatei und das passende VTT-Transkript auswählen.")
            else:
                with st.spinner("Dateien werden gespeichert…"):
                    item = exam_api.upload_media(transcript, audio, title)
                st.session_state["exam_open_editor"] = item["id"]
                st.rerun()


def notes(items):
    for note in items:
        st.markdown("**" + note["phrase"] + "**")
        st.write(note["meaning"])
        if note.get("usage"):
            st.caption(note["usage"])
        if note.get("example"):
            st.write("Eigenes Alltagsbeispiel: " + note["example"])


def prepare(selected, data, sound):
    draft = data["draft"]
    total = len(data["pages"])
    completed = len(data.get("extracted_pages", []))
    st.subheader("Lesung zum Üben vorbereiten")
    st.caption("Erst Umgangssprache verstehen, dann Hör- und Leseverstehen üben. Fortschritt wird gespeichert.")
    if completed < total:
        st.info("Die Dateien sind gespeichert. Erstelle jetzt die Erklärungen und Fragen für alle Abschnitte.")
        st.caption("Die Erstellung nutzt deinen KI-Anbieter. Fertige Abschnitte bleiben bei Unterbrechungen erhalten. Eine zusätzliche Spracherkennung ist nicht nötig.")
        if st.button("Erklärungen und Fragen erstellen" if not completed else "Erstellung fortsetzen", type="primary"):
            exam_api.start_import(selected)
            st.rerun()

        @st.fragment(run_every="5s")
        def status():
            job = exam_api.import_status(selected)
            st.progress(job["completed"] / job["total"], text=f"{job['completed']} von {job['total']} Abschnitten vorbereitet")
            if job["running"]:
                st.info("Die Erstellung läuft im Hintergrund. Du kannst diese Seite verlassen.")
            elif job.get("error"):
                st.warning(job["error"])
            elif job["completed"] == job["total"]:
                st.rerun(scope="app")
        status()
    else:
        st.success(f"Alle {total} Abschnitte vorbereitet: {len(draft.get('learning_notes', []))} Erklärungen und {len(draft['questions'])} Fragen.")
        checked = st.checkbox("Ich habe Erklärungen, Fragen und Lösungen mit Transkript und Aufnahme geprüft.", key=f"media_review_{selected}_{data['revision']}")
        if st.button("Zum Üben freigeben", disabled=not checked, type="primary"):
            exam_api.save(selected, draft, True, data["revision"])
            st.session_state["exam_open_practice"] = selected
            st.rerun()
    st.warning("Automatische Untertitel können Fehler enthalten. Unklare Wörter bitte mit der Aufnahme abgleichen.")
    section = st.number_input("Transkriptabschnitt ansehen", 1, total, 1, key=f"media_section_{selected}")
    chunk = data["media_sections"][section - 1]
    st.caption(f"{int(chunk['start']) // 60}:{int(chunk['start']) % 60:02d}–{int(chunk['end']) // 60}:{int(chunk['end']) % 60:02d}")
    if st.checkbox("Diesen Hörabschnitt abspielen", key=f"media_play_{selected}"):
        st.audio(sound(selected), start_time=chunk["start"], end_time=chunk["end"])
    with st.expander("Originaltranskript", expanded=True):
        st.write(chunk["text"])
        st.download_button("Original-VTT herunterladen", data["vtt"], file_name="transkript.vtt", mime="text/vtt")
    selected_notes = [(i, n) for i, n in enumerate(draft.get("learning_notes", [])) if n["section"] == section]
    if selected_notes:
        with st.expander("Umgangssprache – Erklärungen prüfen und ändern", expanded=True):
            for i, note in selected_notes:
                with st.form(f"media_note_{selected}_{i}_{data['revision']}"):
                    phrase = st.text_input("Ausdruck im Transkript", note["phrase"])
                    meaning = st.text_area("Bedeutung in diesem Zusammenhang", note["meaning"])
                    usage = st.text_area("Sprachgebrauch / Ironie", note.get("usage", ""))
                    example = st.text_input("Eigenes Alltagsbeispiel", note.get("example", ""))
                    if st.form_submit_button("Erklärung speichern"):
                        updated = list(draft["learning_notes"])
                        updated[i] = {**note, "phrase": phrase, "meaning": meaning, "usage": usage, "example": example}
                        exam_api.save(selected, {**draft, "learning_notes": updated}, False, data["revision"])
                        st.rerun()
    with st.expander("Abschnitt erneut vorbereiten"):
        st.caption("Ersetzt die Erklärungen und Fragen dieses Abschnitts. Anbietergebühren fallen an.")
        if st.button("Diesen Abschnitt neu erstellen"):
            with st.spinner("Erklärungen und Fragen werden erstellt…"):
                exam_api.extract(selected, section, section)
            st.rerun()
    for warning in draft.get("warnings", []):
        st.caption(warning)
