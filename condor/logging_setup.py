"""Process logging beyond the tmux pane: a rotating file, redaction, faulthandler.

Until this module the only log was stderr in a tmux pane with bounded
scrollback, and the session — traceback included — is destroyed when the
process dies. After a crash or a money incident there was nothing to read.

Three things, all set up by :func:`setup_file_logging` from ``main()``:

* a size-rotated ``condor.log`` under ``runtime_root()/logs``;
* ``faulthandler`` pointed at a file beside it, so a hard crash (segfault,
  abort, a fatal signal) leaves the Python stacks behind;
* :class:`RedactingFormatter` on every handler, so a bot token or a password
  that reaches a log line does not reach the disk.

Deliberately not at import time: ``import main`` must not create files, and the
root it writes under is only settled once the process holds the instance lock.
"""

from __future__ import annotations

import faulthandler
import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from condor import paths

__all__ = [
    "LOG_FORMAT",
    "RedactingFormatter",
    "logs_dir",
    "redact",
    "setup_file_logging",
]

LOG_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
LOG_FILENAME = "condor.log"
FAULT_FILENAME = "faulthandler.log"
# 10 MB x 6 files: days of INFO on a busy install, bounded at ~60 MB.
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

_REDACTED = "[REDACTED]"

# Telegram bot token: <bot id>:<35-ish url-safe chars>. python-telegram-bot puts
# it in every request path, which is how it gets into exception text.
_BOT_TOKEN = re.compile(r"(?<!\d)(\d{6,12}):[A-Za-z0-9_-]{30,}")
# user:password@host in a URL.
_URL_USERINFO = re.compile(r"(://[^\s/:@]+):[^\s/@]+@")
# Authorization header values.
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE)
# password=..., "api_secret": "...", secret_key: ... — the key, then its value.
# ``token`` on its own is deliberately not a key here: in this codebase a token
# is usually a traded asset (``token=SOL``), and redacting those would gut the
# logs this exists to preserve.
_SECRET_KV = re.compile(r"""(?ix)
    (
      ["']?
      \b(?:[a-z0-9_]*(?:password|passwd|passphrase|secret)[a-z0-9_]*
         |api[_-]?key|private[_-]?key|access[_-]?token|refresh[_-]?token
         |bot[_-]?token|auth[_-]?token|hub[_-]?token|telegram[_-]?token)
      ["']?
      \s*[:=]\s*
    )
    (?:"[^"]*"|'[^']*'|[^\s,;&)}\]]+)
    """)


def redact(text: str) -> str:
    """``text`` with bot tokens, URL passwords and secret-looking values removed."""
    text = _BOT_TOKEN.sub(rf"\1:{_REDACTED}", text)
    text = _URL_USERINFO.sub(rf"\1:{_REDACTED}@", text)
    text = _BEARER.sub(rf"\1 {_REDACTED}", text)
    return _SECRET_KV.sub(rf"\1{_REDACTED}", text)


class RedactingFormatter(logging.Formatter):
    """A formatter whose output has been through :func:`redact`.

    A formatter rather than a ``logging.Filter`` because the traceback is only
    rendered here: a filter sees the message and its args, not the exception
    text, and an exception out of an HTTP client is exactly where a token-
    bearing URL turns up.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def logs_dir() -> Path:
    """Where this deployment's log files live."""
    return paths.runtime_root() / "logs"


_file_handler: RotatingFileHandler | None = None
# faulthandler writes to the descriptor, so the file object has to stay alive.
_fault_file = None


def setup_file_logging(level: int = logging.INFO) -> Path | None:
    """Attach the rotating file handler, redaction and faulthandler.

    Idempotent, and it never raises: a log directory that cannot be created
    must not stop the bot, so the failure is reported on stderr logging and the
    process carries on as it did before this existed. Returns the log file's
    path, or ``None`` if file logging could not be set up.
    """
    global _file_handler, _fault_file
    root = logging.getLogger()

    # Redact what already goes to the pane too: it is what gets pasted into an
    # issue. Only handlers still on the stock formatter class are touched.
    for handler in root.handlers:
        if type(handler.formatter) is logging.Formatter or handler.formatter is None:
            handler.setFormatter(RedactingFormatter(LOG_FORMAT))

    if _file_handler is not None:
        return Path(_file_handler.baseFilename)

    directory = logs_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
        # Owner-only at the directory: log lines carry chat ids, server names
        # and balances even after redaction.
        os.chmod(directory, 0o700)
        handler = RotatingFileHandler(
            directory / LOG_FILENAME,
            maxBytes=MAX_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "File logging is off: could not open %s (%s)", directory, exc
        )
        return None
    handler.setLevel(level)
    handler.setFormatter(RedactingFormatter(LOG_FORMAT))
    root.addHandler(handler)
    _file_handler = handler

    try:
        _fault_file = open(directory / FAULT_FILENAME, "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except (OSError, ValueError, RuntimeError) as exc:
        logging.getLogger(__name__).warning("faulthandler is off: %s", exc)

    return Path(handler.baseFilename)


def _reset_for_tests() -> None:
    """Detach what :func:`setup_file_logging` attached."""
    global _file_handler, _fault_file
    if _file_handler is not None:
        logging.getLogger().removeHandler(_file_handler)
        _file_handler.close()
        _file_handler = None
    if _fault_file is not None:
        try:
            faulthandler.disable()
        finally:
            _fault_file.close()
            _fault_file = None
