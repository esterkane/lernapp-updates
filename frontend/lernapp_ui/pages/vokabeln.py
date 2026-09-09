"""Vocabulary practice and explicit import are visible learning activities."""
from uuid import uuid4

import streamlit as st
from lernapp_ui import api

st.title("📚 Vokabeln")
st.caption("Wörter wiederholen oder eine Wortliste in Lernkarten umwandeln.")
try:
    notice = st.session_state.pop('vocab_import_notice', None)
    if notice:
        st.success(notice)
    stats = api.vocab_stats()
    st.write(f"{stats['total']} Lernkarten · {stats['due']} heute fällig")
    mode = st.radio("Was möchtest du machen?", ["Wiederholen", "Vokabeln hinzufügen"], horizontal=True)
    if mode == "Wiederholen":
        # Fresh due list after every review/import: no stale empty cache.
        cards = api.list_vocab(due_only=True, limit=1)
        if not cards:
            st.info("Heute sind keine Karten fällig." if stats['total'] else
                    "Noch keine Lernkarten. Hochgeladene Lesetexte werden nicht automatisch zu Vokabeln. Öffne „Vokabeln hinzufügen“, um eine CSV-Liste oder ein PDF-Glossar zu übernehmen.")
        else:
            card = cards[0]
            with st.container(border=True):
                st.subheader(card['wort'])
                if st.button("Bedeutung zeigen", key=f"flip_{card['id']}"):
                    st.session_state['vocab_revealed'] = card['id']
                if st.session_state.get('vocab_revealed') == card['id']:
                    st.write(card['bedeutung'])
                    if card.get('beispiel'):
                        st.caption(card['beispiel'])
                    columns = st.columns(3)
                    for column, label, quality in zip(columns, ['Wusste ich', 'Unsicher', 'Noch einmal üben'], [5, 3, 1], strict=True):
                        if column.button(label):
                            api.review_vocab(card['id'], quality)
                            st.session_state.pop('vocab_revealed', None)
                            st.rerun()
    else:
        st.write("CSV: Spalten **wort**, **bedeutung**, optional **beispiel**. PDF: Glossar mit fett gedruckten Begriffen und Doppelpunkt vor der Übersetzung.")
        upload = st.file_uploader("Vokabelliste auswählen", type=['csv', 'pdf'])
        if st.button("Wortpaare auslesen", disabled=upload is None):
            rows = api._request('POST', '/vocab/preview', files={'file': (upload.name, upload.getvalue(), upload.type)}, timeout=120)
            st.session_state['vocab_preview_id'] = uuid4().hex
            st.session_state['vocab_import_rows'] = rows
            st.session_state['vocab_import_name'] = upload.name
        rows = st.session_state.get('vocab_import_rows')
        if rows:
            st.caption(f"{len(rows)} Wortpaare aus {st.session_state['vocab_import_name']}. Prüfe die Zuordnung; du kannst Einträge ändern oder entfernen.")
            reverse = st.checkbox("Wort und Übersetzung tauschen", help="Bei einem Englisch–Deutsch-Glossar kannst du so Deutsch auf die Vorderseite legen.")
            editable = [{'wort': r['bedeutung'] if reverse else r['wort'],
                         'bedeutung': r['wort'] if reverse else r['bedeutung'],
                         'beispiel': r.get('beispiel') or ''} for r in rows]
            edited = st.data_editor(editable, num_rows='dynamic', hide_index=True,
                                    column_config={'wort': 'Wort (Vorderseite)', 'bedeutung': 'Bedeutung (Rückseite)', 'beispiel': 'Beispiel (optional)'},
                                    key=f"vocab_preview_{st.session_state.get('vocab_preview_id', 'preview')}_{reverse}")
            if st.button("Als Lernkarten übernehmen", type='primary'):
                imported = api._post('/vocab/import', {'items': edited})
                st.session_state['vocab_import_notice'] = f"{imported['added']} Lernkarten hinzugefügt; {imported['skipped']} bereits vorhandene Wortpaare ausgelassen."
                st.session_state.pop('vocab_cards', None)
                st.session_state.pop('vocab_import_rows', None)
                st.rerun()
        with st.expander("Einzelne Vokabel hinzufügen"), st.form('vocab_manual'):
            word = st.text_input('Wort')
            meaning = st.text_input('Bedeutung')
            if st.form_submit_button('Lernkarte speichern'):
                api._post('/vocab/import', {'items': [{'wort': word, 'bedeutung': meaning}]})
                st.success('Lernkarte gespeichert.')
except api.ApiError as exc:
    st.error(exc.message_de)
