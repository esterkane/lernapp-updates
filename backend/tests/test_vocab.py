"""Vocab: SM-2 unit cases, CSV import through POST /documents, due list, review scheduling, stats, delete."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.core.db import db_session
from app.db.base import Document, VocabItem
from app.services import vocab
from app.services.vocab import sm2
from sqlalchemy import select


@pytest.fixture(autouse=True)
def _clean(learner_id: str) -> None:
    with db_session() as db:
        db.execute(VocabItem.__table__.delete())
        for d in db.execute(select(Document)).scalars():
            db.delete(d)


# ---------------------------------------------------------------- SM-2 pure function


def test_sm2_first_reviews_follow_1_6_then_ease() -> None:
    ease, interval, reps = sm2(2.5, 0, 0, 5)
    assert (ease, interval, reps) == (2.6, 1, 1)
    ease, interval, reps = sm2(ease, interval, reps, 4)
    assert (interval, reps) == (6, 2) and ease == 2.6
    ease, interval, reps = sm2(ease, interval, reps, 4)
    assert reps == 3 and interval == round(6 * 2.6) == 16


def test_sm2_failure_resets_repetitions_and_lowers_ease() -> None:
    ease, interval, reps = sm2(2.5, 16, 3, 2)
    assert (interval, reps) == (1, 0)
    assert ease < 2.5
    e = 2.5
    for _ in range(20):
        e, _i, _r = sm2(e, 1, 0, 0)
    assert e == 1.3  # floor


def test_sm2_quality_3_is_a_pass_and_bounds() -> None:
    ease, interval, reps = sm2(2.5, 0, 0, 3)
    assert reps == 1 and interval == 1 and ease == 2.36
    with pytest.raises(ValueError):
        sm2(2.5, 0, 0, 6)
    with pytest.raises(ValueError):
        sm2(2.5, 0, 0, -1)


# ---------------------------------------------------------------- CSV parsing


def test_parse_csv_variants() -> None:
    bom_semicolon = "﻿wort;bedeutung;beispiel\nverhandeln;to negotiate;Wir verhandeln über den Preis.\nSkonto;discount\n"
    rows = vocab.parse_csv(bom_semicolon.encode("utf-8"))
    assert rows == [
        ("verhandeln", "to negotiate", "Wir verhandeln über den Preis."),
        ("Skonto", "discount", None),
    ]
    no_header_comma = "die Lieferfrist,delivery deadline\ngrößtenteils,mostly\n\n,leer\n"
    rows = vocab.parse_csv(no_header_comma.encode("utf-8-sig"))
    assert rows == [("die Lieferfrist", "delivery deadline", None), ("größtenteils", "mostly", None)]
    assert vocab.parse_csv(b"") == []
    assert vocab.parse_csv(b"nur eine Spalte\n") == []


# ---------------------------------------------------------------- API round trip


def test_csv_upload_due_review_stats_delete(learner_id: str, client) -> None:  # type: ignore[no-untyped-def]
    csv = "﻿wort;bedeutung;beispiel\nverhandeln;to negotiate;Wir verhandeln.\nSkonto;discount\nZahlungsziel;payment term;\n"
    r = client.post(
        "/documents",
        files={"file": ("vokabeln.csv", csv.encode("utf-8"), "text/csv")},
        data={"learner_id": learner_id, "tags": "vokabeln"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_vocab_items"] == 3 and body["n_chunks"] == 0 and body["status"] == "indexiert"
    assert body["cost_eur"] == 0.0  # no embedding for vocab lists
    docs = client.get("/documents", params={"learner_id": learner_id}).json()
    assert docs[0]["tags"] == ["vokabeln"] and docs[0]["mime"] == "text/csv"

    stats = client.get("/vocab/stats", params={"learner_id": learner_id}).json()
    assert stats == {"total": 3, "due": 3}

    due = client.get("/vocab", params={"learner_id": learner_id, "due_only": "true", "limit": 20}).json()
    assert [d["wort"] for d in due] == ["verhandeln", "Skonto", "Zahlungsziel"]
    assert {"id", "wort", "bedeutung", "beispiel", "due_at", "repetitions", "interval_days"} <= set(due[0])
    assert due[0]["beispiel"] == "Wir verhandeln." and due[1]["beispiel"] is None

    item_id = due[0]["id"]
    r = client.post(f"/vocab/{item_id}/review", json={"quality": 5})
    assert r.status_code == 200
    item = r.json()
    assert item["repetitions"] == 1 and item["interval_days"] == 1 and item["ease"] == 2.6
    due_at = datetime.fromisoformat(item["due_at"])
    assert timedelta(hours=23) < due_at - datetime.now(UTC) <= timedelta(days=1)

    r = client.post(f"/vocab/{item_id}/review", json={"quality": 4})
    assert r.json()["interval_days"] == 6 and r.json()["repetitions"] == 2
    r = client.post(f"/vocab/{item_id}/review", json={"quality": 1})
    assert r.json()["interval_days"] == 1 and r.json()["repetitions"] == 0
    assert client.post(f"/vocab/{item_id}/review", json={"quality": 9}).status_code == 422
    assert client.post("/vocab/nope/review", json={"quality": 3}).status_code == 404

    due_now = client.get("/vocab", params={"learner_id": learner_id, "due_only": "true"}).json()
    assert item_id not in {d["id"] for d in due_now}
    everything = client.get("/vocab", params={"learner_id": learner_id, "due_only": "false"}).json()
    assert len(everything) == 3
    assert client.get("/vocab/stats", params={"learner_id": learner_id}).json() == {"total": 3, "due": 2}

    assert client.delete(f"/vocab/{item_id}").json() == {"deleted": True}
    assert client.delete(f"/vocab/{item_id}").status_code == 404
    assert client.get("/vocab/stats", params={"learner_id": learner_id}).json() == {"total": 2, "due": 2}


def test_add_item_api_and_validation(learner_id: str, client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/vocab", json={"learner_id": learner_id, "wort": "die Lieferfrist", "bedeutung": "delivery deadline"}
    )
    assert r.status_code == 200
    item = r.json()
    assert item["wort"] == "die Lieferfrist" and item["repetitions"] == 0 and item["beispiel"] is None
    assert client.post("/vocab", json={"learner_id": learner_id, "wort": " ", "bedeutung": "x"}).status_code == 400
    assert vocab.stats(learner_id) == {"total": 1, "due": 1}
    assert vocab.get_item(item["id"], owner_id="someone-else") is None
    assert vocab.review(item["id"], 4, owner_id="someone-else") is None


def test_empty_csv_is_an_error_document(learner_id: str, client) -> None:  # type: ignore[no-untyped-def]
    r = client.post(
        "/documents",
        files={"file": ("leer.csv", b"wort;bedeutung\n", "text/csv")},
        data={"learner_id": learner_id},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "fehler" and r.json()["n_vocab_items"] == 0
