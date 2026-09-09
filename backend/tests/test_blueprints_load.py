from __future__ import annotations

import pytest
from app.core.blueprints import Blueprint, load_blueprints, unverified
from pydantic import ValidationError


def test_seed_blueprints_load() -> None:
    bps = load_blueprints()
    assert set(bps) >= {
        "testdaf_digital_lesen",
        "testdaf_digital_hoeren",
        "testdaf_digital_schreiben",
        "testdaf_digital_sprechen",
    }
    for b in bps.values():
        assert b.score_scale.max == 20  # internal scale, never TDN


def test_unverified_reported() -> None:
    assert isinstance(unverified(), list)


def test_rubric_ref_required_for_rubric_scoring() -> None:
    with pytest.raises(ValidationError):
        Blueprint.model_validate(
            {
                "id": "x",
                "skill": "schreiben",
                "format": "digital",
                "source_url": "https://www.testdaf.de/",
                "version": "0.0.1",
                "total_time_seconds": 10,
                "n_tasks": 1,
                "input_modality": "text",
                "output_modality": "typed_text",
                "scoring": "rubric",
                "score_scale": {"min": 0, "max": 20},
                "tasks": [],
            }
        )


def test_item_sum_must_match() -> None:
    with pytest.raises(ValidationError):
        Blueprint.model_validate(
            {
                "id": "x",
                "skill": "lesen",
                "format": "digital",
                "source_url": "https://www.testdaf.de/",
                "version": "0.0.1",
                "total_time_seconds": 10,
                "n_tasks": 2,
                "n_items": 10,
                "input_modality": "text",
                "output_modality": "selection",
                "scoring": "deterministic",
                "score_scale": {"min": 0, "max": 20},
                "tasks": [{"task_type": "a", "n_items": 3}, {"task_type": "b", "n_items": 3}],
            }
        )


def test_api_lists_blueprints(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/blueprints")
    assert r.status_code == 200
    ids = {b["id"] for b in r.json()}
    assert "testdaf_digital_schreiben" in ids
