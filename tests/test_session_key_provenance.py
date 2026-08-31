"""The seat's session key reaches the tool that reports back to it.

A delegation's outcome is supposed to land in the conversation that asked for
it. The chain is long and every link but one was covered: the chat agent calls
``delegate``; the condor MCP subprocess posts its ``session_key``; the route
resolves that to a conversation id; the record carries it; and the completion is
written into that conversation and pushed to the open tab.

The uncovered link was the first one -- *how the subprocess learns its key*.
It was handed only ``CONDOR_SESSION_KEY``, set on the ACP subprocess's
environment. But the MCP server is a grandchild: the bridge spawns it through
the MCP SDK, which gives a stdio server the ``env`` from its own config rather
than this process's environment. So the key arrived empty, the route resolved no
conversation, and the record was written with neither -- leaving the outcome
with the bell as its only surface and nothing in the chat that asked.

Every other identity value already travelled on argv for exactly that reason
(SEC-180). These tests pin that the key does too, end to end: from the spawner
through to the value ``mcp_servers.condor.settings`` parses with no environment
at all.
"""

import subprocess
import sys
from pathlib import Path

from condor.runtime import toolsets

REPO = Path(__file__).resolve().parent.parent


def _arg(args: list[str], flag: str) -> str | None:
    return args[args.index(flag) + 1] if flag in args else None


def test_the_condor_subprocess_is_told_which_seat_it_is_sitting_in():
    args = toolsets._condor_mcp_args(42, 42, "condor", session_key="web:42:conv-a")
    assert _arg(args, "--session-key") == "web:42:conv-a"


def test_a_seat_with_no_conversation_behind_it_says_so():
    """A consult, a delegate worker and a tick have no key, and must not fake one."""
    args = toolsets._condor_mcp_args(42, 42, "condor")
    assert "--session-key" not in args


def test_the_binding_carries_the_key_down_to_the_toolset(monkeypatch):
    """`_spawn_session` knows the raw key; `binding.resolve` is how it gets there."""
    from condor.runtime import binding
    from condor.runtime.keys import SessionKey
    from condor.runtime.models import SessionSpec

    seen: dict = {}

    def fake_build(*args, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr(
        "condor.runtime.toolsets.build_mcp_servers_for_session", fake_build
    )
    binding.resolve(
        SessionSpec(
            key=str(SessionKey.web(42, "conv-a")),
            user_id=42,
            chat_id=42,
            agent_key="claude-code",
        ),
        None,
        session_key="web:42:conv-a",
    )
    assert seen.get("session_key") == "web:42:conv-a"


def test_the_subprocess_reads_the_key_off_argv_with_no_environment():
    """The whole point: no CONDOR_SESSION_KEY in the environment, key still known.

    Run out-of-process because ``mcp_servers.condor.settings`` parses argv at
    import; a monkeypatched ``sys.argv`` in this process would only prove the
    parser works, not that the value survives a real spawn.
    """
    args = toolsets._condor_mcp_args(42, 42, "condor", session_key="tg:4242")
    code = (
        "from mcp_servers.condor.settings import settings;"
        "print(settings.session_key)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        cwd=REPO,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": str(REPO), "HOME": str(REPO)},
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "tg:4242"
