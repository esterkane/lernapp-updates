"""Weekly digest (skill ``weekly-digest``): summary file only, ledger figures quoted verbatim."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.core import ledger
from app.core.db import ensure_default_learner
from app.services import assessment, tutor

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import weekly_digest  # noqa: E402


@pytest.fixture()
def some_activity() -> str:
    lid = ensure_default_learner()
    s = tutor.create_session(lid, kind="tutor")
    tutor.turn(s["session_id"], text="Ich möchte über meine Gehaltsverhandlung sprechen.")
    tutor.end_session(s["session_id"])
    assessment.assess_writing(
        "Ich bin der Meinung, dass Homeoffice sinnvoll ist.", task_text="Homeoffice?", learner_id=lid
    )
    return lid


def test_digest_file_written_with_verbatim_ledger_total(tmp_path: Path, some_activity: str) -> None:
    now = datetime.now(UTC) + timedelta(seconds=1)
    out = weekly_digest.write_digest(tmp_path, now=now)
    assert out.exists() and out.parent == tmp_path
    assert out.name == f"digest-{now.isocalendar().year}-{now.isocalendar().week:02d}.md"
    text = out.read_text(encoding="utf-8")

    expected = ledger.summary(from_=now - timedelta(days=weekly_digest.WINDOW_DAYS), to=now, group_by="stage")
    assert "Kosten" in text
    assert f"total_eur: {expected['total_eur']!r}" in text
    assert 'ledger.summary(from_=…, to=…, group_by="stage")' in text
    assert "ledger.counterfactual(" in text
    assert "GET /costs/summary" in text
    # learner section (German) + ops section (English) + human list
    assert "## Lernfortschritt" in text and "## Ops" in text and "Needs a human" in text
    assert "schreiben_v1" in text  # rubric totals by rubric_version
    assert "TODO(verify)" in text


def test_digest_never_overwrites(tmp_path: Path, some_activity: str) -> None:
    now = datetime.now(UTC) + timedelta(seconds=1)
    first = weekly_digest.write_digest(tmp_path, now=now)
    second = weekly_digest.write_digest(tmp_path, now=now)
    third = weekly_digest.write_digest(tmp_path, now=now)
    assert first != second != third
    assert second.name.endswith("-2.md") and third.name.endswith("-3.md")
    assert first.exists() and second.exists() and third.exists()


def test_digest_reports_empty_sources_honestly(tmp_path: Path) -> None:
    far_past = datetime(2000, 1, 8, tzinfo=UTC)
    out = weekly_digest.write_digest(tmp_path, now=far_past)
    text = out.read_text(encoding="utf-8")
    assert "keine Sitzungen" in text
    assert "total_eur: 0.0" in text
