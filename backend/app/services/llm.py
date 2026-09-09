"""LLM access (ADR-0003, ADR-0016): every completion goes through ``complete`` → HookEngine → LiteLLM → ledger.

``complete``/``stream`` build a ``TierCall`` and hand the provider call to ``engine.execute`` as
``dispatch``; a pre-hook deny raises ``HookDenied`` and the provider is provably never called
(no ledger row either — there was no call). Post-hooks normalize the parsed result before it is
returned. ``stream`` collects the upstream token stream inside ``dispatch`` so the post-hooks see
the complete text; chunks are yielded afterwards (no caller needs incremental tokens today).
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, TypeVar, cast, overload

from pydantic import BaseModel, ValidationError

from app.core import credentials
from app.core.config import get_settings
from app.core.hooks import HookDenied, SessionState, TierCall, engine
from app.core.ledger import record_llm_usage
from app.core.models import resolve_tier, vendor_of
from app.core.usage import Service, extract_llm_usage
from app.services.fakes import fake_completion, usage_for

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

TIMEOUTS = {"conversation": 30, "assessment": 90, "validator": 90, "generator": 90, "eu_conversation": 30}
RETRY_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    status: int | None = None
    model: str | None = None


def _to_dicts(messages: list[Any]) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in messages:
        if isinstance(m, dict):
            out.append({"role": str(m["role"]), "content": str(m["content"])})
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _status(exc: BaseException) -> int | None:
    return getattr(exc, "status_code", None)


def _record_failure(
    model: str, *, session_id: str | None, learner_id: str | None, prompt_version: str, tier: str, error: str
) -> None:
    """A provider call that raised still leaves a trace: one ``outcome=error`` usage event
    (cost_status unknown, never counted as expected cost) + its ``unit=requests`` ledger row."""
    from app.core.usage import UsageDraft, record_usage

    try:
        record_usage(
            UsageDraft(
                provider=vendor_of(model),
                stage="llm",
                model=model,
                learner_id=learner_id,
                session_id=session_id,
                service=_service_for(tier),
                tier_name=tier,
                prompt_version=prompt_version,
                outcome="error",
                meta={"tier": tier, "error": error[:200]},
            )
        )
    except Exception:  # noqa: BLE001
        log.exception("could not write failure event")


def _service_for(tier: str) -> Service:
    """Usage-summary service of an LLM tier: assessment/validator calls are 'Bewertung', the rest 'Sprachmodell'."""
    return "evaluation" if tier in ("assessment", "validator") else "llm"


def _api_key_kwargs(model: str, learner_id: str | None) -> dict[str, Any]:
    """BYOK: the learner's encrypted key for the model's vendor; ``None`` → LiteLLM's environment fallback."""
    key = credentials.require_workspace_key(learner_id, vendor_of(model))
    return {"api_key": key} if key else {}


def _usage_fields(resp: Any) -> dict[str, Any]:
    """Provider-reported usage of a LiteLLM response (never estimated) → ``record_llm_usage`` kwargs."""
    u = extract_llm_usage(resp)
    return {
        "tokens_in": u.get("input_tokens"),
        "tokens_out": u.get("output_tokens"),
        "tokens_in_cached": u.get("cached_input_tokens"),
        "reasoning_tokens": u.get("reasoning_tokens"),
        "total_tokens": u.get("total_tokens"),
        "request_id": u.get("request_id"),
        "raw_usage": u.get("raw_usage") or {},
    }


def _german_message(model: str, exc: BaseException) -> str:
    """Learner-facing wording for the most common provider failures; technical detail stays appended."""
    from app.core.models import vendor_of

    name = {
        "openai": "OpenAI",
        "anthropic": "Anthropic",
        "gemini": "Google Gemini",
        "mistral": "Mistral",
        "azure": "Azure",
    }.get(vendor_of(model), vendor_of(model))
    from app.core.credentials import redact

    text = redact(str(exc))
    if "AuthenticationError" in text or _status(exc) == 401 or "API Key" in text or "api key" in text.lower():
        return f"Für {name} ist kein gültiger Schlüssel hinterlegt (Einstellungen → Schlüssel). Modell: {model}."
    if _status(exc) == 429:
        return f"{name} ist gerade ausgelastet oder das Kontingent ist erschöpft (Modell {model}). Bitte kurz warten."
    if "NotFoundError" in text or _status(exc) == 404:
        return f"Das Modell {model} kennt {name} nicht – bitte config/models.yaml prüfen (scripts/verify_models.py)."
    return f"{name} konnte die Anfrage nicht bearbeiten. Bitte später erneut versuchen (Modell: {model})."


