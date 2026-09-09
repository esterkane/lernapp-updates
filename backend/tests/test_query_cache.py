import pytest
from app.services import query_cache, rag


@pytest.fixture(autouse=True)
def empty_cache():
    query_cache.clear()
    yield
    query_cache.clear()


class Backend:
    model = "test/model"

    def __init__(self):
        self.calls = []

    def embed(self, texts, **kwargs):
        self.calls.append((texts, kwargs))
        return [[1.0, 2.0]]


def test_reuse_is_scoped_and_returns_independent_vectors():
    backend = Backend()

    def get(owner="a", query="Frage"):
        return query_cache.query_vector(backend, query, owner_id=owner, session_id="session")

    get()[0] = 99
    assert get() == [1, 2]
    assert len(backend.calls) == 1
    get(owner="b")
    get(query="Andere Frage")
    backend.model = "test/other-model"
    get()
    assert len(backend.calls) == 4


def test_expiration_and_lru_bound(monkeypatch):
    backend = Backend()
    clock = [0.0]
    monkeypatch.setattr(query_cache, "monotonic", lambda: clock[0])
    monkeypatch.setattr(query_cache, "MAX_ENTRIES", 2)

    def get(q):
        return query_cache.query_vector(backend, q, owner_id="a", session_id="s")

    get("one")
    get("two")
    get("one")
    get("three")
    get("two")
    assert len(backend.calls) == 4
    clock[0] = query_cache.TTL_SECONDS + 1
    get("two")
    assert len(backend.calls) == 5
    assert len(query_cache._cache) == 1


def test_failures_are_retried(monkeypatch):
    backend = Backend()
    original = backend.embed

    def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(backend, "embed", fail)
    with pytest.raises(RuntimeError):
        query_cache.query_vector(backend, "q", owner_id="a", session_id="s")
    monkeypatch.setattr(backend, "embed", original)
    assert query_cache.query_vector(backend, "q", owner_id="a", session_id="s") == [1, 2]
    assert len(backend.calls) == 1


def test_cached_embedding_keeps_documents_fresh_and_avoids_extra_charge(client):
    from tests.conftest import ledger_count

    owner = client.post("/workspaces", json={"name": "Cache test"}).json()["id"]
    doc = rag.ingest(owner, "rechnung.txt", b"Die Rechnung ist offen. Bitte bezahlen.")
    first = {}
    second = {}
    before = ledger_count()
    hits = rag.search("Rechnung", owner, timings=first)
    assert hits
    after = ledger_count()
    assert after > before
    rag.update_document(doc["document_id"], title="Updated", use_in={"tutor": False})
    assert rag.search("Rechnung", owner, timings=second)[0].document_title == "Updated"
    assert rag.search("Rechnung", owner, use_in="tutor") == []
    assert ledger_count() == after
    rag.delete_document(doc["document_id"], owner_id=owner)
    assert rag.search("Rechnung", owner) == []
    assert ledger_count() == after
    assert all(t[key] >= 0 for t in (first, second) for key in ("query_embedding", "retrieval_database"))


def test_empty_and_filtered_collections_skip_embeddings(client, monkeypatch):
    owner = client.post("/workspaces", json={"name": "Empty retrieval"}).json()["id"]
    other = client.post("/workspaces", json={"name": "Other retrieval"}).json()["id"]
    doc = rag.ingest(other, "other.txt", b"Die Rechnung ist offen.")

    def unexpected():
        raise AssertionError("No embedding backend should be needed")

    monkeypatch.setattr(rag, "get_embeddings", unexpected)
    times = {}
    assert rag.search("Rechnung", owner, timings=times) == []
    assert times["query_embedding"] == 0
    assert rag.search("Rechnung", other, tags=["missing"]) == []
    rag.update_document(doc["document_id"], use_in={"tutor": False})
    assert rag.search("Rechnung", other, use_in="tutor") == []


def test_lexical_search_overlaps_embedding_and_preserves_context(client, monkeypatch):
    from threading import Event

    from app.core.db import get_engine
    from app.core.workspaces import current_workspace, selected_workspace
    from sqlalchemy import event

    owner = client.post("/workspaces", json={"name": "Parallel retrieval"}).json()["id"]
    doc = rag.ingest(owner, "rechnung.txt", b"Die Rechnung ist offen. Bitte bezahlen.")
    lexical_started = Event()
    original = rag.query_vector
    observed = []

    def observe_sql(conn, cursor, statement, parameters, context, executemany):
        if "ts_rank_cd" in statement:
            lexical_started.set()

    def embedding(*args, **kwargs):
        observed.append(current_workspace())
        assert lexical_started.wait(5), "Keyword search must run before embedding completes"
        return original(*args, **kwargs)

    monkeypatch.setattr(rag, "query_vector", embedding)
    engine = get_engine()
    event.listen(engine, "before_cursor_execute", observe_sql)
    token = selected_workspace.set(owner)
    try:
        times = {}
        hits = rag.search("Rechnung", owner, timings=times)
    finally:
        selected_workspace.reset(token)
        event.remove(engine, "before_cursor_execute", observe_sql)
    assert [h.document_id for h in hits] == [doc["document_id"]]
    assert observed == [owner]
    assert times["query_embedding"] >= 0 and times["retrieval_database"] >= 0
