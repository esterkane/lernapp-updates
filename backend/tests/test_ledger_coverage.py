"""ADR-0006: every adapter call writes a ledger row (fake backends, real pricing)."""

from __future__ import annotations

from pathlib import Path

from app.core import ledger
from app.services.embeddings import get_embeddings
from app.services.fakes import silent_wav
from app.services.llm import complete, stream
from app.services.schemas import PronunciationTip
from app.services.stt import get_stt
from app.services.tts import get_tts

from tests.conftest import ledger_count


def test_llm_complete_writes_rows() -> None:
    before = ledger_count()
    out = complete(
        "conversation",
        [{"role": "user", "content": "Hallo"}],
        prompt_version="1.0.0",
        session_id="t-llm",
        learner_id="default",
    )
    assert isinstance(out, str) and out
    assert ledger_count() - before == 2  # tokens_in + tokens_out


def test_llm_schema_writes_rows() -> None:
    before = ledger_count()
    tip = complete(
        "conversation",
        [{"role": "user", "content": "Bericht"}],
        PronunciationTip,
        prompt_version="1.0.0",
        session_id="t-llm2",
        learner_id="default",
    )
    assert tip.tip_de
    assert ledger_count() - before >= 2


def test_llm_stream_writes_rows() -> None:
    before = ledger_count()
    text = "".join(
        stream(
            "conversation",
            [{"role": "user", "content": "Hallo"}],
            prompt_version="1.0.0",
            session_id="t-stream",
            learner_id="default",
        )
    )
    assert text
    assert ledger_count() - before == 2


def test_stt_writes_row(tmp_path: Path) -> None:
    wav = tmp_path / "a.wav"
    wav.write_bytes(silent_wav(2.0))
    before = ledger_count()
    t = get_stt().transcribe(wav, session_id="t-stt", learner_id="default")
    assert t.text
    rows = ledger.rows_for_session("t-stt")
    assert ledger_count() - before == 1
    assert rows[-1]["stage"] == "stt" and rows[-1]["unit"] == "audio_seconds" and rows[-1]["local"] is True
    assert abs(rows[-1]["quantity"] - 2.0) < 0.05


def test_tts_writes_row() -> None:
    before = ledger_count()
    a = get_tts().synthesize("Guten Tag, wie geht es Ihnen?", session_id="t-tts", learner_id="default")
    assert a.data
    rows = ledger.rows_for_session("t-tts")
    assert ledger_count() - before == 1
    assert rows[-1]["unit"] == "chars" and rows[-1]["quantity"] == len("Guten Tag, wie geht es Ihnen?")


def test_embeddings_write_row() -> None:
    before = ledger_count()
    vecs = get_embeddings().embed(["Verhandlung", "Prüfung"], session_id="t-emb", learner_id="default")
    assert len(vecs) == 2
    assert ledger_count() - before == 1


def test_meter_writes_on_exception() -> None:
    before = ledger_count()
    try:
        with ledger.meter("tts", "google", "google/wavenet-de", "chars", session_id="t-exc") as m:
            m.quantity = 10
            raise RuntimeError("provider down")
    except RuntimeError:
        pass
    assert ledger_count() - before == 1
    row = ledger.rows_for_session("t-exc")[-1]
    assert row["cost_usd"] is not None and row["cost_usd"] > 0


def test_unknown_model_is_unpriced_not_lost() -> None:
    before = ledger_count()
    with ledger.meter("tts", "acme", "acme/unknown-voice", "chars", session_id="t-unpriced") as m:
        m.quantity = 100
    assert ledger_count() - before == 1
    row = ledger.rows_for_session("t-unpriced")[-1]
    assert row["cost_usd"] is None
    s = ledger.summary(group_by="stage")
    assert s["unpriced_rows"] >= 1


def test_counterfactual_reprices_local_rows() -> None:
    with ledger.meter("stt", "local", "local/faster-whisper", "audio_seconds", session_id="t-cf", local=True) as m:
        m.quantity = 600  # 10 minutes
    cf = ledger.counterfactual("all_cloud_openai")
    assert cf["counterfactual_usd"] > cf["actual_usd"]
    assert "stt" in cf["by_stage"]


def test_costs_api(client) -> None:  # type: ignore[no-untyped-def]
    r = client.get("/costs/summary", params={"group_by": "day"})
    assert r.status_code == 200 and "total_eur" in r.json()
    r = client.get("/costs/counterfactual", params={"variant": "cheapest_cloud"})
    assert r.status_code == 200
    r = client.get("/costs/summary", params={"group_by": "bogus"})
    assert r.status_code == 400


def test_prosody_writes_local_pron_row() -> None:
    from app.services.fakes import fake_transcript
    from app.services.pronunciation import analyze_prosody_metered

    before = ledger_count()
    report = analyze_prosody_metered(fake_transcript(12.0), session_id="t-pron", learner_id="default")
    assert report.n_words > 0
    assert ledger_count() - before == 1
    row = ledger.rows_for_session("t-pron")[-1]
    assert row["stage"] == "pron" and row["local"] is True and row["cost_usd"] == 0.0
    assert abs(row["quantity"] - 12.0) < 0.01


def test_failed_llm_call_leaves_a_row(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import app.services.llm as llm_mod
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "llm_backend", "litellm")

    def boom(*a, **k):  # type: ignore[no-untyped-def]
        raise llm_mod.LLMError("provider down")

    monkeypatch.setattr(llm_mod, "_call_litellm", boom)
    before = ledger_count()
    import pytest

    with pytest.raises(llm_mod.LLMError):
        complete(
            "conversation",
            [{"role": "user", "content": "Hallo"}],
            prompt_version="1.0.0",
            session_id="t-fail",
            learner_id="default",
        )
    assert ledger_count() - before == 1
    row = ledger.rows_for_session("t-fail")[-1]
    assert row["unit"] == "requests" and row["cost_usd"] is None
