"""Fetch the headless Chrome kaleido renders charts with, and give up if it hangs.

``make install`` used to run this as a bare ``python -c "import kaleido;
kaleido.get_chrome_sync()"`` with stderr sent to ``/dev/null``. Three things
went wrong at once when the download stalled: there was no timeout, so it
waited for ever; there was no progress, so nothing distinguished "slow" from
"wedged"; and the only line on screen was ``Setting up Chrome for chart
rendering...``, indefinitely. Measured in that state: 256 KB of a ~150 MB
archive after thirty minutes, the connection open and idle. The install could
not finish and nothing said why.

Everything here is best effort. Chrome is for chart *images* -- the dashboard,
the trading loops and the update flow all work without it -- so no outcome
below is fatal and the exit status is always zero. What changes is that the
operator is told which of the three things happened.

The stall detector matters more than the ceiling. A cold download on a slow
link can legitimately run for minutes, so a timeout generous enough not to cut
that off is also generous enough to sit on a dead connection for just as long.
Bytes arriving is the signal that separates them.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

#: Overall ceiling. Generous: this is a ~150 MB archive and some hosts are far
#: from the CDN. The stall detector is what catches a wedged download early.
TIMEOUT = int(os.environ.get("CONDOR_CHROME_TIMEOUT", "900"))
#: How long the archive may not grow before we call it stuck.
STALL = int(os.environ.get("CONDOR_CHROME_STALL", "90"))
_POLL = 2.0
_REPORT_EVERY = 15.0


def chrome_path() -> Path | None:
    """Where kaleido expects the browser, or ``None`` on an unsupported arch."""
    try:
        from choreographer.cli._cli_utils import get_chrome_download_path

        return get_chrome_download_path()
    except Exception:  # noqa: BLE001 - an import failure is "cannot tell"
        return None


def downloaded_bytes(root: Path) -> int:
    """How much of the archive has landed so far. Zero when nothing has."""
    try:
        return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    except OSError:
        return 0


def _download_root() -> Path | None:
    try:
        from choreographer.cli._cli_utils import default_download_path

        return Path(default_download_path)
    except Exception:  # noqa: BLE001
        return None


def _mb(n: int) -> str:
    return f"{n / 1_000_000:.1f} MB"


def fetch(timeout: int = TIMEOUT, stall: int = STALL) -> tuple[bool, str]:
    """Download Chrome, reporting progress and refusing to wait for ever.

    Returns ``(ok, message)``. The download runs in a child process because
    ``get_chrome_sync`` is a blocking call with no timeout of its own: killing
    the child is the only way to stop waiting on it.
    """
    installed = chrome_path()
    if installed is None:
        return False, "This platform has no Chrome-for-Testing build; skipped."
    if installed.exists():
        return True, f"Already installed at {installed}."

    root = _download_root()
    proc = subprocess.Popen(
        [sys.executable, "-c", "import kaleido; kaleido.get_chrome_sync()"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    started = last_change = last_report = time.monotonic()
    seen = downloaded_bytes(root) if root else 0

    while proc.poll() is None:
        time.sleep(_POLL)
        now = time.monotonic()
        current = downloaded_bytes(root) if root else 0
        if current != seen:
            seen, last_change = current, now
        if now - last_report >= _REPORT_EVERY and seen:
            print(f"  … {_mb(seen)} downloaded", flush=True)
            last_report = now
        if now - last_change >= stall:
            proc.kill()
            proc.wait()
            return False, (
                f"Stalled: {_mb(seen)} downloaded, then nothing for {stall}s. "
                "The connection is open but idle — usually a proxy or a "
                "firewall holding the CDN. Charts render as tables until this "
                "succeeds; retry with `make setup-chrome`."
            )
        if now - started >= timeout:
            proc.kill()
            proc.wait()
            return False, (
                f"Gave up after {timeout}s with {_mb(seen)} downloaded. It was "
                "still making progress, so the link is just slow: raise the "
                "ceiling with `CONDOR_CHROME_TIMEOUT=1800 make setup-chrome`."
            )

    output = (proc.stdout.read() if proc.stdout else "") or ""
    if proc.returncode == 0:
        return True, f"Installed at {installed}."
    tail = "\n".join(output.strip().splitlines()[-5:])
    return False, (
        f"Chrome could not be installed (exit {proc.returncode}). Charts render "
        "as tables until it is; retry with `make setup-chrome`."
        + (f"\n{tail}" if tail else "")
    )


def main() -> int:
    """Always zero: an optional renderer must never fail an install."""
    ok, message = fetch()
    print(f"  {'✓' if ok else '!'} {message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
