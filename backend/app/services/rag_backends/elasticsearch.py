"""Elasticsearch adapter skeleton (ADR-0007: optional, ``RAG_BACKEND=es``, ``uv sync --extra es``).

Not part of the default stack. Mirrors ``rag.search``: owner scoping, tag + use_in filters,
lexical (german analyzer) and kNN candidates fused with the same pure-Python RRF.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.services.embeddings import get_embeddings
from app.services.rag import TOP_N, ChunkHit, rrf

INDEX = "lernapp_chunks"


class ElasticsearchBackend:
    def __init__(self, url: str | None = None) -> None:
        try:
            from elasticsearch import Elasticsearch
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "RAG_BACKEND=es, aber das Paket 'elasticsearch' ist nicht installiert (uv sync --extra es)."
            ) from exc
        self.url = url or get_settings().elasticsearch_url or "http://localhost:9200"
        self.client = Elasticsearch(self.url)

    def _filters(self, owner_id: str, tags: list[str] | None, use_in: str | None) -> list[dict[str, Any]]:
        f: list[dict[str, Any]] = [{"term": {"owner_id": owner_id}}]
        if tags:
            f.append({"terms": {"tags": tags}})
        if use_in:
            f.append({"term": {f"use_in.{use_in}": True}})
        return f

    def search(
        self, query: str, owner_id: str, tags: list[str] | None = None, k: int = 8, use_in: str | None = None
    ) -> list[ChunkHit]:
        filters = self._filters(owner_id, tags, use_in)
        lexical = self.client.search(
            index=INDEX,
            size=TOP_N,
            query={
                "bool": {
                    "must": [{"match": {"text": {"query": query, "analyzer": "german"}}}],
                    "filter": filters,
                }
            },
        )
        backend = get_embeddings()
        vec = backend.embed([query], session_id=f"rag-search:{owner_id}", learner_id=owner_id)[0]
        vector = self.client.search(
            index=INDEX,
            size=TOP_N,
            knn={
                "field": "embedding",
                "query_vector": vec,
                "k": TOP_N,
                "num_candidates": TOP_N * 5,
                "filter": filters + [{"term": {"embedding_model": backend.model}}],
            },
        )
        docs: dict[str, dict[str, Any]] = {}
        ranked: list[list[str]] = []
        for resp in (lexical, vector):
            ids: list[str] = []
            for h in resp["hits"]["hits"]:
                docs[h["_id"]] = h["_source"]
                ids.append(h["_id"])
            ranked.append(ids)
        hits: list[ChunkHit] = []
        for cid, score in rrf(ranked)[:k]:
            src = docs[cid]
            hits.append(
                ChunkHit(
                    chunk_id=cid,
                    document_id=str(src.get("document_id", "")),
                    document_title=str(src.get("document_title", "")),
                    ord=int(src.get("ord", 0)),
                    heading=src.get("heading"),
                    text=str(src.get("text", "")),
                    score=round(score, 6),
                )
            )
        return hits
