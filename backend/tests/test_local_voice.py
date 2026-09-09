import pytest
from app.services import local_voice


def test_setup_requires_explicit_opt_in(client, monkeypatch):
    called = []
    monkeypatch.setattr(local_voice, "start", lambda: called.append(True) or {"state": "installing"})
    assert client.post("/settings/local-voice", json={}).status_code == 400
    assert not called
    assert client.post("/settings/local-voice", json={"accept_optional_install": True}).status_code == 200
    assert called == [True]


def test_corrupt_download_preserves_previous_file(tmp_path, monkeypatch):
    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            yield b"corrupt"

    monkeypatch.setattr(local_voice.httpx, "stream", lambda *a, **k: Response())
    target = tmp_path / "MODEL_CARD"
    target.write_bytes(b"previous")
    with pytest.raises(ValueError, match="checksum"):
        local_voice.download_file("MODEL_CARD", target)
    assert target.read_bytes() == b"previous"
    assert list(tmp_path.iterdir()) == [target]


def test_failed_setup_has_no_raw_exception(monkeypatch):
    def fail():
        raise RuntimeError("secret-example-provider-key")

    monkeypatch.setattr(local_voice, "install", fail)
    previous = local_voice._state, local_voice._message
    try:
        local_voice._worker()
        result = local_voice.status()
        assert result["state"] == "error"
        assert "secret-example" not in str(result)
    finally:
        local_voice._state, local_voice._message = previous
