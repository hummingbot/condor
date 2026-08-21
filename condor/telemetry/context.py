"""The install context: who is reporting, not who is using.

Everything here describes the *deployment* — a random id, a git sha, a platform
triple, and a handful of counts and booleans. It is computed once per process
and sent once per batch rather than once per event, which is both cheaper and a
smaller surface to audit.

What is deliberately not here: hostname, IP, MAC, username, home directory,
timezone, locale, server names or URLs, and any count that could be traced to a
person. ``user_count`` is a number, never a list.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger(__name__)

_REPO = Path(__file__).resolve().parent.parent.parent
_started_at = time.monotonic()

_app: dict | None = None


def uptime_h() -> float:
    return round((time.monotonic() - _started_at) / 3600.0, 3)


def _git(*args: str) -> str:
    """A short, bounded git read. Returns '' rather than raising, ever."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(_REPO),
            capture_output=True,
            text=True,
            timeout=3,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""


def _in_docker() -> bool:
    try:
        if Path("/.dockerenv").exists():
            return True
        cgroup = Path("/proc/self/cgroup")
        return cgroup.exists() and "docker" in cgroup.read_text()
    except Exception:
        return False


def app() -> dict:
    """Version and platform. Computed once — the git calls are not free."""
    global _app
    if _app is None:
        # An install deployed from a tarball rather than a clone reports
        # "unknown", which the collector treats as its own bucket.
        _app = {
            "version": _git("rev-parse", "--short", "HEAD") or "unknown",
            "branch": _git("rev-parse", "--abbrev-ref", "HEAD") or "unknown",
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "os": platform.system().lower(),
            "arch": platform.machine().lower(),
            "in_docker": _in_docker(),
        }
    return dict(_app)


def version() -> str:
    return app()["version"]


def _llm_providers() -> list[str]:
    """Which provider *slots* are configured. Names only — never a key, never a
    prefix of a key, never its length."""
    found = []
    for name, var in (
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("google", "GEMINI_API_KEY"),
        ("groq", "GROQ_API_KEY"),
        ("deepseek", "DEEPSEEK_API_KEY"),
    ):
        if os.environ.get(var, "").strip():
            found.append(name)
    return found


def _mode() -> str:
    """``telegram`` | ``local``. Which surface this install actually runs.

    One of two fixed names, never a token, a chat id or a host — it answers
    "how many installs run Condor without Telegram at all?", which is the whole
    point of local mode and was previously invisible.
    """
    try:
        from utils.config import CONDOR_MODE

        return CONDOR_MODE
    except Exception:
        return "unknown"


def config_shape() -> dict:
    """Counts and capability flags. Every field is a bool, an int, or a fixed name."""
    shape: dict = {
        "mode": _mode(),
        "has_web": True,
        "has_gateway": False,
        "has_hb_api": False,
        "llm_providers": _llm_providers(),
        "user_count": 0,
        "server_count": 0,
        "agent_count": 0,
    }
    try:
        from condor.telemetry.consent import _cm

        cm = _cm()
        if cm is not None:
            servers = cm.list_servers() or {}
            shape["server_count"] = len(servers)
            shape["has_hb_api"] = bool(servers)
            shape["user_count"] = len(cm.get_all_users() or [])
    except Exception:
        log.debug("Telemetry could not read config shape", exc_info=True)
    try:
        agents_dir = _REPO / "agents"
        shape["agent_count"] = sum(
            1 for p in agents_dir.iterdir() if (p / "AGENT.md").is_file()
        )
    except Exception:
        pass
    try:
        shape["has_gateway"] = bool(os.environ.get("GATEWAY_URL", "").strip())
    except Exception:
        pass
    return shape


def envelope(events: list[dict], dropped: int, level: str) -> dict:
    """The wire format. One context, many events, one idempotency key each."""
    from condor.telemetry import consent

    return {
        "schema": 1,
        "install_id": consent.install_id(),
        "level": level,
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "app": app(),
        "config": config_shape(),
        "dropped": dropped,
        "events": events,
    }
