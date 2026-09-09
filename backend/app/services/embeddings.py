"""Embedding adapter (ADR-0007/0008): batched, metered as tokens_in; ``get_embeddings()`` is hook-wrapped (ADR-0016)."""

from __future__ import annotations

import logging
from typing import Protocol

from app.core import credentials
from app.core.config import get_settings
from app.core.hooks import HookDenied, SessionState, TierCall, engine
from app.core.ledger import meter
from app.core.models import resolve_tier, vendor_of
from app.core.usage import extract_llm_usage
from app.services.fakes import fake_embedding

log = logging.getLogger(__name__)
BATCH = 64


class EmbeddingBackend(Protocol):
    model: str

    def embed(self, texts: list[str], *, session_id: str | None, learner_id: str | None) -> list[list[float]]: ...


class LiteLLMEmbeddings:
    def __init__(self) -> None:
        self.model = resolve_tier("embedding").model

    def embed(self, texts: list[str], *, session_id: str | None, learner_id: str | None) -> list[list[float]]:
        import litellm

        out: list[list[float]] = []
        vendor = vendor_of(self.model)
        api_key = credentials.require_workspace_key(learner_id, vendor)
        for i in range(0, len(texts), BATCH):
            batch = texts[i : i + BATCH]
            with meter(
                "embed",
                vendor,
                self.model,
                "tokens_in",
                session_id=session_id,
                learner_id=learner_id,
                meta={"n_texts": len(batch), "operation": "embedding"},
            ) as m:
                resp = litellm.embedding(model=self.model, input=batch, api_key=api_key)
                u = extract_llm_usage(resp)
                tokens = u.get("input_tokens") if u.get("input_tokens") is not None else u.get("total_tokens")
                if u.get("request_id"):
                    m.meta["request_id"] = u["request_id"]
                if u.get("raw_usage"):
                    m.meta["raw_usage"] = u["raw_usage"]
                if u.get("total_tokens"):
                    m.meta["total_tokens"] = u["total_tokens"]
                if tokens is not None:
                    m.meta["usage_source"] = "provider"
                else:  # Missing usage must not be invented from learner text.
                    m.meta.update({"usage_source": "unavailable", "usage_unavailable": True})
                m.quantity = tokens if tokens is not None else 0
                out.extend([d["embedding"] for d in resp.data])
        return out


class FakeEmbeddings:
    model = "fake/embedding-64"

    def embed(self, texts: list[str], *, session_id: str | None, learner_id: str | None) -> list[list[float]]:
        real_model = resolve_tier("embedding").model
        with meter(
            "embed",
            vendor_of(real_model),
            real_model,
            "tokens_in",
            session_id=session_id,
            learner_id=learner_id,
            local=True,
            meta={"fake": True},
        ) as m:
            m.quantity = sum(len(t) // 4 for t in texts)
            return [fake_embedding(t) for t in texts]


class HookedEmbeddings:
    """Backend proxy: ``embed`` runs through ``engine.execute`` (kind ``embed``)."""

    def __init__(self, inner: EmbeddingBackend) -> None:
        self.inner = inner
        self.model = inner.model

    def embed(
        self, texts: list[str], *, session_id: str | None, learner_id: str | None, state: SessionState | None = None
    ) -> list[list[float]]:
        call = TierCall(
            kind="embed",
            tier="embedding",
            model=self.inner.model,
            session_id=session_id,
            learner_id=learner_id,
            payload_summary={"n_texts": len(texts)},
        )
        res = engine.execute(
            call,
            state or SessionState(),
            lambda _c: self.inner.embed(texts, session_id=session_id, learner_id=learner_id),
        )
        if not res.success:
            raise HookDenied(res)
        return res.result  # type: ignore[no-any-return]


def _raw_embeddings() -> EmbeddingBackend:
    if get_settings().embedding_backend == "fake":
        return FakeEmbeddings()
    return LiteLLMEmbeddings()


def get_embeddings() -> EmbeddingBackend:
    return HookedEmbeddings(_raw_embeddings())


def embed(
    texts: list[str], *, session_id: str | None, learner_id: str | None, state: SessionState | None = None
) -> list[list[float]]:
    return HookedEmbeddings(_raw_embeddings()).embed(texts, session_id=session_id, learner_id=learner_id, state=state)
