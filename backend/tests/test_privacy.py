"""Export + deletion (ADR-0011, product rule 9)."""

from __future__ import annotations

import csv
import importlib.util
import io
import json
import zipfile
from pathlib import Path

from app.core.db import db_session, ensure_default_learner
from app.core.paths import repo_root
from app.db.base import CostLedger, Learner, Session, Turn, UsageEvent, VocabItem
from app.services import privacy, tutor
from app.services.results import store_deterministic_result
from sqlalchemy import select


def _prepare(lid: str) -> str:
    """Learner with one session (ledger rows), a result and a vocab item."""
    with db_session() as db:
        if db.get(Learner, lid) is None:
            db.add(Learner(id=lid, display_name="Exportperson", level="B2"))
        db.add(VocabItem(owner_id=lid, wort="abwägen", bedeutung="Vor- und Nachteile prüfen"))
    sid = tutor.create_session(lid, "tutor")["session_id"]
    tutor.turn(sid, text="Ich möchte meine Verhandlung vorbereiten.")
    store_deterministic_result(
        lid, skill="lesen", task_type="lesen_1", blueprint_id="testdaf_lesen", score=6, score_max=10
    )
    return sid


def test_export_zip_via_api(client) -> None:  # type: ignore[no-untyped-def]
    lid = "privacy-export"
    sid = _prepare(lid)
    r = client.get(f"/learners/{lid}/export")
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"]
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    assert set(zf.namelist()) == set(privacy.EXPORT_MEMBERS)

    learner = json.loads(zf.read("learner.json"))
    assert json.loads(zf.read("usage.json")), "usage events must be exported"
    assert learner["id"] == lid and learner["display_name"] == "Exportperson" and learner["exported_at"]
    sessions = json.loads(zf.read("sessions.json"))
    assert [s["session_id"] for s in sessions] == [sid]
    assert [t["role"] for t in sessions[0]["turns"]] == ["learner", "assistant"]
    results = json.loads(zf.read("results.json"))
    assert len(results) == 1 and results[0]["skill"] == "lesen"
    assert json.loads(zf.read("documents.json")) == []
    vocab = list(csv.DictReader(io.StringIO(zf.read("vocab.csv").decode("utf-8"))))
    assert vocab[0]["wort"] == "abwägen"
    progress = list(csv.DictReader(io.StringIO(zf.read("progress.csv").decode("utf-8"))))
    assert progress[0]["score"] == "6.0" and "internal_score_20" in progress[0]
    costs = list(csv.DictReader(io.StringIO(zf.read("costs.csv").decode("utf-8"))))
    assert len(costs) >= 2 and sid in {c["session_id"] for c in costs}
    assert {c["stage"] for c in costs} >= {"llm", "tts"}
    assert client.get("/learners/nope/export").status_code == 404


def test_export_cli_script(tmp_path: Path) -> None:
    lid = "privacy-cli"
    _prepare(lid)
    script = repo_root() / "scripts" / "export_learner_data.py"
    spec = importlib.util.spec_from_file_location("export_learner_data", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    out = tmp_path / "export"
    assert mod.main(["--learner", lid, "--out", str(out)]) == 0
    assert {p.name for p in out.iterdir()} == set(privacy.EXPORT_MEMBERS)
    assert json.loads((out / "learner.json").read_text(encoding="utf-8"))["id"] == lid
    assert mod.main(["--learner", "nope", "--out", str(tmp_path / "x")]) == 1


def test_delete_learner_cascades_and_anonymises_ledger(client) -> None:  # type: ignore[no-untyped-def]
    lid = "privacy-delete"
    sid = _prepare(lid)
    with db_session() as db:
        rows_before = db.execute(select(CostLedger).where(CostLedger.session_id == sid)).scalars().all()
        assert rows_before and all(r.learner_id == lid for r in rows_before)
        ids = [r.id for r in rows_before]

    r = client.delete(f"/learners/{lid}")
    assert r.status_code == 200 and r.json()["deleted"] is True

    with db_session() as db:
        assert db.execute(select(UsageEvent).where(UsageEvent.learner_id == lid)).first() is None
        assert db.execute(select(UsageEvent).where(UsageEvent.session_id == sid)).first() is None
        assert db.get(Learner, lid) is None
        assert db.get(Session, sid) is None
        assert db.execute(select(Turn).where(Turn.session_id == sid)).first() is None
        assert db.execute(select(VocabItem).where(VocabItem.owner_id == lid)).first() is None
        kept = db.execute(select(CostLedger).where(CostLedger.id.in_(ids))).scalars().all()
        assert len(kept) == len(ids), "ledger rows are kept for aggregates"
        assert all(r.learner_id is None and r.session_id is None and r.meta == {} for r in kept)
        assert all(r.cost_eur is not None for r in kept)
    assert client.delete(f"/learners/{lid}").status_code == 404
    assert client.get(f"/learners/{lid}").status_code == 404


def test_delete_default_learner_then_recreate(client, learner_id: str) -> None:  # type: ignore[no-untyped-def]
    sid = tutor.create_session(learner_id, "tutor")["session_id"]
    tutor.turn(sid, text="Kurzer Test.")
    try:
        r = client.delete(f"/learners/{learner_id}")
        assert r.status_code == 200
        with db_session() as db:
            assert db.get(Learner, learner_id) is None
            assert db.execute(select(CostLedger).where(CostLedger.learner_id == learner_id)).first() is None
    finally:
        assert ensure_default_learner() == learner_id
    with db_session() as db:
        assert db.get(Learner, learner_id) is not None
