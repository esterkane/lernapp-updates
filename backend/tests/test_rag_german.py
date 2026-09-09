"""ADR-0007/0008 quality gates: umlauts/ß, hyphenation repair, sentence-safe chunks, owner isolation,
tag + use_in filters, RRF determinism, hybrid search API, delete cascade."""

from __future__ import annotations

import io
import re

import pytest
from app.core.db import db_session
from app.db.base import Chunk, Document, Learner, VocabItem
from app.services import rag
from sqlalchemy import func, select

from tests.conftest import ledger_count

GERMAN_MD = """# Preisverhandlung mit dem Lieferanten

Die Preisverhandlung begann am Montag. Frau Müller erklärte, dass die Straße zur Fabrik
gesperrt sei. Der Geschäftsführer schlug eine Paketlösung vor, die größtenteils akzeptiert wurde.

## Zahlungsbedingungen

Die Zahlungsziele wurden auf 30 Tage verkürzt. Ein Skonto von 2 % bei Zahlung innerhalb von
zehn Tagen ist möglich, z. B. bei Vorkasse. Herr Dr. Schäfer bestätigte dies per E-Mail.
"""

HYPHEN_TXT = "Die Ver-\nhandlung über die Straße begann. Die E-\nMail kam später an, größtenteils.\n"


def minimal_pdf(lines: list[str]) -> bytes:
    """A hand-built one-page PDF (Helvetica, WinAnsi) — enough for pypdf to extract umlauts and line breaks."""

    def esc(s: str) -> bytes:
        return s.encode("cp1252").replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)")

    ops = [b"BT /F1 12 Tf 50 750 Td 14 TL"] + [b"(" + esc(ln) + b") Tj T*" for ln in lines] + [b"ET"]
    stream = b"\n".join(ops)
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + o + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode() + b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def long_german_text(n_sections: int = 3, sentences_per_section: int = 40) -> str:
    parts = []
    for s in range(n_sections):
        parts.append(f"# Abschnitt {s + 1}: Verhandlungsführung\n")
        for i in range(sentences_per_section):
            parts.append(
                f"Satz {s}-{i}: Die Verhandlungspartnerin betonte, dass die Lieferfrist für die Maschinenteile "
                f"unbedingt eingehalten werden müsse, weil sonst Vertragsstrafen drohen würden."
            )
            if i % 7 == 6:
                parts.append("")  # paragraph break
    return "\n".join(parts)


@pytest.fixture()
def other_learner() -> str:
    with db_session() as db:
        if db.get(Learner, "other") is None:
            db.add(Learner(id="other", display_name="Andere", level="B2"))
    return "other"


@pytest.fixture(autouse=True)
def _clean_documents(learner_id: str) -> None:
    with db_session() as db:
        for d in db.execute(select(Document)).scalars():
            db.delete(d)
        db.execute(VocabItem.__table__.delete())


# ---------------------------------------------------------------- pure text processing


def test_hyphenation_repair_keeps_real_hyphens() -> None:
    out = rag.repair_hyphenation(HYPHEN_TXT)
    assert "Verhandlung" in out
    assert "Ver-" not in out
    assert "E-Mail" in out


def test_umlauts_survive_extraction_txt_docx_pdf() -> None:
    txt = rag.extract_text("a.txt", "Straße, Müller, Übung, Ärger, größtenteils".encode("utf-8-sig"))
    assert "Straße" in txt and "Müller" in txt and "Übung" in txt and "Ärger" in txt

    import docx

    d = docx.Document()
    d.add_heading("Zahlungsbedingungen", level=1)
    d.add_paragraph("Die Zahlungsziele wurden verkürzt. Größtenteils akzeptiert.")
    buf = io.BytesIO()
    d.save(buf)
    docx_text = rag.extract_text("b.docx", buf.getvalue())
    assert "# Zahlungsbedingungen" in docx_text and "verkürzt" in docx_text and "Größtenteils" in docx_text

    pdf_text = rag.normalize_text(rag.extract_text("c.pdf", minimal_pdf(HYPHEN_TXT.split("\n"))))
    assert "Verhandlung über die Straße" in pdf_text
    assert "E-Mail" in pdf_text and "größtenteils" in pdf_text


