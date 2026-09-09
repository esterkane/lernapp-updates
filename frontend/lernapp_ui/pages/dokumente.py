"""Meine Dokumente – drag & drop upload, tags, "Verwenden in" toggles, list, search (rag-documents skill)."""

from __future__ import annotations

from typing import Any

import streamlit as st
from lernapp_ui import api, exam_api
from lernapp_ui.components import TAG_LABELS, USE_IN_LABELS, chips, datetime_de, empty_state, show_error

st.title("Dateien für meine Übungen")
if st.button("← Zur Materialbibliothek"):
    st.switch_page("pages/materialien.py")
st.caption(
    "Lade eigene Texte, Notizen oder Vokabellisten hoch. Die App nutzt sie in Übungen, im Rollenspiel und beim Tutor."
)

STATUS_LABELS: dict[str, str] = {
    "hochgeladen": "Hochgeladen",
    "extrahiert": "Text gelesen",
    "gechunkt": "Wird vorbereitet",
    "indexiert": "Für Gespräche bereit",
    "fehler": "Fehler",
    "error": "Fehler",
}
STATUS_COLORS: dict[str, str] = {"indexiert": "green", "fehler": "red", "error": "red"}
TAG_BY_LABEL = {label: tag for tag, label in TAG_LABELS.items()}
MIME_BY_SUFFIX = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "txt": "text/plain",
    "md": "text/markdown",
    "csv": "text/csv",
}


def _mime(uploaded: Any) -> str:
    explicit = getattr(uploaded, "type", None)
    if explicit:
        return str(explicit)
    suffix = str(getattr(uploaded, "name", "")).rsplit(".", 1)[-1].lower()
    return MIME_BY_SUFFIX.get(suffix, "application/octet-stream")


def _status_badge(status: str | None) -> str:
    key = str(status or "")
    return f":{STATUS_COLORS.get(key, 'blue')}-badge[{STATUS_LABELS.get(key, key.capitalize() or 'Unbekannt')}]"


# ------------------------------------------------------------------ upload
st.markdown("### Hochladen")
files = st.file_uploader(
    "Dateien hierher ziehen oder auswählen",
    type=["pdf", "docx", "txt", "md", "csv"],
    accept_multiple_files=True,
    key="doc_uploader",
)
with st.expander("Optional: Schlagwörter und Verwendung anpassen"):
    tag_labels = st.multiselect(
        "Schlagwörter", list(TAG_LABELS.values()), default=["Sonstiges"], key="doc_tags", help="Wofür sind die Dateien?"
    )
    st.markdown("**Verwenden in:**")
    u1, u2, u3 = st.columns(3)
    use_in_flags = {
        "uebungen": u1.checkbox(USE_IN_LABELS["uebungen"], value=True, key="doc_use_uebungen"),
        "rollenspiel": u2.checkbox(USE_IN_LABELS["rollenspiel"], value=True, key="doc_use_rollenspiel"),
        "tutor": u3.checkbox(USE_IN_LABELS["tutor"], value=True, key="doc_use_tutor"),
    }

with st.expander("Vokabelliste als CSV-Datei hochladen – so muss sie aussehen"):
    st.write(
        "Eine Tabelle mit den Spalten **wort**, **bedeutung** und optional **beispiel**. "
        "Die erste Zeile enthält die Spaltennamen. Jede weitere Zeile ist eine Vokabel:"
    )
    st.code(
        "wort,bedeutung,beispiel\ndie Verhandlung,negotiation,Die Verhandlung dauerte zwei Stunden.\n", language="text"
    )
    st.caption("Die Vokabeln findest du unter „Lernen“ → „Vokabeln“.")

