"""Exception → HTTP mapping for boundary errors (ADR-0016 hook denials, session-hub fatal failures)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.credentials import CredentialError
from app.core.errors import AdapterFailure
from app.core.hooks import HookDenied

STATUS_BY_CATEGORY = {"business": 400, "validation": 422, "permission": 403, "transient": 502}


def _hook_denied(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, HookDenied)  # noqa: S101 — registered for this type only
    err = exc.result.error
    category = err.category if err else "business"
    body: dict[str, Any] = {
        "detail": err.message if err else str(exc),
        "error_category": category,
        "alternatives": err.alternatives if err else [],
    }
    return JSONResponse(status_code=STATUS_BY_CATEGORY.get(category, 400), content=body)


def _adapter_failure(_: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AdapterFailure)  # noqa: S101
    ctx = exc.context
    body: dict[str, Any] = {
        "detail": ctx.message,
        "error_category": ctx.category,
        "alternatives": ctx.alternatives,
    }
    return JSONResponse(status_code=STATUS_BY_CATEGORY.get(ctx.category, 502), content=body)


SECRET_FIELDS = {"api_key", "token", "password", "secret"}


def _strip_secret_input(value: Any) -> Any:
    """Drop submitted secrets from an echoed validation ``input`` (dict keys named like a credential)."""
    if isinstance(value, dict):
        return {k: ("***" if str(k).lower() in SECRET_FIELDS else _strip_secret_input(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_secret_input(v) for v in value]
    return value


def _validation_error(_: Request, exc: Exception) -> JSONResponse:
    """422 like FastAPI's default, but a submitted ``api_key`` is never echoed back (ADR-0019)."""
    assert isinstance(exc, RequestValidationError)  # noqa: S101
    errors: list[dict[str, Any]] = []
    for err in exc.errors():
        e = dict(err)
        loc = tuple(str(x) for x in e.get("loc", ()))
        if any(x.lower() in SECRET_FIELDS for x in loc):
            e.pop("input", None)
        elif "input" in e:
            e["input"] = _strip_secret_input(e["input"])
        e.pop("url", None)
        if "ctx" in e:
            e["ctx"] = {k: str(v) for k, v in dict(e["ctx"]).items()}
        errors.append(e)
    return JSONResponse(status_code=422, content={"detail": jsonable_encoder(errors)})


def _credential_error(_: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "detail": "Der gespeicherte API-Schlüssel kann nicht verwendet werden. Bitte unter Einstellungen erneut hinterlegen."
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(CredentialError, _credential_error)
    app.add_exception_handler(HookDenied, _hook_denied)
    app.add_exception_handler(AdapterFailure, _adapter_failure)
    app.add_exception_handler(RequestValidationError, _validation_error)
