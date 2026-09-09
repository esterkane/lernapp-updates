"""Conservative vocabulary import: explicit PDF glossary labels or CSV, never invented pairs."""
from __future__ import annotations

import ctypes
import io
import re
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select

from app.core.db import db_session
from app.db.base import Document, Learner, VocabItem
from app.services import vocab


def _clean(text: str) -> str:
    text = re.sub(r'[\x02\ufffe]\s*(?=und\b|oder\b)', '- ', text)
    text = re.sub(r'[\x02\ufffe]\s*', '', text)
    return ' '.join(text.split()).strip()


def preview(filename: str, data: bytes) -> list[dict[str, Any]]:
    if filename.lower().endswith('.csv'):
        return [{'wort': w, 'bedeutung': m, 'beispiel': e} for w, m, e in vocab.parse_csv(data)]
    if not filename.lower().endswith('.pdf') or not data.startswith(b'%PDF-'):
        raise ValueError('Bitte eine CSV-Vokabelliste oder ein PDF-Glossar auswählen.')
    import pypdfium2 as pdfium
    from pypdfium2 import raw

    from app.services.exam_pages import _render_lock

    result = []
    # PDFium is not thread safe; share the renderer's process-wide lock.
    with _render_lock, pdfium.PdfDocument(io.BytesIO(data)) as document:
        if len(document) > 150:
            raise ValueError('Bitte höchstens 150 PDF-Seiten importieren.')
        for number in range(len(document)):
            page = document[number]
            text = page.get_textpage()
            word = meaning = ''
            in_meaning = False
            def finish(number: int = number) -> None:
                nonlocal word, meaning, in_meaning
                left = _clean(word)
                right = _clean(re.sub(r'(?:\r?\n)[A-Z](?:,\s*[A-Z])?\s*$', '', meaning))
                if left and right and len(left) <= 255 and len(right) <= 2000:
                    result.append({'wort': left, 'bedeutung': right, 'beispiel': None, 'page': number + 1})
                word = meaning = ''
                in_meaning = False
            try:
                if text.count_chars() > 100000:
                    raise ValueError('Diese PDF-Seite enthält zu viel Text. Bitte eine kleinere Datei verwenden.')
                for index in range(text.count_chars()):
                    char = text.get_text_range(index, 1)
                    box = text.get_charbox(index)
                    # Exclude repeated margin furniture. Large alphabet headings are not definitions.
                    if box[1] < 45 or box[3] > page.get_height() - 45:
                        continue
                    if box[3] - box[1] > 12:
                        finish()
                        continue
                    font = ctypes.create_string_buffer(200)
                    flags = ctypes.c_int()
                    raw.FPDFText_GetFontInfo(text.raw, index, font, 200, ctypes.byref(flags))
                    if b'Bold' in font.value or b'bold' in font.value:
                        if in_meaning:
                            finish()
                        word += char
                    elif char == ':' and word.strip():
                        in_meaning = True
                    elif in_meaning:
                        meaning += char
                    elif char.isspace():
                        word += char
                    elif word.strip():
                        word = ''
                finish()
            finally:
                text.close()
                page.close()
    if not result:
        raise ValueError('Keine eindeutigen Wortpaare erkannt. Unterstützt werden PDF-Glossare mit fett gedrucktem Begriff und Doppelpunkt. Für andere PDFs bitte Wortpaare als CSV bereitstellen.')
    return result


def import_items(owner: str, items: list[dict[str, Any]], source_document_id: str | None = None) -> dict[str, int]:
    """Serialize imports per owner; existing cards and review schedules remain untouched."""
    added = skipped = 0
    def identity(word: str, meaning: str) -> tuple[str, str]:
        return (' '.join(word.split()).casefold(), ' '.join(meaning.split()).casefold())
    with db_session() as db:
        learner = db.execute(select(Learner).where(Learner.id == owner).with_for_update()).scalar_one_or_none()
        if learner is None:
            raise HTTPException(404, 'Lernbereich nicht gefunden.')
        if source_document_id:
            source = db.execute(select(Document.id).where(Document.id == source_document_id, Document.owner_id == owner)).scalar_one_or_none()
            if source is None:
                raise HTTPException(404, 'Quelldokument nicht gefunden.')
        existing = {identity(w, m) for w, m in db.execute(select(VocabItem.wort, VocabItem.bedeutung).where(VocabItem.owner_id == owner))}
        for item in items:
            word, meaning = item['wort'].strip(), item['bedeutung'].strip()
            if not word or not meaning or len(word) > 255 or len(meaning) > 2000:
                raise ValueError('Jede Karte benötigt einen Begriff (höchstens 255 Zeichen) und eine Bedeutung (höchstens 2000 Zeichen).')
            key = identity(word, meaning)
            if key in existing:
                skipped += 1
                continue
            db.add(VocabItem(owner_id=owner, wort=word, bedeutung=meaning,
                             beispiel=item.get('beispiel'), source_document_id=source_document_id))
            existing.add(key)
            added += 1
    return {'added': added, 'skipped': skipped}