def test_sentence_split_guards_abbreviations() -> None:
    s = rag.split_sentences("Das gilt z. B. bei Vorkasse. Herr Dr. Schäfer bestätigte dies. Danke!")
    assert s == ["Das gilt z. B. bei Vorkasse.", "Herr Dr. Schäfer bestätigte dies.", "Danke!"]


def test_headings_are_kept_and_structured() -> None:
    sections = rag.structure(GERMAN_MD)
    headings = [h for h, _ in sections]
    assert headings == ["Preisverhandlung mit dem Lieferanten", "Zahlungsbedingungen"]
    assert rag.is_heading("EINLEITUNG", prev_blank=False)
    assert rag.is_heading("Wichtige Redemittel", prev_blank=True)
    assert not rag.is_heading("Wichtige Redemittel folgen hier.", prev_blank=True)
    assert not rag.is_heading("- ein Aufzählungspunkt", prev_blank=True)


def test_chunks_never_split_sentences_and_overlap() -> None:
    text = long_german_text()
    drafts = rag.chunk_text(text)
    assert len(drafts) >= 4
    for d in drafts:
        body = d.text.split("\n", 1)[1] if d.heading else d.text
        assert body.rstrip()[-1] in ".!?", body[-80:]
        assert len(d.text) <= rag.CHUNK_CHARS + 20
        assert d.heading and d.heading.startswith("Abschnitt")
    assert [d.ord for d in drafts] == list(range(len(drafts)))
    # every source sentence appears whole in at least one chunk
    joined = "\n".join(d.text for d in drafts)
    for sentence in re.findall(r"Satz \d+-\d+:[^.]+\.", text):
        assert sentence in joined
    # consecutive chunks of the same section share their overlap sentence(s)
    for a, b in zip(drafts, drafts[1:], strict=False):
        if a.heading == b.heading:
            last = rag.split_sentences(a.text.split("\n", 1)[1].replace("\n\n", " "))[-1]
            assert last in b.text


# ---------------------------------------------------------------- RRF


def test_rrf_correct_and_deterministic() -> None:
    lists = [["a", "b", "c"], ["c", "a", "d"]]
    fused = rag.rrf(lists, k=60)
    ids = [i for i, _ in fused]
    assert ids[0] == "a"  # ranks 1 + 2
    assert ids[1] == "c"  # ranks 3 + 1
    assert set(ids[2:]) == {"b", "d"}
    assert ids[2] == "b"  # both 1/62 → tie broken by id
    assert fused == rag.rrf(lists, k=60) == rag.rrf([list(x) for x in lists])
    assert abs(fused[0][1] - (1 / 61 + 1 / 62)) < 1e-12
    assert rag.rrf([]) == []
    assert rag.rrf([["x"], []]) == [("x", 1 / 61)]


# ---------------------------------------------------------------- ingestion + retrieval (DB)


def test_ingest_txt_indexes_and_meters(learner_id: str) -> None:
    before = ledger_count()
    res = rag.ingest(learner_id, "verhandlung.txt", long_german_text().encode(), tags="verhandlung", use_in="tutor")
    assert res["status"] == "indexiert" and res["n_chunks"] >= 4 and res["n_vocab_items"] == 0
    assert ledger_count() > before  # embeddings were metered (product rule 8)
    with db_session() as db:
        doc = db.get(Document, res["document_id"])
        assert doc is not None and doc.status == "indexiert" and doc.n_chunks == res["n_chunks"]
        assert doc.use_in == {"uebungen": False, "rollenspiel": False, "tutor": True}
        chunks = db.execute(select(Chunk).where(Chunk.document_id == doc.id).order_by(Chunk.ord)).scalars().all()
        assert len(chunks) == res["n_chunks"]
        assert all(c.embedding_model == "fake/embedding-64" and c.owner_id == learner_id for c in chunks)
        assert all(c.heading for c in chunks)


