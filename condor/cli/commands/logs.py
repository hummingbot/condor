"""``condor logs`` — tail the server's rotating log file.

``condor serve`` writes ``store/logs/condor.log`` (as well as stdout), so a
model-switch or crash cause is settleable after the fact (incident 2).
"""

import time

import typer

from condor.cli.commands.serve import LOG_FILE
from condor.cli.output import ExitCode, echo, emit, fail, json_option


def _tail_lines(lines: int) -> list[str]:
    with open(LOG_FILE, "r", errors="replace") as f:
        return [line.rstrip("\n") for line in f][-lines:]


def logs(
    lines: int = typer.Option(
        200, "--lines", "-n", help="Number of trailing lines to show."
    ),
    follow: bool = typer.Option(
        False, "--follow", "-f", help="Stream new lines until interrupted (Ctrl-C)."
    ),
    as_json: bool = json_option(),
) -> None:
    """Show recent server log output (-f to follow live).

    Note for agents: ``-f`` runs until interrupted — bound it (e.g. a
    timeout) rather than awaiting forever.
    """
    if as_json and follow:
        fail(
            "--json is a snapshot format; it cannot be combined with --follow",
            ExitCode.CONFIG_ERROR,
        )
    if not LOG_FILE.exists():
        fail(
            f"no log file at {LOG_FILE} yet — it appears once `condor serve` runs",
            ExitCode.NOT_FOUND,
        )

    tail = _tail_lines(lines)

    if not follow:
        if as_json:
            emit({"file": str(LOG_FILE), "lines": tail}, "", True)
        else:
            for line in tail:
                echo(line)
        return

    for line in tail:
        echo(line)
    with open(LOG_FILE, "r", errors="replace") as f:
        f.seek(0, 2)  # end of file
        try:
            while True:
                line = f.readline()
                if line:
                    echo(line.rstrip("\n"))
                else:
                    time.sleep(0.5)
        except KeyboardInterrupt:
            return
