"""The log survives the tmux pane, and secrets do not survive the log (OP-05)."""

import faulthandler
import logging
from logging.handlers import RotatingFileHandler

import pytest

from condor import logging_setup, paths
from condor.logging_setup import RedactingFormatter, redact, setup_file_logging

# Built at runtime: a literal in this shape trips secret scanners on push.
FAKE_BOT_TOKEN = "123456789" + ":" + "AAH" + "x" * 32


@pytest.fixture(autouse=True)
def _detach():
    root = logging.getLogger()
    formatters = [(h, h.formatter) for h in root.handlers]
    logging_setup._reset_for_tests()
    yield
    logging_setup._reset_for_tests()
    for handler, formatter in formatters:
        handler.setFormatter(formatter)


def test_a_rotating_file_handler_is_configured_under_the_runtime_root():
    log_file = setup_file_logging()

    assert log_file == paths.runtime_root() / "logs" / "condor.log"
    handlers = [
        h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)
    ]
    assert len(handlers) == 1
    assert handlers[0].maxBytes > 0 and handlers[0].backupCount > 0

    logging.getLogger("condor.test").warning("written to disk")
    handlers[0].flush()
    assert "written to disk" in log_file.read_text()


def test_setup_is_idempotent():
    first = setup_file_logging()
    second = setup_file_logging()
    assert first == second
    handlers = [
        h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)
    ]
    assert len(handlers) == 1


def test_faulthandler_writes_to_a_file_beside_the_log():
    log_file = setup_file_logging()
    assert faulthandler.is_enabled()
    assert (log_file.parent / "faulthandler.log").exists()


def test_an_unwritable_log_directory_does_not_stop_the_boot(monkeypatch, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("")
    monkeypatch.setattr(logging_setup, "logs_dir", lambda: blocker / "logs")
    assert setup_file_logging() is None


def test_a_bot_token_never_reaches_the_file():
    log_file = setup_file_logging()
    log = logging.getLogger("condor.test")
    log.error("GET https://api.telegram.org/bot%s/getUpdates failed", FAKE_BOT_TOKEN)
    try:
        raise RuntimeError(f"bad url https://api.telegram.org/bot{FAKE_BOT_TOKEN}/x")
    except RuntimeError:
        log.exception("request blew up")
    for handler in logging.getLogger().handlers:
        handler.flush()

    text = log_file.read_text()
    assert "getUpdates failed" in text and "request blew up" in text
    assert FAKE_BOT_TOKEN not in text
    assert FAKE_BOT_TOKEN.split(":")[1] not in text


@pytest.mark.parametrize(
    "line, secret",
    [
        ("login password=hunter2 ok", "hunter2"),
        ("server {'username': 'admin', 'password': 'hunter2'}", "hunter2"),
        ('body {"api_secret": "s3cr3tvalue", "pair": "SOL-USDC"}', "s3cr3tvalue"),
        ("connecting to http://admin:hunter2@localhost:8000/x", "hunter2"),
        ("header Authorization: Bearer abcdef123456ghijkl", "abcdef123456ghijkl"),
        ("hub_token: tok_live_abcdef", "tok_live_abcdef"),
        ("web_jwt_secret=deadbeefcafe", "deadbeefcafe"),
    ],
)
def test_secret_shapes_are_redacted(line, secret):
    out = redact(line)
    assert secret not in out
    assert "[REDACTED]" in out


@pytest.mark.parametrize(
    "line",
    [
        "swap token=SOL amount=1.5 on jupiter",
        "tokens: ['SOL', 'USDC'] for pool 8sLbNZoA1cfnvMJLPfp98ZLAnFSYCFApfJKMbiXNLwxj",
        "executor 7f3a closed at 12:34:56 pnl=$1,204.50",
        "Dashboard on http://127.0.0.1:8080 — reachable on the tailnet only.",
    ],
)
def test_ordinary_trading_lines_are_left_alone(line):
    assert redact(line) == line


def test_the_formatter_redacts_the_traceback_too():
    formatter = RedactingFormatter("%(message)s")
    try:
        raise ValueError(f"token {FAKE_BOT_TOKEN} rejected")
    except ValueError:
        import sys

        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "failed", None, sys.exc_info()
        )
    assert FAKE_BOT_TOKEN not in formatter.format(record)


def test_apscheduler_is_quieted_by_main():
    import main  # noqa: F401 - the level is set at import

    assert logging.getLogger("apscheduler").level == logging.WARNING
