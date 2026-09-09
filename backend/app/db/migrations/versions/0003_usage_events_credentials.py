"""usage events (one per provider call) + encrypted provider credentials + ledger.event_id (ADR-0019)

Revision ID: 0003_usage_events
Revises: 0002_audit_log
Create Date: 2026-09-08

Backfill: existing cost_ledger rows are grouped into usage events (same second, session, stage, model,
tier). A lone ``unit=requests`` row with ``meta.ok=false`` becomes an ``outcome=error`` event; a
``requests`` row next to token rows (old settings-test double accounting) is folded into that event.
Historical cost values are copied as-is (list price at the time; cost_status ``estimated``, tier
``unknown``; local rows → ``free``).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import OrderedDict

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003_usage_events"
down_revision = "0002_audit_log"
branch_labels = None
depends_on = None

STAGE_TO_SERVICE = {"llm": "llm", "stt": "stt", "tts": "tts", "pron": "pronunciation", "embed": "embedding"}


def upgrade() -> None:
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("learner_id", sa.String(64)),
        sa.Column("session_id", sa.String(64)),
        sa.Column("request_id", sa.String(128)),
        sa.Column("idempotency_key", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("service", sa.String(16), nullable=False),
        sa.Column("stage", sa.String(8), nullable=False),
        sa.Column("tier_name", sa.String(32)),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("operation", sa.String(64)),
        sa.Column("prompt_version", sa.String(32)),
        sa.Column("outcome", sa.String(8), nullable=False, server_default="ok"),
        sa.Column("input_tokens", sa.Integer),
        sa.Column("cached_input_tokens", sa.Integer),
        sa.Column("output_tokens", sa.Integer),
        sa.Column("reasoning_tokens", sa.Integer),
        sa.Column("total_tokens", sa.Integer),
        sa.Column("audio_input_seconds", sa.Float),
        sa.Column("audio_output_seconds", sa.Float),
        sa.Column("characters", sa.Integer),
        sa.Column("pricing_tier", sa.String(8), nullable=False, server_default="unknown"),
        sa.Column("cost_status", sa.String(10), nullable=False, server_default="unknown"),
        sa.Column("list_cost_usd", sa.Float),
        sa.Column("list_cost_eur", sa.Float),
        sa.Column("expected_cost_usd", sa.Float),
        sa.Column("expected_cost_eur", sa.Float),
        sa.Column("pricing_rule_id", sa.String(160)),
        sa.Column("pricing_version", sa.String(24), nullable=False),
        sa.Column("fx_rate", sa.Float, nullable=False),
        sa.Column("fx_source", sa.String(64), nullable=False, server_default="config"),
        sa.Column("fx_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("local", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("raw_usage", pg.JSONB, nullable=False, server_default="{}"),
        sa.Column("meta", pg.JSONB, nullable=False, server_default="{}"),
    )
    op.create_index("ix_usage_events_ts", "usage_events", ["ts"])
    op.create_index("ix_usage_events_learner_id", "usage_events", ["learner_id"])
    op.create_index("ix_usage_events_session_id", "usage_events", ["session_id"])
    op.create_index("ix_usage_events_provider", "usage_events", ["provider"])
    op.create_index("ix_usage_events_service", "usage_events", ["service"])
    op.create_index("ix_usage_events_model", "usage_events", ["model"])
    op.create_index("ux_usage_events_idempotency_key", "usage_events", ["idempotency_key"], unique=True)

    op.create_table(
        "provider_credentials",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("learner_id", sa.String(64), sa.ForeignKey("learners.id", ondelete="CASCADE"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("ciphertext", sa.Text),
        sa.Column("key_hint", sa.String(8)),
        sa.Column("pricing_tier", sa.String(8), nullable=False, server_default="unknown"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_test_at", sa.DateTime(timezone=True)),
        sa.Column("last_test_ok", sa.Boolean),
        sa.Column("last_test_message", sa.String(300)),
        sa.UniqueConstraint("learner_id", "provider", name="uq_provider_credentials_learner_provider"),
    )
    op.create_index("ix_provider_credentials_learner_id", "provider_credentials", ["learner_id"])

    op.add_column("cost_ledger", sa.Column("event_id", sa.String(32)))
    op.create_index("ix_cost_ledger_event_id", "cost_ledger", ["event_id"])
    _backfill()


def _backfill() -> None:
    conn = op.get_bind()
    rows = (
        conn.execute(
            sa.text(
                "select id, ts, session_id, learner_id, stage, provider, model, prompt_version, unit, quantity, "
                "unit_price_usd, cost_usd, cost_eur, fx_rate, pricing_version, local, meta::text as meta "
                "from cost_ledger where event_id is null order by id"
            )
        )
        .mappings()
        .all()
    )
    groups: OrderedDict[tuple, list] = OrderedDict()
    for r in rows:
        meta = json.loads(r["meta"] or "{}")
        key = (
            r["session_id"],
            r["stage"],
            r["model"],
            r["provider"],
            r["prompt_version"],
            r["ts"].replace(microsecond=0),
            r["learner_id"],
        )  # meta.tier deliberately not part of the key (old settings-test rows differ in it)
        groups.setdefault(key, []).append((r, meta))
    # Fold a lone successful `requests` row (old settings-test double accounting) into the token-row
    # group of the same call when that group is within 2 seconds.
    for key in list(groups):
        items = groups[key]
        if len(items) == 1 and items[0][0]["unit"] == "requests" and items[0][1].get("ok"):
            sid, stage, model, provider, pv, ts, owner = key
            for other_key, other_items in groups.items():
                if other_key == key or other_key[:5] != (sid, stage, model, provider, pv) or other_key[6] != owner:
                    continue
                if abs((other_key[5] - ts).total_seconds()) <= 2 and any(
                    r["unit"] != "requests" for r, _ in other_items
                ):
                    other_items.extend(items)
                    del groups[key]
                    break
    for key, items in groups.items():
        session_id, stage, model, provider, prompt_version, ts, owner = key
        tier = next((m.get("tier") for _, m in items if m.get("tier")), None)
        units: dict[str, float] = {}
        for row, _ in items:
            units[row["unit"]] = units.get(row["unit"], 0.0) + float(row["quantity"])
        token_rows = [r for r, _ in items if r["unit"] != "requests"]
        request_rows = [(r, m) for r, m in items if r["unit"] == "requests"]
        # a lone `requests` row means: call recorded without usage; ok=false → failed call
        outcome = "ok" if token_rows or any(m.get("ok") for _, m in request_rows) else "error"
        local = any(r["local"] for r, _ in items)
        priced = [r for r in token_rows if r["cost_usd"] is not None]
        list_usd = sum(float(r["cost_usd"] or 0) for r in token_rows) if token_rows else None
        list_eur = sum(float(r["cost_eur"] or 0) for r in token_rows) if token_rows else None
        if outcome == "error" or not token_rows:
            status, tier_p, exp_usd, exp_eur = "unknown", "unknown", None, None
        elif local:
            status, tier_p, exp_usd, exp_eur = "free", "free", 0.0, 0.0
        elif len(priced) == len(token_rows):
            status, tier_p, exp_usd, exp_eur = "estimated", "unknown", list_usd, list_eur
        else:
            status, tier_p, exp_usd, exp_eur = "unknown", "unknown", None, None
        meta = {
            "backfilled_from_ledger": True,
            "request_count_estimated": True,
            "ledger_ids": [r["id"] for r, _ in items],
        }
        if request_rows:
            meta["error"] = request_rows[0][1].get("error")
            meta["settings_test"] = bool(request_rows[0][1].get("test"))
        first = items[0][0]
        event_id = uuid.uuid4().hex
        idem = (
            "backfill:"
            + hashlib.sha256(json.dumps([str(k) for k in key] + [str(first["id"])]).encode()).hexdigest()[:40]
        )
        conn.execute(
            sa.text(
                "insert into usage_events (id, ts, learner_id, session_id, idempotency_key, provider, service, stage, tier_name, "
                "model, prompt_version, outcome, input_tokens, cached_input_tokens, output_tokens, audio_input_seconds, characters, "
                "pricing_tier, cost_status, list_cost_usd, list_cost_eur, expected_cost_usd, expected_cost_eur, pricing_rule_id, "
                "pricing_version, fx_rate, fx_source, fx_at, local, raw_usage, meta) values "
                "(:id, :ts, :learner_id, :session_id, :idem, :provider, :service, :stage, :tier, :model, :pv, :outcome, :tin, :tcached, "
                ":tout, :audio, :chars, :ptier, :status, :lusd, :leur, :eusd, :eeur, :rule, :pver, :fx, 'config', :ts, :local, '{}', :meta)"
            ),
            {
                "id": event_id,
                "ts": first["ts"],
                "learner_id": first["learner_id"],
                "session_id": session_id,
                "idem": idem,
                "provider": provider,
                "service": STAGE_TO_SERVICE.get(stage, "other"),
                "stage": stage,
                "tier": tier,
                "model": model,
                "pv": prompt_version,
                "outcome": outcome,
                "tin": int(units["tokens_in"]) if "tokens_in" in units else None,
                "tcached": int(units["tokens_in_cached"]) if "tokens_in_cached" in units else None,
                "tout": int(units["tokens_out"]) if "tokens_out" in units else None,
                "audio": units.get("audio_seconds"),
                "chars": int(units["chars"]) if "chars" in units else None,
                "ptier": tier_p,
                "status": status,
                "lusd": list_usd,
                "leur": list_eur,
                "eusd": exp_usd,
                "eeur": exp_eur,
                "rule": f"{first['pricing_version']}:{stage}:{model}",
                "pver": first["pricing_version"],
                "fx": float(first["fx_rate"]),
                "local": local,
                "meta": json.dumps(meta),
            },
        )
        conn.execute(
            sa.text("update cost_ledger set event_id = :e where id = any(:ids)"),
            {"e": event_id, "ids": [r["id"] for r, _ in items]},
        )


def downgrade() -> None:
    op.drop_index("ix_cost_ledger_event_id", table_name="cost_ledger")
    op.drop_column("cost_ledger", "event_id")
    op.drop_table("provider_credentials")
    op.drop_table("usage_events")
