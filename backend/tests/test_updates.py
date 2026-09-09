import hashlib
import json
import zipfile

import pytest
from app.services import updates
from lernapp_launcher import update_worker as worker


def bundle(tmp_path, version="1.2.3", extra=None):
    content = {"pyproject.toml": f'[project]\nversion="{version}"\n'.encode(), "uv.lock": b"lock"}
    if extra:
        content.update(extra)
    path = tmp_path / "update.zip"
    with zipfile.ZipFile(path, "w") as z:
        for k, v in content.items():
            z.writestr(k, v)
        z.writestr(
            "SOURCE_MANIFEST.json",
            json.dumps({"version": version, "files": {k: hashlib.sha256(v).hexdigest() for k, v in content.items()}}),
        )
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("name", ["../escape", "/absolute", "data/pg/file", ".env", "a\\b", "C:bad"])
def test_update_archive_rejects_unsafe_entries(tmp_path, name):
    archive, digest = bundle(tmp_path, extra={name: b"bad"})
    with pytest.raises(ValueError):
        worker.unpack(archive, tmp_path / "out", "1.2.3", digest)
    assert not (tmp_path / "out").exists()


def test_update_archive_integrity_and_version(tmp_path):
    archive, digest = bundle(tmp_path)
    with pytest.raises(ValueError):
        worker.unpack(archive, tmp_path / "bad", "1.2.3", "0" * 64)
    with pytest.raises(ValueError):
        worker.unpack(archive, tmp_path / "bad", "1.2.4", digest)
    worker.unpack(archive, tmp_path / "good", "1.2.3", digest)
    assert (tmp_path / "good/uv.lock").read_bytes() == b"lock"


def test_release_metadata_validated_and_cached(monkeypatch):
    monkeypatch.setattr(updates, "_cache", {})
    monkeypatch.setattr(updates, "_checked", 0)
    monkeypatch.setattr(updates, "current_version", lambda: "1.0.0")
    monkeypatch.setattr(updates, "supported", lambda: True)
    calls = []
    metadata = {
        "tag_name": "v1.2.3",
        "assets": [
            {
                "name": updates.ASSET,
                "digest": "sha256:" + "a" * 64,
                "size": 100,
                "browser_download_url": f"https://github.com/{updates.REPOSITORY}/releases/download/v1.2.3/{updates.ASSET}",
            }
        ],
    }

    class Response:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return metadata

    monkeypatch.setattr(updates.httpx, "get", lambda *a, **k: calls.append(a) or Response())
    assert updates.check(True)["available"]
    assert updates.check()["available"] and len(calls) == 1
    metadata["assets"][0]["browser_download_url"] = "https://evil.example/payload"
    assert not updates.check(True)["available"]


def test_failed_install_restores_source_database_and_settings(tmp_path, monkeypatch):
    data = tmp_path / "local"
    app = data / "app"
    home = data / "updates"
    app.mkdir(parents=True)
    home.mkdir()
    (app / "old.txt").write_text("original app")
    (data / "pg").mkdir()
    (data / "pg/rows").write_text("learner progress")
    (data / ".env").write_text("private settings")
    archive, digest = bundle(tmp_path)
    request = home / "request.json"
    request.write_text(
        json.dumps(
            {
                "app": str(app),
                "data": str(data),
                "archive": str(archive),
                "version": "1.2.3",
                "sha256": digest,
                "uv": "uv",
            }
        )
    )
    monkeypatch.setattr(worker, "stop", lambda *a: None)
    starts = []
    monkeypatch.setattr(worker, "start", lambda *a: starts.append(a))

    def fail(*a, **kw):
        (data / "pg/rows").write_text("failed migration")
        (data / ".env").write_text("changed settings")
        raise RuntimeError("dependency failure")

    monkeypatch.setattr(worker.subprocess, "run", fail)
    worker.run(request)
    assert (app / "old.txt").read_text() == "original app"
    assert (data / "pg/rows").read_text() == "learner progress"
    assert (data / ".env").read_text() == "private settings"
    assert len(starts) == 1
    assert json.loads((home / "status.json").read_text())["phase"] == "failed"


def test_update_refuses_database_backup_while_running(tmp_path, monkeypatch):
    root = tmp_path / "app"
    root.mkdir()
    data = tmp_path
    (data / "pg").mkdir()
    (data / "pg/postmaster.pid").write_text("123")
    monkeypatch.setattr(worker.subprocess, "run", lambda *a, **k: None)
    with pytest.raises(RuntimeError, match="still running"):
        worker.stop(root, data)


def test_background_check_starts_on_freshly_booted_laptop(monkeypatch):
    from app.services import updates

    started = []

    class Thread:
        def __init__(self, **kwargs):
            pass

        def start(self):
            started.append(True)

    monkeypatch.setattr(updates, "_cache", {})
    monkeypatch.setattr(updates, "_checked", 0)
    monkeypatch.setattr(updates, "supported", lambda: True)
    monkeypatch.setattr(updates, "job_status", lambda: {})
    monkeypatch.setattr(updates.time, "monotonic", lambda: 60)
    monkeypatch.setattr(updates.threading, "Thread", Thread)
    updates.cached_status()
    assert started == [True]
