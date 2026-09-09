"""Optional retrieval backends (ADR-0007). Default is Postgres in ``app.services.rag``; ``RAG_BACKEND=es``
switches ``rag.search`` to the Elasticsearch adapter, which mirrors the same ``search`` signature."""

from __future__ import annotations

from typing import Protocol

from app.core.config import get_settings
from app.services.rag import ChunkHit


class SearchBackend(Protocol):
    def search(
        self, query: str, owner_id: str, tags: list[str] | None = None, k: int = 8, use_in: str | None = None
    ) -> list[ChunkHit]: ...


def get_backend() -> SearchBackend:
    if get_settings().rag_backend == "es":
        from app.services.rag_backends.elasticsearch import ElasticsearchBackend

        return ElasticsearchBackend()
    raise RuntimeError("RAG_BACKEND=pg uses app.services.rag directly; no adapter needed")
