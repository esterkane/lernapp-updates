"""Closing one app window must never stop a replacement instance."""
import argparse
from types import SimpleNamespace

from lernapp_launcher import cli, desktop


def test_close_only_stops_its_own_services(monkeypatch):
    old = cli.RunState(123, 124, 125, 8000, 8501, 1.0)
    new = cli.RunState(456, 457, 458, 8000, 8501, 2.0)
    session = desktop.Session()
    session.owner = old.launcher_pid
    stopped = []
    monkeypatch.setattr(cli, '_stop_state', stopped.append)
    monkeypatch.setattr(cli, 'read_state', lambda: new)
    session.stop()
    assert stopped == []
    monkeypatch.setattr(cli, 'read_state', lambda: old)
    session.stop()
    assert stopped == [old]


def test_close_before_start_never_launches(monkeypatch):
    session = desktop.Session()
    monkeypatch.setattr(cli, 'read_state', lambda: None)
    monkeypatch.setattr(cli, 'running_state', lambda: None)
    def unexpected(*args, **kwargs):
        raise AssertionError('Cannot start services after closing')
    monkeypatch.setattr(desktop.subprocess, 'Popen', unexpected)
    session.stop()
    session.start()
    assert session.owner is None


def test_windows_start_uses_window_but_supervisor_does_not(monkeypatch):
    monkeypatch.setattr(cli, 'IS_WINDOWS', True)
    monkeypatch.setattr(desktop, 'main', lambda: 42)
    assert cli.cmd_start(argparse.Namespace(dev=False, no_browser=False)) == 42
    monkeypatch.setattr(cli, 'running_state', lambda: SimpleNamespace(api_port=1, ui_url='local'))
    monkeypatch.setattr(cli, 'health', lambda port: {'status': 'ok'})
    assert cli.cmd_start(argparse.Namespace(dev=False, no_browser=True)) == 0
