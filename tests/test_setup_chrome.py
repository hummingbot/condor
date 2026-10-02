"""`make install` must not be able to hang on an optional renderer (V11).

The bare `python -c "import kaleido; kaleido.get_chrome_sync()"` this replaces
had no timeout and sent stderr to /dev/null. Measured when it stalled: 256 KB
of a ~150 MB archive after thirty minutes, connection open and idle, the only
line on screen "Setting up Chrome for chart rendering...". The install could
not finish and nothing said why.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from condor import setup_chrome


def test_an_already_installed_chrome_costs_no_download(tmp_path, monkeypatch):
    """Re-running `make install` must not re-fetch 150 MB every time."""
    exe = tmp_path / "chrome"
    exe.write_text("x")
    monkeypatch.setattr(setup_chrome, "chrome_path", lambda: exe)

    def no_popen(*a, **kw):  # pragma: no cover - reached only on regression
        raise AssertionError("an installed browser must not be downloaded again")

    monkeypatch.setattr(setup_chrome.subprocess, "Popen", no_popen)

    ok, message = setup_chrome.fetch()
    assert ok is True
    assert "Already installed" in message


def test_an_unsupported_platform_is_skipped_not_attempted(monkeypatch):
    monkeypatch.setattr(setup_chrome, "chrome_path", lambda: None)
    ok, message = setup_chrome.fetch()
    assert ok is False
    assert "no Chrome-for-Testing build" in message


class _FakeProc:
    """A download that never finishes and never writes another byte."""

    returncode = None
    stdout = None

    def __init__(self):
        self.killed = False

    def poll(self):
        return None if not self.killed else -9

    def kill(self):
        self.killed = True
        self.returncode = -9

    def wait(self):
        return self.returncode


def _run_with(monkeypatch, tmp_path, proc, sizes):
    """Drive `fetch` over a scripted size sequence with time under our control."""
    monkeypatch.setattr(setup_chrome, "chrome_path", lambda: tmp_path / "absent")
    monkeypatch.setattr(setup_chrome, "_download_root", lambda: tmp_path)
    monkeypatch.setattr(setup_chrome.subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(setup_chrome.time, "sleep", lambda _s: None)

    clock = {"t": 0.0}
    seq = list(sizes)

    def fake_monotonic():
        return clock["t"]

    def fake_bytes(_root):
        clock["t"] += setup_chrome._POLL
        return seq.pop(0) if seq else (seq_last[0] if seq_last else 0)

    seq_last = [sizes[-1]] if sizes else [0]
    monkeypatch.setattr(setup_chrome.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(setup_chrome, "downloaded_bytes", fake_bytes)
    return setup_chrome.fetch(timeout=10_000, stall=30)


def test_a_download_that_stops_moving_is_given_up_on(tmp_path, monkeypatch, capsys):
    """The exact failure seen: bytes arrive, then stop, and nothing ever ends."""
    proc = _FakeProc()
    ok, message = _run_with(monkeypatch, tmp_path, proc, [0, 262144] + [262144] * 60)

    assert ok is False
    assert proc.killed is True, "the child was left running"
    assert "Stalled" in message and "0.3 MB" in message
    assert "make setup-chrome" in message, "the remedy has to be nameable"


def test_a_slow_but_moving_download_is_not_called_stalled(tmp_path, monkeypatch):
    """A cold fetch over a slow link must not be mistaken for a wedged one."""
    proc = _FakeProc()
    growing = [i * 500_000 for i in range(200)]
    monkeypatch.setattr(setup_chrome, "chrome_path", lambda: tmp_path / "absent")
    monkeypatch.setattr(setup_chrome, "_download_root", lambda: tmp_path)
    monkeypatch.setattr(setup_chrome.subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(setup_chrome.time, "sleep", lambda _s: None)
    clock = {"t": 0.0}
    seq = list(growing)
    monkeypatch.setattr(setup_chrome.time, "monotonic", lambda: clock["t"])

    def sizes(_root):
        clock["t"] += setup_chrome._POLL
        return seq.pop(0) if seq else growing[-1]

    monkeypatch.setattr(setup_chrome, "downloaded_bytes", sizes)

    ok, message = setup_chrome.fetch(timeout=120, stall=30)
    assert "Stalled" not in message, message
    assert ok is False and "Gave up after 120s" in message
    assert "CONDOR_CHROME_TIMEOUT" in message, "say which knob to turn"


def test_main_never_fails_the_install(monkeypatch, capsys):
    """Charts are optional; `make install` must survive every outcome."""
    monkeypatch.setattr(
        setup_chrome, "fetch", lambda *a, **kw: (False, "Stalled: nothing arrived.")
    )
    assert setup_chrome.main() == 0
    assert "Stalled" in capsys.readouterr().out


def test_downloaded_bytes_tolerates_a_missing_directory(tmp_path):
    assert setup_chrome.downloaded_bytes(tmp_path / "nope") == 0
    (tmp_path / "a").write_bytes(b"x" * 10)
    assert setup_chrome.downloaded_bytes(tmp_path) == 10


def test_no_size_signal_means_no_stall_verdict(tmp_path, monkeypatch):
    """Without a download root the counter is pinned at 0 forever.

    `last_change` then never moves and the stall branch fires on a download
    that is progressing fine — killing it and blaming a proxy.
    """
    proc = _FakeProc()
    monkeypatch.setattr(setup_chrome, "chrome_path", lambda: tmp_path / "absent")
    monkeypatch.setattr(setup_chrome, "_download_root", lambda: None)
    monkeypatch.setattr(setup_chrome.subprocess, "Popen", lambda *a, **kw: proc)
    monkeypatch.setattr(setup_chrome.time, "sleep", lambda _s: None)
    clock = {"t": 0.0}

    def monotonic():
        clock["t"] += setup_chrome._POLL
        return clock["t"]

    monkeypatch.setattr(setup_chrome.time, "monotonic", monotonic)

    ok, message = setup_chrome.fetch(timeout=60, stall=10)

    assert ok is False
    assert "Stalled" not in message, message
    assert "Gave up after 60s" in message, "the ceiling should be what stops it"
