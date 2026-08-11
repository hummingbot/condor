"""Every knob the collector reads from its environment, in one place.

The house pattern (``utils/config.py``) is a module of module-level reads rather
than env lookups scattered through the code, so that what a deployment can
change is one file long. These are read per call rather than at import, because
the test suite sets them with ``monkeypatch.setenv``.
"""

from __future__ import annotations

import os

DEFAULT_DSN = "sqlite:///telemetry.db"
DEFAULT_RETENTION_DAYS = 90
DEFAULT_ROLLUP_INTERVAL_S = 6 * 3600


def dsn() -> str:
    """Where events go. A Postgres URL in production, a SQLite path otherwise."""
    return os.environ.get("TELEMETRY_DSN", "").strip() or DEFAULT_DSN


def trust_proxy() -> bool:
    """Whether ``X-Forwarded-For`` may be believed.

    Off unless an operator says otherwise, because the header is written by the
    caller: trusting it on a directly-exposed service hands every client a
    one-header rate-limit bypass.
    """
    return os.environ.get("TELEMETRY_TRUSTED_PROXY", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def retention_days() -> int:
    """How long raw events live. Rollups are permanent regardless."""
    try:
        return max(1, int(os.environ.get("TELEMETRY_RETENTION_DAYS", "").strip()))
    except ValueError:
        return DEFAULT_RETENTION_DAYS


def rollup_interval_s() -> int:
    try:
        return max(60, int(os.environ.get("TELEMETRY_ROLLUP_INTERVAL_S", "").strip()))
    except ValueError:
        return DEFAULT_ROLLUP_INTERVAL_S