def test_unsupported_and_empty_files(learner_id: str, client) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ValueError):
        rag.ingest(learner_id, "bild.png", b"\x89PNG", tags=None, use_in=None)
    r = client.post(
        "/documents", files={"file": ("bild.png", b"\x89PNG", "image/png")}, data={"learner_id": learner_id}
    )
    assert r.status_code == 415
    res = rag.ingest(learner_id, "leer.txt", b"   \n  ", tags=None, use_in=None)
    assert res["status"] == "fehler" and res["error"]
    docs = rag.list_documents(learner_id)
    assert any(d["status"] == "fehler" and d["error"] for d in docs)


def test_search_api_german_compound_word(learner_id: str, client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/documents",
        files={"file": ("preisverhandlung.md", GERMAN_MD.encode(), "text/markdown")},
        data={"learner_id": learner_id, "tags": "verhandlung, testdaf", "use_in": "tutor,rollenspiel"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "indexiert" and body["n_chunks"] >= 1 and "cost_eur" in body
    assert body["title"] == "preisverhandlung"

    docs = client.get("/documents", params={"learner_id": learner_id}).json()
    assert [d["id"] for d in docs] == [body["document_id"]]
    assert docs[0]["tags"] == ["verhandlung", "testdaf"]
    assert docs[0]["use_in"] == {"uebungen": False, "rollenspiel": True, "tutor": True}

    r = client.get("/documents/search", params={"q": "Preisverhandlung", "learner_id": learner_id})
    hits = r.json()
    assert r.status_code == 200 and hits
    assert {"chunk_id", "document_id", "document_title", "ord", "heading", "text", "score"} <= set(hits[0])
    assert hits[0]["document_id"] == body["document_id"] and "Preisverhandlung" in hits[0]["text"]
    # inflected / stemmed form and umlaut word also hit the lexical path ('german' dictionary)
    assert client.get("/documents/search", params={"q": "Zahlungsziel", "learner_id": learner_id}).json()
    assert client.get("/documents/search", params={"q": "verkürzt Skonto", "learner_id": learner_id}).json()
    assert client.get("/documents/search", params={"q": "", "learner_id": learner_id}).json() == []


def test_owner_isolation(learner_id: str, other_learner: str, client) -> None:  # type: ignore[no-untyped-def]
    rag.ingest(learner_id, "geheim.md", GERMAN_MD.encode(), tags="verhandlung", use_in=None)
    assert rag.search("Preisverhandlung", learner_id)
    assert rag.search("Preisverhandlung", other_learner) == []
    assert rag.context_for(other_learner, "Preisverhandlung") == []
    assert client.get("/documents", params={"learner_id": other_learner}).json() == []
    assert client.get("/documents/search", params={"q": "Preisverhandlung", "learner_id": other_learner}).json() == []
    doc_id = rag.list_documents(learner_id)[0]["id"]
    assert rag.delete_document(doc_id, owner_id=other_learner) is False
    assert client.delete(f"/documents/{doc_id}", params={"learner_id": other_learner}).status_code == 404
    assert (
        client.patch(f"/documents/{doc_id}", json={"title": "x"}, params={"learner_id": other_learner}).status_code
        == 404
    )


def test_tag_and_use_in_filters(learner_id: str) -> None:
    a = rag.ingest(
        learner_id,
        "a.txt",
        b"Die Preisverhandlung war hart. Wir verhandelten lange.",
        tags="verhandlung",
        use_in="rollenspiel",
    )
    b = rag.ingest(
        learner_id,
        "b.txt",
        b"Die Preisverhandlung im TestDaF-Text war fiktiv. Es ging um Studium.",
        tags="testdaf",
        use_in="uebungen,tutor",
    )
    all_hits = rag.search("Preisverhandlung", learner_id)
    assert {h.document_id for h in all_hits} == {a["document_id"], b["document_id"]}
    assert {h.document_id for h in rag.search("Preisverhandlung", learner_id, tags=["testdaf"])} == {b["document_id"]}
    assert {h.document_id for h in rag.search("Preisverhandlung", learner_id, tags="verhandlung,sonstiges")} == {
        a["document_id"]
    }
    assert rag.search("Preisverhandlung", learner_id, tags=["sonstiges"]) == []
    assert {h.document_id for h in rag.search("Preisverhandlung", learner_id, use_in="tutor")} == {b["document_id"]}
    assert {h.document_id for h in rag.search("Preisverhandlung", learner_id, use_in="rollenspiel")} == {
        a["document_id"]
    }
    assert {h.document_id for h in rag.context_for(learner_id, "Preisverhandlung", use_in="uebungen")} == {
        b["document_id"]
    }
    with pytest.raises(ValueError):
        rag.search("x", learner_id, use_in="bogus")
    # update toggles are respected by the next search
    rag.update_document(a["document_id"], use_in={"tutor": True}, tags=["testdaf"], title="Neu")
    ids = {h.document_id for h in rag.search("Preisverhandlung", learner_id, use_in="tutor", tags=["testdaf"])}
    assert ids == {a["document_id"], b["document_id"]}
    assert rag.get_document(a["document_id"])["title"] == "Neu"  # type: ignore[index]


def test_format_context_and_context_for(learner_id: str) -> None:
    rag.ingest(learner_id, "k.md", GERMAN_MD.encode(), tags=None, use_in="tutor")
    hits = rag.context_for(learner_id, "Skonto Zahlungsziele", use_in="tutor", k=2)
    assert hits and len(hits) <= 2
    ctx = rag.format_context(hits)
    assert ctx.startswith(f"[Dok: {hits[0].document_title} §{hits[0].ord}] ")
    assert rag.format_context([]) == "—"


def test_vector_path_ignores_other_embedding_models(learner_id: str) -> None:
    res = rag.ingest(learner_id, "v.txt", b"Die Lieferfrist wurde verschoben. Das war teuer.", tags=None, use_in=None)
    with db_session() as db:
        # a stale chunk from an old 3-dim model must not break the cosine query
        db.add(
            Chunk(
                document_id=res["document_id"],
                owner_id=learner_id,
                ord=99,
                heading=None,
                text="Altes Modell.",
                embedding=[0.1, 0.2, 0.3],
                embedding_model="old/embedding-3",
            )
        )
    hits = rag.search("Lieferfrist", learner_id)
    assert hits and all(h.ord != 99 or "Altes" in h.text for h in hits)
    assert hits[0].ord == 0


def test_delete_cascades_chunks_and_vocab(learner_id: str, client) -> None:  # type: ignore[no-untyped-def]
    doc = rag.ingest(learner_id, "d.md", GERMAN_MD.encode(), tags=None, use_in=None)
    csv_doc = rag.ingest(
        learner_id, "vokabeln.csv", b"wort;bedeutung\nverhandeln;negotiate\n", tags="vokabeln", use_in=None
    )
    assert csv_doc["n_vocab_items"] == 1 and csv_doc["status"] == "indexiert"
    with db_session() as db:
        assert db.execute(select(func.count()).where(Chunk.document_id == doc["document_id"])).scalar_one() > 0
        assert (
            db.execute(select(func.count()).where(VocabItem.source_document_id == csv_doc["document_id"])).scalar_one()
            == 1
        )
    assert client.delete(f"/documents/{doc['document_id']}").json() == {"deleted": True}
    assert client.delete(f"/documents/{csv_doc['document_id']}").json() == {"deleted": True}
    assert client.delete(f"/documents/{doc['document_id']}").status_code == 404
    with db_session() as db:
        assert db.execute(select(func.count()).where(Chunk.document_id == doc["document_id"])).scalar_one() == 0
        assert (
            db.execute(select(func.count()).where(VocabItem.source_document_id == csv_doc["document_id"])).scalar_one()
            == 0
        )
    assert rag.list_documents(learner_id) == []