if st.button("Hochladen", type="primary", disabled=not files):
    tags = [TAG_BY_LABEL[label] for label in tag_labels if label in TAG_BY_LABEL]
    use_in = [k for k, on in use_in_flags.items() if on]
    total = len(files or [])
    progress = st.progress(0.0, text="Hochladen…")
    outcomes: list[dict[str, Any]] = []
    for n, uploaded in enumerate(files or [], 1):
        progress.progress((n - 1) / total, text=f"{n} von {total}: {uploaded.name} wird verarbeitet…")
        try:
            res = api.upload_document(uploaded.getvalue(), uploaded.name, _mime(uploaded), tags=tags, use_in=use_in)
            outcomes.append({"name": uploaded.name, "ok": True, "res": res})
        except api.ApiError as exc:
            outcomes.append({"name": uploaded.name, "ok": False, "error": exc.message_de})
        progress.progress(n / total, text=f"{n} von {total} fertig")
    progress.empty()
    for o in outcomes:
        if o["ok"]:
            res = o["res"]
            details = []
            if res.get("n_chunks"):
                details.append(f"{res['n_chunks']} Abschnitte")
            if res.get("n_vocab_items"):
                details.append(f"{res['n_vocab_items']} Vokabeln")
            st.success(
                f"**{o['name']}** – {STATUS_LABELS.get(str(res.get('status')), res.get('status', ''))}. "
                + " · ".join(details)
            )
        else:
            st.error(
                f"**{o['name']}** konnte nicht verarbeitet werden: {o['error']} Prüfe die Datei und versuche es noch einmal."
            )

st.divider()

# ------------------------------------------------------------------ search
st.markdown("### In meinen Dokumenten suchen")
query = st.text_input("Suchbegriff", placeholder="z. B. Kündigungsfrist, Preisverhandlung…", key="doc_search_q")
if query.strip():
    try:
        hits = api.search_documents(query.strip(), k=8)
    except api.ApiError as exc:
        show_error(exc, api.HINT_CONNECTION)
        hits = []
    if not hits:
        st.caption("Nichts gefunden. Probiere ein anderes Wort oder lade zuerst ein Dokument hoch.")
    for hit in hits:
        with st.container(border=True):
            heading = f" › {hit['heading']}" if hit.get("heading") else ""
            st.markdown(f"**{hit.get('document_title', '')}** §{hit.get('ord', '')}{heading}")
            st.write(hit.get("text") or "")

st.divider()

# ------------------------------------------------------------------ list
st.markdown("### Deine Dokumente")
try:
    docs: list[dict[str, Any]] = api.list_documents()
    linked = {str(item["rag_document_id"]) for item in exam_api.listing() if item.get("rag_document_id")}
    docs = [doc for doc in docs if str(doc["id"]) not in linked]
except api.ApiError as exc:
    show_error(exc, api.HINT_CONNECTION)
    docs = []

if not docs:
    empty_state("Noch keine Dokumente. Lade oben deine erste Datei hoch – zum Beispiel eine Vokabelliste.")
else:
    h = st.columns([4, 3, 1, 2, 2, 2])
    for col, label in zip(h, ["Titel", "Schlagwörter", "", "Hochgeladen", "Status", ""], strict=True):
        col.markdown(f"**{label}**")
    for doc in docs:
        doc_id = str(doc.get("id"))
        cols = st.columns([4, 3, 1, 2, 2, 2])
        cols[0].write(doc.get("title") or doc.get("filename") or "–")
        with cols[1]:
            doc_tags = [TAG_LABELS.get(t, t) for t in (doc.get("tags") or [])]
            if doc_tags:
                chips(doc_tags)
            else:
                st.caption("–")

        cols[3].write(datetime_de(doc.get("created_at")))
        cols[4].markdown(_status_badge(doc.get("status")))
        with cols[5]:
            with st.popover("Bearbeiten"):
                current_tags = [TAG_LABELS.get(t, t) for t in (doc.get("tags") or []) if t in TAG_LABELS]
                new_tag_labels = st.multiselect(
                    "Schlagwörter", list(TAG_LABELS.values()), default=current_tags, key=f"doc_edit_tags_{doc_id}"
                )
                current_use = doc.get("use_in") or {}
                new_use = {
                    k: st.checkbox(label, value=bool(current_use.get(k, True)), key=f"doc_edit_use_{k}_{doc_id}")
                    for k, label in USE_IN_LABELS.items()
                }
                if st.button("Speichern", key=f"doc_save_{doc_id}", type="primary"):
                    try:
                        api.patch_document(
                            doc_id, tags=[TAG_BY_LABEL[label] for label in new_tag_labels], use_in=new_use
                        )
                        st.success("Gespeichert.")
                        st.rerun()
                    except api.ApiError as exc:
                        show_error(exc, "Bitte noch einmal versuchen.")
            if st.button("Löschen", key=f"doc_delete_{doc_id}"):
                try:
                    api.delete_document(doc_id)
                    st.rerun()
                except api.ApiError as exc:
                    show_error(exc, "Das Dokument konnte nicht gelöscht werden. Bitte noch einmal versuchen.")
        if doc.get("error"):
            st.caption(f"Fehler: {doc['error']}")
