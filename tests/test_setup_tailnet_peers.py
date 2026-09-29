"""`tailnet_api_peers` in setup-environment.sh, exercised as shell.

The helper decides which tailnet node the wizard writes into config.yml as
the hummingbot-api host, and getting it wrong is not a visible failure: the
wrong node is very likely running the same software, so it answers, and the
mistake surfaces as "401 Incorrect username or password" against a password
that was never wrong.

There is no bats in this repo and no reason to add one for a single awk/grep
pipeline, so the function is sourced out of the script with a `tailscale`
stub ahead of it on PATH. That runs the real line, not a Python
reimplementation of it -- a copy of the regex in a test asserts only that the
copy matches itself.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SETUP = Path(__file__).resolve().parent.parent / "setup-environment.sh"


def run_peers(tmp_path: Path, status_output: str, want: str = "hummingbot-api") -> list[str]:
    """Return `tailnet_api_peers <want>` over a stubbed `tailscale status`."""
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir(exist_ok=True)
    stub = stub_dir / "tailscale"
    peers = tmp_path / "peers.txt"
    peers.write_text(status_output)
    stub.write_text(f'#!/bin/sh\ncat "{peers}"\n')
    stub.chmod(0o755)

    # Pull just the function out: sourcing the whole script would run an
    # installer. Everything from its definition to the closing brace.
    src = SETUP.read_text()
    start = src.index("tailnet_api_peers() {")
    end = src.index("\n}\n", start) + len("\n}\n")
    func = src[start:end]

    proc = subprocess.run(
        ["bash", "-c", f'PATH="{stub_dir}:$PATH"\n{func}\ntailnet_api_peers "{want}"'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return [ln for ln in proc.stdout.splitlines() if ln.strip()]


def status(*names: str) -> str:
    return "".join(
        f"100.64.0.{i + 1}\t{n}\tdavid@\tlinux\t-\n" for i, n in enumerate(names)
    )


# ── the suffixes Tailscale itself assigns ──────────────────────────────


def test_finds_the_unsuffixed_name(tmp_path):
    assert run_peers(tmp_path, status("hummingbot-api")) == ["hummingbot-api"]


def test_finds_numeric_collision_suffixes(tmp_path):
    out = run_peers(tmp_path, status("hummingbot-api-1", "hummingbot-api-2"))
    assert out == ["hummingbot-api-1", "hummingbot-api-2"]


# ── the suffixes an operator assigns, which is what this PR is about ───


@pytest.mark.parametrize("name", ["hummingbot-api-cornell", "hummingbot-api-eu"])
def test_finds_desk_named_nodes(tmp_path, name):
    """The regression: TAILSCALE_HOSTNAME is a setting, not only a collision."""
    assert run_peers(tmp_path, status(name)) == [name]


def test_deliberate_numeric_fleet_all_found(tmp_path):
    """A planned multi-instance deployment, the case that motivated the reword."""
    out = run_peers(
        tmp_path, status("hummingbot-api", "hummingbot-api-1", "hummingbot-api-2")
    )
    assert out == ["hummingbot-api", "hummingbot-api-1", "hummingbot-api-2"]
    assert len(out) > 1, "must reach the pick-one branch, never auto-select"


# ── names that must NOT be claimed ─────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "condor",
        "condor-hackathon",
        "condor-hackathon-server",
        "condor1",
        "hbapi",
        "my-hummingbot-api",  # suffix match, not prefix -- anchored ^
    ],
)
def test_ignores_unrelated_nodes(tmp_path, name):
    assert run_peers(tmp_path, status(name)) == []


def test_ignores_the_prefix_without_a_separator(tmp_path):
    """`hummingbot-apis` is a different word, not a suffixed hummingbot-api."""
    assert run_peers(tmp_path, status("hummingbot-apis")) == []


def test_picks_only_the_api_out_of_a_mixed_tailnet(tmp_path):
    out = run_peers(
        tmp_path,
        status(
            "condor-hackathon",
            "hummingbot-api-cornell",
            "condor1",
            "someones-laptop",
        ),
    )
    assert out == ["hummingbot-api-cornell"]


# ── shape of the output ────────────────────────────────────────────────


def test_empty_tailnet_is_empty_not_an_error(tmp_path):
    """The no-match branch must be reachable: `|| true` keeps grep's 1 quiet."""
    assert run_peers(tmp_path, "") == []


def test_reads_the_name_column_not_the_address(tmp_path):
    out = run_peers(tmp_path, status("hummingbot-api"))
    assert out == ["hummingbot-api"]
    assert not any(c.startswith("100.") for c in out)


def test_custom_want_is_honoured(tmp_path):
    """The parameter is real, even though today's only caller hardcodes it."""
    out = run_peers(tmp_path, status("condor", "condor-hackathon"), want="condor")
    assert out == ["condor", "condor-hackathon"]
