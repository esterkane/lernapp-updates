import io

from app.core.db import db_session
from app.db.base import VocabItem
from app.services.vocab_import import preview
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
from sqlalchemy import select


def test_pdf_explicit_pairs_keep_columns_and_wrapped_meanings():
    buffer = io.BytesIO()
    pdf = PdfWriter()
    page = pdf.add_blank_page(595, 842)
    fonts = DictionaryObject()
    for name, font in [('F1', 'Helvetica-Bold'), ('F2', 'Helvetica')]:
        fonts[NameObject('/' + name)] = pdf._add_object(DictionaryObject({
            NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'),
            NameObject('/BaseFont'): NameObject('/' + font),
        }))
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): fonts})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 10 Tf 60 700 Td (Invoice) Tj /F2 10 Tf (: Rechnung) Tj ET '
                    b'BT /F1 10 Tf 320 700 Td (Payment) Tj /F2 10 Tf (: Zahlung) Tj ET')
    page[NameObject('/Contents')] = pdf._add_object(stream)
    pdf.write(buffer)
    pairs = preview('glossary.pdf', buffer.getvalue())
    assert [(p['wort'], p['bedeutung']) for p in pairs] == [('Invoice', 'Rechnung'), ('Payment', 'Zahlung')]


def test_import_deduplicates_preserves_review_and_is_workspace_scoped(client):
    owner = client.post('/workspaces', json={'name': 'Cards'}).json()['id']
    headers = {'X-Lernapp-Workspace': owner}
    body = {'items': [{'wort': 'Rechnung', 'bedeutung': 'invoice'}, {'wort': ' Rechnung ', 'bedeutung': 'Invoice'}]}
    assert client.post('/vocab/import', headers=headers, json=body).json() == {'added': 1, 'skipped': 1}
    item = client.get('/vocab', headers=headers).json()[0]
    client.post(f"/vocab/{item['id']}/review", headers=headers, json={'quality': 5})
    assert client.post('/vocab/import', headers=headers, json=body).json() == {'added': 0, 'skipped': 2}
    with db_session() as db:
        card = db.execute(select(VocabItem).where(VocabItem.id == item['id'])).scalar_one()
        assert card.repetitions == 1
    assert client.post('/vocab/import', json=body).json()['added'] == 1
    assert client.post('/vocab/import', headers=headers, json={**body, 'source_document_id': 'missing'}).status_code == 404


def test_csv_preview_and_invalid_import_are_safe(client):
    response = client.post('/vocab/preview', files={'file': ('words.csv', b'wort,bedeutung\nRechnung,invoice', 'text/csv')})
    assert response.status_code == 200
    assert response.json()[0]['wort'] == 'Rechnung'
    assert client.post('/vocab/import', json={'items': [{'wort': ' ', 'bedeutung': 'invoice'}]}).status_code == 400
