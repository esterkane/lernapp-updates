"""Original-page rendering with a bounded cache; authorize every request before lookup."""
import hashlib
import io
from collections import OrderedDict
from threading import Lock

from fastapi import HTTPException
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import JSONPATH

from app.core.db import db_session
from app.db.exams import Exam, ExamAttempt

_render_lock = Lock()
_cache: OrderedDict[tuple[str, str, bytes, int, int], bytes] = OrderedDict()
_CACHE_BYTES = 32 * 1024 * 1024
_cache_bytes = 0


def render(identifier: str, owner: str, page: int, attempt_id: str | None = None) -> bytes:
    global _cache_bytes
    with db_session() as db:
        if attempt_id:
            attempt = db.execute(select(
                ExamAttempt.exam_id,
                func.jsonb_path_query_array(ExamAttempt.snapshot, cast('$.questions[*].source_pages[*]', JSONPATH)),
            ).where(ExamAttempt.id == attempt_id, ExamAttempt.owner_id == owner)).one_or_none()
            if attempt is None or attempt[0] != identifier or page not in attempt[1]:
                raise HTTPException(404, "Diese Quellseite gehört nicht zum Versuch.")
        row = db.execute(select(Exam.pdf, Exam.payload["page_rotation"]).where(
            Exam.id == identifier, Exam.owner_id == owner,
        )).one_or_none()
        if row is None:
            raise HTTPException(404, "Modelltest in diesem Lernbereich nicht gefunden.")
        data, rotation = row
    if rotation not in (0, 90, 180, 270):
        rotation = 0
    key = (owner, identifier, hashlib.sha256(data).digest(), page, rotation)
    import pypdfium2 as pdfium

    with _render_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]
        with pdfium.PdfDocument(data) as document:
            if not 1 <= page <= len(document):
                raise HTTPException(404, "PDF-Seite existiert nicht.")
            pdf_page = document[page - 1]
            try:
                bitmap = pdf_page.render(scale=1.5, rotation=rotation)
                try:
                    buffer = io.BytesIO()
                    bitmap.to_pil().save(buffer, format="PNG")
                    image = buffer.getvalue()
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        if len(image) <= _CACHE_BYTES:
            while _cache and _cache_bytes + len(image) > _CACHE_BYTES:
                _cache_bytes -= len(_cache.popitem(last=False)[1])
            _cache[key] = image
            _cache_bytes += len(image)
        return image
