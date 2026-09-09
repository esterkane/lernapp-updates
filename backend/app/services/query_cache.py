"""Bounded, process-local query vectors. Document search results are never cached."""

from collections import OrderedDict
from hashlib import sha256
from threading import Lock
from time import monotonic

from app.services.embeddings import EmbeddingBackend

MAX_ENTRIES = 256
TTL_SECONDS = 900.0
_lock = Lock()
_cache: OrderedDict[tuple[str, str, str, str], tuple[float, tuple[float, ...]]] = OrderedDict()


def clear() -> None:
    with _lock:
        _cache.clear()


def query_vector(backend: EmbeddingBackend, query: str, *, owner_id: str, session_id: str) -> list[float]:
    # Exact query hashes avoid retaining learner text or silently changing inputs.
    key = (
        owner_id,
        type(getattr(backend, "inner", backend)).__qualname__,
        backend.model,
        sha256(query.encode("utf-8")).hexdigest(),
    )
    with _lock:
        now = monotonic()
        for stale in [k for k, (expires, _) in _cache.items() if expires <= now]:
            del _cache[stale]
        cached = _cache.get(key)
        if cached is not None:
            _cache.move_to_end(key)
            return list(cached[1])
    # Keep network calls outside the lock; unrelated searches must not wait.
    # Failures are never cached. Only real provider calls enter the cost ledger.
    vector = backend.embed([query], session_id=session_id, learner_id=owner_id)[0]
    with _lock:
        _cache[key] = (monotonic() + TTL_SECONDS, tuple(vector))
        _cache.move_to_end(key)
        while len(_cache) > MAX_ENTRIES:
            _cache.popitem(last=False)
    return list(vector)
