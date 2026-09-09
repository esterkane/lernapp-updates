"""Schema validity as the fraction of fields that validate against the Pydantic model (partial credit)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, TypeAdapter, ValidationError


def evaluate(payload: Any, schema: type[BaseModel]) -> dict[str, Any]:
    fields = schema.model_fields
    total = len(fields)
    if not isinstance(payload, dict):
        return {"score": 0.0, "valid_fields": 0, "total_fields": total, "invalid": list(fields)}
    try:
        schema.model_validate(payload)
        return {"score": 1.0, "valid_fields": total, "total_fields": total, "invalid": []}
    except ValidationError:
        pass
    invalid: list[str] = []
    for name, info in fields.items():
        if name not in payload:
            if info.is_required():
                invalid.append(name)
            continue
        try:
            TypeAdapter(info.annotation).validate_python(payload[name])
        except ValidationError:
            invalid.append(name)
    valid = total - len(invalid)
    return {
        "score": valid / total if total else 0.0,
        "valid_fields": valid,
        "total_fields": total,
        "invalid": invalid,
    }


def score(payload: Any, schema: type[BaseModel]) -> float:
    return float(evaluate(payload, schema)["score"])