def _validator_alternative(tier: str, failed_model: str, exc: LLMError) -> str | None:
    """Next validator fallback (different vendor than assessment, key available) after a quota/404 error.

    Example: a free-tier Gemini key gets 429 on Gemini Pro but works with Gemini Flash (ADR-0003 lists
    the alternatives; ordering is data from config/models.yaml + pricing.yaml free_tier flags).
    """
    if tier != "validator" or exc.status not in (429, 404):
        return None
    from app.core.models import validator_candidates

    for candidate in validator_candidates():
        if candidate != failed_model:
            return candidate
    return None


def _call_litellm(
    model: str,
    messages: list[dict[str, str]],
    *,
    on_retry: Callable[[int, int | None], None] | None = None,
    **kwargs: Any,
) -> Any:
    import litellm

    litellm.drop_params = True
    litellm.telemetry = False  # ADR-0011: no telemetry
    delay = 1.0
    last: BaseException | None = None
    for attempt in range(3):
        try:
            return litellm.completion(model=model, messages=messages, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last = exc
            st = _status(exc)
            if st in RETRY_STATUS and attempt < 2:
                if on_retry is not None:
                    on_retry(attempt + 1, st)
                log.warning("LLM %s returned %s, retry %d", model, st, attempt + 1)
                time.sleep(delay)
                delay *= 2
                continue
            err = LLMError(_german_message(model, exc))
            err.status, err.model = st, model
            raise err from exc
    err = LLMError(_german_message(model, last) if last else f"LLM call to {model} failed after retries")
    err.status, err.model = _status(last) if last else None, model
    raise err


def _fake_response(schema: type[BaseModel] | None, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    text = fake_completion(schema, messages)
    u = usage_for(messages, text)
    return text, {"tokens_in": u["prompt_tokens"], "tokens_out": u["completion_tokens"], "request_id": None}


def _tier_call(
    tier: str,
    *,
    prompt_name: str | None,
    prompt_version: str,
    session_id: str | None,
    learner_id: str | None,
    n_messages: int,
    schema: type[BaseModel] | None,
    stream: bool = False,
) -> TierCall:
    return TierCall(
        kind="llm",
        tier=tier,
        model=resolve_tier(tier).model,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        session_id=session_id,
        learner_id=learner_id,
        payload_summary={"n_messages": n_messages, "schema": schema.__name__ if schema else None, "stream": stream},
    )


@overload
def complete(
    tier: str,
    messages: list[Any],
    schema: type[T],
    *,
    prompt_version: str,
    session_id: str | None,
    learner_id: str | None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    prompt_name: str | None = None,
    state: SessionState | None = None,
) -> T: ...


@overload
def complete(
    tier: str,
    messages: list[Any],
    schema: None = None,
    *,
    prompt_version: str,
    session_id: str | None,
    learner_id: str | None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    prompt_name: str | None = None,
    state: SessionState | None = None,
) -> str: ...


def complete(
    tier: str,
    messages: list[Any],
    schema: type[T] | None = None,
    *,
    prompt_version: str,
    session_id: str | None,
    learner_id: str | None,
    temperature: float | None = None,
    max_tokens: int | None = None,
    prompt_name: str | None = None,
    state: SessionState | None = None,
) -> T | str:
    """Chat completion for a tier through the hook engine. With ``schema``: JSON-mode + validation + one repair retry.

    ``prompt_name`` lets prompt-specific hooks fire (``roleplay_coach``, ``roleplay_counterpart``,
    ``pronunciation_tip``, ``speaking_feedback``); ``state`` carries hook outputs back
    (``state.extra["needs_review"|"regenerate"|"violations"]``). Raises ``HookDenied`` on a deny.
    """
    call = _tier_call(
        tier,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        session_id=session_id,
        learner_id=learner_id,
        n_messages=len(messages),
        schema=schema,
    )

    def dispatch(c: TierCall) -> T | str:
        return _complete_dispatch(
            c.tier,
            messages,
            schema,
            prompt_version=prompt_version,
            session_id=session_id,
            learner_id=learner_id,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    res = engine.execute(call, state or SessionState(), dispatch)
    if not res.success:
        raise HookDenied(res)
    return cast(T | str, res.result)


def _complete_dispatch(
    tier: str,
    messages: list[Any],
    schema: type[T] | None,
    *,
    prompt_version: str,
    session_id: str | None,
    learner_id: str | None,
    temperature: float | None,
    max_tokens: int | None,
) -> T | str:
    """The actual provider call (only reachable through ``engine.execute``)."""
    tc = resolve_tier(tier)
    msgs = _to_dicts(messages)
    s = get_settings()
    temp = tc.temperature if temperature is None else temperature
    mt = max_tokens or tc.max_tokens
    meta = {"session_id": session_id or "-", "learner_id": "anon", "prompt_version": prompt_version, "tier": tier}

    finish_reason: str | None = None

    def _run(msgs_: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
        nonlocal finish_reason
        finish_reason = None
        if s.llm_backend == "fake":
            return _fake_response(schema, msgs_)
        kwargs: dict[str, Any] = {"temperature": temp, "timeout": TIMEOUTS.get(tier, 60), "metadata": meta}
        kwargs.update(_api_key_kwargs(tc.model, learner_id))
        if mt:
            kwargs["max_tokens"] = mt
        if schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": False},
            }
        kwargs["on_retry"] = lambda attempt, status: _record_failure(
            tc.model,
            session_id=session_id,
            learner_id=learner_id,
            prompt_version=prompt_version,
            tier=tier,
            error=f"retry attempt {attempt}; HTTP status {status}",
        )
        attempted: set[str] = set()
        while True:
            attempted.add(tc.model)
            # Replace, rather than retain, credentials when the provider changes.
            kwargs.pop("api_key", None)
            kwargs.update(_api_key_kwargs(tc.model, learner_id))
            try:
                resp = _call_litellm(tc.model, msgs_, **kwargs)
                break
            except LLMError as exc:
                _record_failure(
                    tc.model,
                    session_id=session_id,
                    learner_id=learner_id,
                    prompt_version=prompt_version,
                    tier=tier,
                    error=str(exc),
                )
                if tier != "validator" or s.eu_strict_mode or exc.status not in (429, 404, 500, 502, 503, 504):
                    raise
                from app.core.models import validator_candidates

                candidates = validator_candidates()
                alternative = next((candidate for candidate in candidates if candidate not in attempted), None)
                if alternative is None:
                    raise LLMError(
                        "Die Zweitmeinung ist gerade nicht verfügbar. Bitte den verbundenen Anbieter und sein Kontingent prüfen oder später erneut versuchen."
                    ) from None
                log.warning("validator fallback: %s → %s (HTTP %s)", tc.model, alternative, exc.status)
                tc.model = alternative
        raw_finish = getattr(resp.choices[0], "finish_reason", None)
        finish_reason = (
            raw_finish if raw_finish in {"stop", "length", "max_tokens", "content_filter", "tool_calls"} else "unknown"
        )
        content = resp.choices[0].message.content or ""
        return content, _usage_fields(resp)

    content, usage = _run(msgs)
    record_llm_usage(
        tc.model,
        **usage,
        session_id=session_id,
        learner_id=learner_id,
        prompt_version=prompt_version,
        tier=tier,
        local=s.llm_backend == "fake",
        service=_service_for(tier),
        meta={
            "fake": s.llm_backend == "fake",
            "usage_source": "provider",
            "finish_reason": finish_reason,
            "max_tokens": mt,
        },
    )
    if schema is None:
        return content
    if finish_reason in {"content_filter", "tool_calls"}:
        raise LLMError("Die Bewertung lieferte keine verwendbare Textantwort. Bitte später erneut versuchen.")
    try:
        return schema.model_validate_json(_strip_fences(content))
    except ValidationError as exc:
        log.warning(
            "schema validation failed schema=%s tier=%s model=%s types=%s; attempting one repair",
            schema.__name__,
            tier,
            tc.model,
            [e["type"] for e in exc.errors(include_input=False, include_url=False)],
        )
        if finish_reason in {"length", "max_tokens"}:
            ceiling = tc.repair_max_tokens or mt or 0
            if not mt or ceiling <= mt:
                raise LLMError(
                    "Die Modellantwort wurde am Ausgabelimit abgeschnitten. Eine Wiederholung mit unverändertem Limit wurde vermieden."
                ) from None
            previous_limit = mt
            mt = min(mt * 2, ceiling)
            log.warning(
                "structured response truncated tier=%s model=%s; bounded output limit %s -> %s",
                tier,
                tc.model,
                previous_limit,
                mt,
            )
        repair = msgs + [
            {"role": "assistant", "content": content},
            {
                "role": "user",
                "content": "Die Antwort entsprach nicht dem JSON-Schema. Fehler: "
                + json.dumps([{"type": e["type"]} for e in exc.errors(include_input=False, include_url=False)][:12])
                + "\nVerbindliches Schema: "
                + json.dumps(schema.model_json_schema(), ensure_ascii=False)
                + "\nAntworte erneut NUR mit gültigem JSON nach dem Schema.",
            },
        ]
        content2, usage2 = _run(repair)
        record_llm_usage(
            tc.model,
            **usage2,
            session_id=session_id,
            learner_id=learner_id,
            prompt_version=prompt_version,
            tier=tier,
            local=s.llm_backend == "fake",
            service=_service_for(tier),
            meta={
                "repair": True,
                "fake": s.llm_backend == "fake",
                "usage_source": "provider",
                "finish_reason": finish_reason,
                "max_tokens": mt,
            },
        )
        try:
            return schema.model_validate_json(_strip_fences(content2))
        except ValidationError as repair_exc:
            log.warning(
                "schema repair failed schema=%s tier=%s model=%s types=%s",
                schema.__name__,
                tier,
                tc.model,
                [e["type"] for e in repair_exc.errors(include_input=False, include_url=False)],
            )
            raise LLMError(
                f"{schema.__name__}: ungültige Antwort nach erneutem Versuch. Bitte erneut versuchen."
            ) from None


def stream(
    tier: str,
    messages: list[Any],
    *,
    prompt_version: str,
    session_id: str | None,
    learner_id: str | None,
    prompt_name: str | None = None,
    state: SessionState | None = None,
) -> Iterator[str]:
    """Token stream for the tutor UI, through the hook engine.

    The upstream stream is consumed inside ``dispatch`` (usage recorded in ``finally`` even when
    the consumer disconnects), post-hooks run on the complete text, then chunks are yielded.
    """
    call = _tier_call(
        tier,
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        session_id=session_id,
        learner_id=learner_id,
        n_messages=len(messages),
        schema=None,
        stream=True,
    )

    def dispatch(c: TierCall) -> str:
        return "".join(
            _stream_dispatch(
                c.tier, messages, prompt_version=prompt_version, session_id=session_id, learner_id=learner_id
            )
        )

    res = engine.execute(call, state or SessionState(), dispatch)
    if not res.success:
        raise HookDenied(res)
    text = str(res.result)
    for i in range(0, len(text), 12):
        yield text[i : i + 12]


def _stream_dispatch(
    tier: str,
    messages: list[Any],
    *,
    prompt_version: str,
    session_id: str | None,
    learner_id: str | None,
) -> Iterator[str]:
    tc = resolve_tier(tier)
    msgs = _to_dicts(messages)
    s = get_settings()
    if s.llm_backend == "fake":
        text, fake_usage = _fake_response(None, msgs)
        yield text
        record_llm_usage(
            tc.model,
            **fake_usage,
            session_id=session_id,
            learner_id=learner_id,
            prompt_version=prompt_version,
            tier=tier,
            local=True,
            service=_service_for(tier),
            meta={"fake": True, "stream": True, "usage_source": "provider"},
        )
        return
    try:
        resp = _call_litellm(
            tc.model,
            msgs,
            temperature=tc.temperature,
            max_tokens=tc.max_tokens,
            on_retry=lambda attempt, status: _record_failure(
                tc.model,
                session_id=session_id,
                learner_id=learner_id,
                prompt_version=prompt_version,
                tier=tier,
                error=f"retry attempt {attempt}; HTTP status {status}",
            ),
            stream=True,
            stream_options={"include_usage": True},  # the provider sends a final usage chunk
            timeout=TIMEOUTS.get(tier, 60),
            **_api_key_kwargs(tc.model, learner_id),
        )
    except LLMError as exc:
        _record_failure(
            tc.model,
            session_id=session_id,
            learner_id=learner_id,
            prompt_version=prompt_version,
            tier=tier,
            error=str(exc),
        )
        raise
    full: list[str] = []
    usage: dict[str, Any] | None = None
    request_id: str | None = None
    try:
        for chunk in resp:
            request_id = request_id or getattr(chunk, "id", None)
            delta = chunk.choices[0].delta.content if chunk.choices else None
            if delta:
                full.append(delta)
                yield delta
            if getattr(chunk, "usage", None):
                usage = _usage_fields(chunk)  # provider usage chunk (stream_options.include_usage)
    finally:
        # Written even when the consumer disconnects mid-stream (product rule 8).
        meta: dict[str, Any] = {"stream": True, "usage_source": "provider"}
        if usage is None:
            usage = {"tokens_in": None, "tokens_out": None, "request_id": request_id, "raw_usage": {}}
            meta = {"stream": True, "usage_source": "unavailable"}
        if not usage.get("request_id"):
            usage["request_id"] = request_id
        record_llm_usage(
            tc.model,
            **usage,
            session_id=session_id,
            learner_id=learner_id,
            prompt_version=prompt_version,
            tier=tier,
            service=_service_for(tier),
            meta=meta,
        )


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t[3:]
        if t.endswith("```"):
            t = t[:-3]
    t = t.strip()
    # tolerate leading prose before the first brace
    if not t.startswith("{") and "{" in t:
        t = t[t.index("{") :]
    try:
        json.loads(t)
    except json.JSONDecodeError:
        pass
    return t
