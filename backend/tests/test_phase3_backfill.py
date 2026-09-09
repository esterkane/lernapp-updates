"""Migration 0003 backfill: legacy ledger rows become usage events (run against real-shaped rows)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime

from app.core.db import get_engine
from app.core.paths import repo_root
from sqlalchemy import text


def _load_migration():  # type: ignore[no-untyped-def]
    path = repo_root() / "backend" / "app" / "db" / "migrations" / "versions" / "0003_usage_events_credentials.py"
    spec = importlib.util.spec_from_file_location("mig0003", path)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_backfill_groups_legacy_rows_like_the_installed_db(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    mod = _load_migration()
    engine = get_engine()
    ts = datetime(2026, 9, 8, 10, 31, 17, tzinfo=UTC)
    legacy = [
        # settings test recorded twice (requests row without tier + token rows with tier "test")
        (
            "settings-test",
            "llm",
            "openai",
            "openai/gpt-5.6-luna",
            "requests",
            1,
            None,
            None,
            False,
            '{"ok": true, "test": true}',
        ),
        (
            "settings-test",
            "llm",
            "openai",
            "openai/gpt-5.6-luna",
            "tokens_in",
            13,
            2e-7,
            2.6e-6,
            False,
            '{"tier": "test"}',
        ),
        (
            "settings-test",
            "llm",
            "openai",
            "openai/gpt-5.6-luna",
            "tokens_out",
            4,
            1.2e-6,
            4.8e-6,
            False,
            '{"tier": "test"}',
        ),
        # failed validator call
        (
            "task-1",
            "llm",
            "anthropic",
            "anthropic/claude-sonnet-5",
            "requests",
            1,
            None,
            None,
            False,
            '{"ok": false, "tier": "validator", "error": "x"}',
        ),
        # local STT
        ("sess-1", "stt", "local", "local/faster-whisper", "audio_seconds", 6.2, 0.0, 0.0, True, '{"ok": true}'),
    ]
    with engine.begin() as conn:
        conn.execute(text("delete from usage_events where idempotency_key like 'backfill:%'"))
        conn.execute(
            text("delete from cost_ledger where session_id in ('settings-test','task-1','sess-1') and event_id is null")
        )
        for sid, stage, prov, model, unit, qty, price, cost, local, meta in legacy:
            conn.execute(
                text(
                    "insert into cost_ledger (ts, session_id, learner_id, stage, provider, model, unit, quantity, unit_price_usd, cost_usd, "
                    "cost_eur, fx_rate, pricing_version, local, meta) values (:ts, :sid, 'default', :stage, :prov, :model, :unit, :qty, :price, "
                    ":cost, :ceur, 0.86, '2026.09.08.1', :local, cast(:meta as jsonb))"
                ),
                {
                    "ts": ts,
                    "sid": sid,
                    "stage": stage,
                    "prov": prov,
                    "model": model,
                    "unit": unit,
                    "qty": qty,
                    "price": price,
                    "cost": cost,
                    "ceur": None if cost is None else cost * 0.86,
                    "local": local,
                    "meta": meta,
                },
            )

    class _Op:
        @staticmethod
        def get_bind():  # type: ignore[no-untyped-def]
            return conn

    with engine.begin() as conn:
        monkeypatch.setattr(mod, "op", _Op)
        mod._backfill()
        rows = conn.execute(
            text(
                "select session_id, outcome, cost_status, input_tokens, output_tokens, expected_cost_usd from usage_events "
                "where idempotency_key like 'backfill:%' and session_id in ('settings-test','task-1','sess-1') order by session_id"
            )
        ).fetchall()
        linked = conn.execute(
            text(
                "select count(*) from cost_ledger where session_id in ('settings-test','task-1','sess-1') and event_id is null"
            )
        ).scalar()
    by_session = {r[0]: r for r in rows}
    assert linked == 0
    assert by_session["settings-test"][1:] == (
        "ok",
        "estimated",
        13,
        4,
        by_session["settings-test"][5],
    )  # ONE event, priced
    assert by_session["task-1"][1:3] == ("error", "unknown")
    assert by_session["sess-1"][1:3] == ("ok", "free")
    assert len(rows) == 3
