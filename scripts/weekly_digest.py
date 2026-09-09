#!/usr/bin/env python
"""Weekly learner-progress + cost digest (skill ``.claude/skills/weekly-digest``).

Summary only: reads the DB through the app's own services/ledger and the latest eval report, then
writes ``reports/digest-<YYYY-WW>.md``. Never sends, posts, emails or writes anywhere else; never
overwrites an existing digest (suffix ``-2``, ``-3`` … instead).

Usage:
    uv run python scripts/weekly_digest.py [--out reports] [--days 7] [--demo]

``--demo`` first creates a little activity through the service layer (one tutor session with two
text turns, one writing assessment) so a fresh data dir yields numbers. Use it only with fake
backends, e.g.::

    LERNAPP_DATA_DIR=/tmp/lernapp-digest LLM_BACKEND=fake STT_BACKEND=fake TTS_BACKEND=fake \\
      EMBEDDING_BACKEND=fake uv run python scripts/weekly_digest.py --demo
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "backend"))

WINDOW_DAYS = 7
TOP_ERROR_TAGS = 5
MAE_REGRESSION_LIMIT = 0.15  # eval-harness skill: no criterion may worsen by more than this without a note
TODO_MARKER = "# " + "TODO(verify)"  # built at runtime so this file does not count itself
# Only places where a marker is actionable (model ids, prices, blueprint facts, adapters) — not docs that
# merely describe the convention (CLAUDE.md, skills, the lernapp-kit template copy).
SCAN_DIRS = ("backend", "config", "blueprints", "prompts", "scripts", "mcp", "frontend", "evals", "scenarios")
SCAN_SUFFIXES = {".py", ".yaml", ".yml", ".md", ".toml"}
SCAN_EXCLUDE = {".venv", "__pycache__", "reports", "fixtures"}

# Query names quoted in the ops section so every figure can be re-run by hand.
Q_USAGE = "usage_report.summary(None, from_=…, to=…)  ≙  GET /usage/summary?learner_id&month (expected cost, ADR-0019)"
Q_SUMMARY = 'ledger.summary(from_=…, to=…, group_by="stage")  ≙  GET /costs/summary?from&to&group_by=stage'
Q_COUNTERFACTUAL = (
    'ledger.counterfactual(variant="all_cloud_openai", from_=…, to=…)  ≙  '
    "GET /costs/counterfactual?variant=all_cloud_openai&from&to"
)
Q_SESSIONS = "SELECT kind, count(*), sum(duration_seconds) FROM sessions WHERE started_at >= :from AND started_at < :to GROUP BY kind"
Q_RUBRICS = (
    "SELECT rubric_version, count(*), sum(score), sum(score_max) FROM results "
    "WHERE created_at >= :from AND created_at < :to AND rubric_version IS NOT NULL GROUP BY rubric_version"
)
Q_TAGS = "SELECT unnest(error_tags) AS tag, count(*) FROM results WHERE created_at >= :from AND created_at < :to GROUP BY tag ORDER BY 2 DESC LIMIT 5"
Q_PENDING = "SELECT id, skill, created_at FROM results WHERE (payload->>'routing' = 'spot_check' OR needs_review) AND NOT payload ? 'human_label'"


def _iso(d: datetime) -> str:
    return d.astimezone(UTC).isoformat(timespec="seconds")


def _safe(label: str, fn: Callable[[], Any], errors: list[str]) -> Any:
    """Run one query; on failure record it and return None — never invent a figure."""
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 — the digest must finish and *say* what failed
        errors.append(f"{label}: {type(exc).__name__}: {str(exc)[:200]}")
        return None


# ---------------------------------------------------------------- collection


def _sessions_per_kind(start: datetime, end: datetime) -> list[dict[str, Any]]:
    from app.core.db import db_session
    from app.db.base import Session
    from sqlalchemy import func, select

    with db_session() as db:
        q = (
            select(Session.kind, func.count().label("n"), func.sum(Session.duration_seconds).label("seconds"))
            .where(Session.started_at >= start, Session.started_at < end)
            .group_by(Session.kind)
            .order_by(Session.kind)
        )
        return [{"kind": r.kind, "n": int(r.n), "seconds": float(r.seconds or 0.0)} for r in db.execute(q)]


def _rubric_totals(start: datetime, end: datetime) -> list[dict[str, Any]]:
    from app.core.db import db_session
    from app.db.base import Result
    from sqlalchemy import func, select

    with db_session() as db:
        q = (
            select(
                Result.rubric_version,
                func.count().label("n"),
                func.sum(Result.score).label("score"),
                func.sum(Result.score_max).label("score_max"),
            )
            .where(Result.created_at >= start, Result.created_at < end, Result.rubric_version.is_not(None))
            .group_by(Result.rubric_version)
            .order_by(Result.rubric_version)
        )
        return [
            {
                "rubric_version": r.rubric_version,
                "n": int(r.n),
                "score": float(r.score or 0.0),
                "score_max": float(r.score_max or 0.0),
            }
            for r in db.execute(q)
        ]


def _top_error_tags(start: datetime, end: datetime) -> list[tuple[str, int]]:
    from app.core.db import db_session
    from app.db.base import Result
    from sqlalchemy import select

    counter: Counter[str] = Counter()
    with db_session() as db:
        q = select(Result.error_tags).where(Result.created_at >= start, Result.created_at < end)
        for (tags,) in db.execute(q):
            counter.update(t for t in (tags or []) if t)
    return counter.most_common(TOP_ERROR_TAGS)


def _pending_human_checks() -> list[dict[str, Any]]:
    """Spot-checks (routing == spot_check) and needs_review results without a human label yet — all time."""
    from app.core.db import db_session
    from app.db.base import Result
    from sqlalchemy import or_, select

    with db_session() as db:
        q = (
            select(Result.id, Result.skill, Result.task_type, Result.needs_review, Result.payload, Result.created_at)
            .where(or_(Result.needs_review.is_(True), Result.payload["routing"].astext == "spot_check"))
            .order_by(Result.created_at.desc())
        )
        out: list[dict[str, Any]] = []
        for r in db.execute(q):
            payload = r.payload or {}
            if "human_label" in payload:
                continue
            out.append(
                {
                    "id": r.id,
                    "skill": r.skill,
                    "task_type": r.task_type,
                    "routing": payload.get("routing") or ("needs_review" if r.needs_review else "?"),
                    "created_at": _iso(r.created_at),
                }
            )
        return out


def _learner_names() -> list[str]:
    from app.core.db import db_session
    from app.db.base import Learner
    from sqlalchemy import select

    with db_session() as db:
        return [str(n) for (n,) in db.execute(select(Learner.display_name).order_by(Learner.created_at))]


def _latest_eval_reports(reports_dir: Path) -> dict[str, Any]:
    mds = sorted(reports_dir.glob("*.md"))
    if not mds:
        return {"found": False}
    latest_md = mds[-1]
    jsons = sorted(reports_dir.glob("*.json"))
    latest: dict[str, Any] | None = None
    previous: dict[str, Any] | None = None
    if jsons:
        latest = json.loads(jsons[-1].read_text(encoding="utf-8"))
        if len(jsons) > 1:
            previous = json.loads(jsons[-2].read_text(encoding="utf-8"))
    verdict = "not checkable — only one eval report exists (no previous run to compare against)"
    regressions: list[str] = []
    if latest and previous:
        cur = latest.get("suites", {}).get("writing", {}).get("mae_per_criterion", {})
        old = previous.get("suites", {}).get("writing", {}).get("mae_per_criterion", {})
        for crit, val in cur.items():
            if crit in old and float(val) - float(old[crit]) > MAE_REGRESSION_LIMIT:
                regressions.append(f"{crit}: {old[crit]} → {val}")
        verdict = (
            f"FAILED — MAE worsened by > {MAE_REGRESSION_LIMIT} on: " + "; ".join(regressions)
            if regressions
            else f"passed — no criterion worsened by > {MAE_REGRESSION_LIMIT} vs previous run {previous.get('run_id')}"
        )
    suites: dict[str, Any] = latest.get("suites", {}) if latest else {}
    headline = {
        name: {
            k: v
            for k, v in res.items()
            if k in ("n", "mae_mean", "within_1", "recall_at_5", "mrr", "precision", "recall")
        }
        for name, res in suites.items()
    }
    return {
        "found": True,
        "file": str(latest_md.relative_to(ROOT)) if latest_md.is_relative_to(ROOT) else str(latest_md),
        "title": latest_md.read_text(encoding="utf-8").splitlines()[0].lstrip("# ").strip(),
        "backend": latest.get("llm_backend") if latest else None,
        "cost_eur": latest.get("cost_eur") if latest else None,
        "suites": headline,
        "regression_verdict": verdict,
        "n_reports": len(mds),
    }


def _count_todo_verify(root: Path) -> tuple[int, list[str]]:
    n = 0
    files: list[str] = []
    self_path = Path(__file__).resolve()
    for sub in SCAN_DIRS:
        for p in (root / sub).rglob("*"):
            if not p.is_file() or p.suffix not in SCAN_SUFFIXES or p.resolve() == self_path:
                continue
            if any(part in SCAN_EXCLUDE for part in p.relative_to(root).parts):
                continue
            try:
                hits = sum(
                    1 for line in p.read_text(encoding="utf-8", errors="ignore").splitlines() if TODO_MARKER in line
                )
            except OSError:
                continue
            if hits:
                n += hits
                files.append(f"{p.relative_to(root)} ({hits})")
    return n, files


def collect(now: datetime, days: int = WINDOW_DAYS, root: Path = ROOT) -> dict[str, Any]:
    from app.core import ledger
    from app.core.blueprints import unverified
    from app.services import usage_report

    start, end = now - timedelta(days=days), now
    errors: list[str] = []
    data: dict[str, Any] = {
        "now": now,
        "start": start,
        "end": end,
        "days": days,
        "learners": _safe("learners", _learner_names, errors) or [],
        "sessions": _safe("sessions", lambda: _sessions_per_kind(start, end), errors),
        "rubrics": _safe("rubric totals", lambda: _rubric_totals(start, end), errors),
        "error_tags": _safe("error tags", lambda: _top_error_tags(start, end), errors),
        "pending": _safe("pending spot-checks", _pending_human_checks, errors),
        "usage": _safe("usage_report.summary", lambda: usage_report.summary(None, from_=start, to=end), errors),
        "summary": _safe("ledger.summary", lambda: ledger.summary(from_=start, to=end, group_by="stage"), errors),
        "counterfactual": _safe(
            "ledger.counterfactual",
            lambda: ledger.counterfactual(variant="all_cloud_openai", from_=start, to=end),
            errors,
        ),
        "evals": _safe("eval reports", lambda: _latest_eval_reports(root / "evals" / "reports"), errors),
        "unverified_blueprints": _safe("unverified blueprints", unverified, errors),
        "todo_verify": _safe("TODO(verify) scan", lambda: _count_todo_verify(root), errors),
        "errors": errors,
    }
    return data


# ---------------------------------------------------------------- rendering


def _eur(x: float | None) -> str:
    return "—" if x is None else f"{x:.2f} €"


def render(d: dict[str, Any]) -> str:
    now: datetime = d["now"]
    iso = now.isocalendar()
    week = f"{iso.year}-{iso.week:02d}"
    sessions: list[dict[str, Any]] | None = d["sessions"]
    rubrics: list[dict[str, Any]] | None = d["rubrics"]
    tags: list[tuple[str, int]] | None = d["error_tags"]
    pending: list[dict[str, Any]] | None = d["pending"]
    summary: dict[str, Any] | None = d["summary"]
    usage: dict[str, Any] | None = d.get("usage")
    cf: dict[str, Any] | None = d["counterfactual"]
    ev: dict[str, Any] | None = d["evals"]
    names = ", ".join(d["learners"]) or "—"
    learning_minutes = sum(s["seconds"] for s in sessions) / 60.0 if sessions else 0.0

    lines: list[str] = [
        f"# Wochenbericht / Weekly digest {week}",
        "",
        f"Zeitraum / window: {_iso(d['start'])} – {_iso(d['end'])} ({d['days']} Tage). "
        f"Erstellt / generated: {_iso(now)}. Nur Zusammenfassung — nichts wurde gesendet oder verändert.",
        "",
        "## Lernfortschritt (für dich)",
        "",
        f"Hallo {names}! So lief deine Woche:",
        "",
        "### Sitzungen pro Fertigkeit",
    ]
    if sessions is None:
        lines.append("- Abfrage fehlgeschlagen — siehe Ops.")
    elif not sessions:
        lines.append("- Diese Woche keine Sitzungen aufgezeichnet.")
    else:
        lines.extend(f"- {s['kind']}: {s['n']} Sitzung(en), {s['seconds'] / 60.0:.1f} Minuten" for s in sessions)
    lines += ["", f"**Lernminuten gesamt:** {learning_minutes:.1f}", "", "### Rubrik-Score nach Rubrik-Version"]
    lines.append("(interne Übungsbewertung, 0–4 je Kriterium — keine offizielle TestDaF-Note)")
    if rubrics is None:
        lines.append("- Abfrage fehlgeschlagen — siehe Ops.")
    elif not rubrics:
        lines.append("- Keine bewerteten Texte/Transkripte in diesem Zeitraum.")
    else:
        lines.append("")
        lines.append("| rubric_version | Bewertungen | Summe Punkte | maximal | Anteil |")
        lines.append("|---|---|---|---|---|")
        for r in rubrics:
            share = f"{100.0 * r['score'] / r['score_max']:.0f} %" if r["score_max"] else "—"
            lines.append(f"| {r['rubric_version']} | {r['n']} | {r['score']:.0f} | {r['score_max']:.0f} | {share} |")
    lines += ["", f"### Häufigste Fehler-Tags (Top {TOP_ERROR_TAGS})"]
    if tags is None:
        lines.append("- Abfrage fehlgeschlagen — siehe Ops.")
    elif not tags:
        lines.append("- Keine Fehler-Tags in diesem Zeitraum.")
    else:
        lines.extend(f"{i}. `{t}` — {n}×" for i, (t, n) in enumerate(tags, start=1))
    lines += ["", "### Kosten dieser Woche"]
    if usage is not None:
        req = usage["requests"]
        lines.append(
            f"- Erwartete Kosten (Tarif berücksichtigt): {_eur(usage['expected_cost_eur'])} "
            f"für {req['total']} Anfragen — {usage['status_note_de']}"
        )
    if summary is None:
        lines.append("- Kostenabfrage fehlgeschlagen — siehe Ops (keine Zahl erfunden).")
    else:
        lines.append(
            f"- Zum Vergleich Listenpreis laut Ledger: {_eur(summary['total_eur'])} "
            f"für {summary['rows']} abgerechnete Einheiten"
        )
        if summary.get("unpriced_rows"):
            lines.append(f"- Achtung: {summary['unpriced_rows']} Einheiten ohne Preis in pricing.yaml")
        if cf is not None:
            lines.append(f"- Alles in der Cloud (OpenAI) hätte gekostet: {_eur(cf['counterfactual_eur'])}")
    lines += ["", "### Wartet auf dich"]
    if pending is None:
        lines.append("- Abfrage fehlgeschlagen — siehe Ops.")
    elif not pending:
        lines.append("- Nichts offen: keine Stichproben oder Nachprüfungen zu bestätigen.")
    else:
        lines.append(f"- {len(pending)} Bewertung(en) warten auf deine Bestätigung (Stichprobe/Nachprüfung).")

    # ------------------------------------------------------------ ops
    lines += ["", "## Ops (English)", "", "### Expected cost (usage events, ADR-0019 — primary figure)", ""]
    if usage is None:
        lines.append("- usage_report.summary FAILED — see errors below; no figure invented.")
    else:
        req = usage["requests"]
        lines.append(f"- query: `{Q_USAGE}`")
        lines.append(f"- expected_cost_eur: {usage['expected_cost_eur']!r} · list_cost_eur: {usage['list_cost_eur']!r}")
        lines.append(
            f"- requests: total {req['total']} · priced {req['priced']} · free {req['free']} · "
            f"unknown {req['unknown']} · failed {req['failed']}"
        )
        lines.append(
            f"- learning_seconds: {usage['learning_seconds']!r} · "
            f"cost_per_learning_hour_eur: {usage['cost_per_learning_hour_eur']!r}"
        )
        for um in usage.get("unknown_models") or []:
            lines.append(
                f"- UNPRICED: {um['provider']} {um['model']} ({um['service']}) ×{um['requests']}: {um['missing_units']}"
            )
    lines += ["", "### Ledger (verbatim from the ledger, list price — secondary figure)", ""]
    if summary is None:
        lines.append("- ledger.summary FAILED — see errors below; no figure invented.")
    else:
        lines.append(f"- query: `{Q_SUMMARY}`")
        lines.append(f"- window: from={summary['from']} to={summary['to']}")
        lines.append(f"- total_eur: {summary['total_eur']!r}")
        lines.append(f"- total_usd: {summary['total_usd']!r}")
        lines.append(f"- rows: {summary['rows']} · unpriced_rows: {summary['unpriced_rows']}")
        lines.append(f"- pricing_version: {summary['pricing_version']} · fx_rate: {summary['fx_rate']!r}")
        lines.append(f"- learning_seconds: {summary['learning_seconds']!r}")
        ls = float(summary["learning_seconds"] or 0.0)
        if ls > 0:
            per_hour = float(summary["total_eur"]) / (ls / 3600.0)
            lines.append(f"- eur_per_learning_hour: {per_hour!r} (= total_eur / (learning_seconds / 3600))")
        else:
            lines.append("- eur_per_learning_hour: n/a (learning_seconds == 0)")
        lines += ["", "| stage | rows | quantity | cost_usd | cost_eur | unpriced_rows |", "|---|---|---|---|---|---|"]
        if summary["groups"]:
            for g in summary["groups"]:
                lines.append(
                    f"| {g['key']} | {g['rows']} | {g['quantity']!r} | {g['cost_usd']!r} | {g['cost_eur']!r} | {g['unpriced_rows']} |"
                )
        else:
            lines.append("| (no ledger rows in window) | 0 | 0 | 0 | 0 | 0 |")
    lines.append("")
    if cf is None:
        lines.append("- ledger.counterfactual FAILED — see errors below.")
    else:
        lines.append(f"- query: `{Q_COUNTERFACTUAL}`")
        lines.append(
            f"- actual_eur: {cf['actual_eur']!r} · counterfactual_eur: {cf['counterfactual_eur']!r} · "
            f"saving_eur: {cf['saving_eur']!r} · rows: {cf['rows']}"
        )
        lines.append(f"- mapping: `{cf['mapping']}`")

    lines += ["", "### Learner data queries", ""]
    lines.append(f"- sessions per skill: `{Q_SESSIONS}` → {sessions if sessions is not None else 'FAILED'}")
    lines.append(f"- rubric totals: `{Q_RUBRICS}` → {rubrics if rubrics is not None else 'FAILED'}")
    lines.append(f"- error tags: `{Q_TAGS}` → {tags if tags is not None else 'FAILED'}")
    lines.append(
        f"- pending human checks (all time): `{Q_PENDING}` → {len(pending) if pending is not None else 'FAILED'}"
    )

    lines += ["", "### Evals", ""]
    if ev is None:
        lines.append("- eval report scan FAILED — see errors below.")
    elif not ev["found"]:
        lines.append("- no eval report found in evals/reports/ — run `uv run python evals/run_evals.py`.")
    else:
        lines.append(
            f"- latest report: `{ev['file']}` ({ev['title']}), backend `{ev['backend']}`, cost {ev['cost_eur']} €"
        )
        for name, res in ev["suites"].items():
            lines.append(f"  - {name}: {res}")
        lines.append(f"- regression rule (MAE Δ ≤ {MAE_REGRESSION_LIMIT} per criterion): {ev['regression_verdict']}")

    lines += ["", "## Needs a human", ""]
    if pending:
        lines.append(f"- [ ] {len(pending)} result(s) awaiting a human label (spot_check / needs_review):")
        for p in pending[:20]:
            lines.append(f"  - `{p['id']}` {p['skill']}/{p['task_type']} — {p['routing']} — {p['created_at']}")
        if len(pending) > 20:
            lines.append(f"  - … and {len(pending) - 20} more")
    elif pending is not None:
        lines.append("- [x] no spot-checks or needs_review results waiting")
    ub = d["unverified_blueprints"]
    if ub is None:
        lines.append("- [ ] unverified blueprints: query FAILED")
    elif ub:
        lines.append(
            f"- [ ] unverified blueprints ({len(ub)}): {', '.join(ub)} — check against source_url, set verified_on"
        )
    else:
        lines.append("- [x] all blueprints verified")
    tv = d["todo_verify"]
    if tv is None:
        lines.append("- [ ] `# TODO(verify)` count: scan FAILED")
    else:
        n, files = tv
        lines.append(
            f"- [{'x' if n == 0 else ' '}] `# TODO(verify)` markers: {n}" + (f" — {', '.join(files)}" if files else "")
        )
    if ev and ev.get("found") and ev["regression_verdict"].startswith("FAILED"):
        lines.append("- [ ] eval regression rule FAILED — add a note to the prompt version or roll back")
    if d["errors"]:
        lines += ["", "### Failed queries (figures above marked accordingly; nothing was invented)", ""]
        lines.extend(f"- {e}" for e in d["errors"])
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- writing


def digest_path(reports_dir: Path, now: datetime) -> Path:
    """``digest-<YYYY-WW>.md``; never overwrite — ``-2``, ``-3`` … when the name is taken."""
    iso = now.isocalendar()
    stem = f"digest-{iso.year}-{iso.week:02d}"
    candidate = reports_dir / f"{stem}.md"
    n = 2
    while candidate.exists():
        candidate = reports_dir / f"{stem}-{n}.md"
        n += 1
    return candidate


def write_digest(reports_dir: Path, now: datetime | None = None, days: int = WINDOW_DAYS, root: Path = ROOT) -> Path:
    now = now or datetime.now(UTC)
    reports_dir.mkdir(parents=True, exist_ok=True)
    text = render(collect(now, days=days, root=root))
    out = digest_path(reports_dir, now)
    out.write_text(text, encoding="utf-8")
    return out


def demo_data() -> None:
    """A little activity through the service layer (fake backends only)."""
    from app.core.db import ensure_default_learner
    from app.services import assessment, tutor

    lid = ensure_default_learner()
    s = tutor.create_session(lid, kind="tutor")
    tutor.turn(s["session_id"], text="Ich habe morgen ein Gespräch über mein Gehalt. Wie fange ich an?")
    tutor.turn(s["session_id"], text="Ich möchte 10 Prozent mehr verlangen, aber höflich bleiben.")
    tutor.end_session(s["session_id"])
    assessment.assess_writing(
        "Ich bin der Auffassung, dass Homeoffice die Produktivität steigert, weil weniger Zeit im Verkehr verloren geht. "
        "Allerdings fehlt manchen Menschen der direkte Austausch mit Kolleginnen und Kollegen.",
        task_text="Nehmen Sie Stellung: Sollten Unternehmen Homeoffice dauerhaft anbieten?",
        learner_id=lid,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wochenbericht erzeugen (nur Datei unter reports/)")
    parser.add_argument("--out", default=str(ROOT / "reports"), help="output directory (default: ./reports)")
    parser.add_argument("--days", type=int, default=WINDOW_DAYS, help=f"window in days (default {WINDOW_DAYS})")
    parser.add_argument("--demo", action="store_true", help="create a little demo activity first (fake backends only)")
    args = parser.parse_args(argv)

    from app.core.config import get_settings
    from app.core.db import init_db

    init_db()
    if args.demo:
        if get_settings().llm_backend != "fake":
            print("--demo only with LLM_BACKEND=fake (would spend real money)", file=sys.stderr)
            return 2
        demo_data()
    out = write_digest(Path(args.out))
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
