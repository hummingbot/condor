"""``condor`` CLI entrypoint — set up, run & control, observe & maintain Condor.

Modeled on ``hbot`` (docs/cli-plan.md): designed to be driven
non-interactively by agentic harnesses. Every command emits compact Markdown
and returns a stable exit code (``condor.cli.output.ExitCode``); run/observe
commands also take ``--json`` for machine-readable output. The CLI is thin
views over the append-only files and the control socket — it owns no
configuration, no cache, no daemon of its own. File-backed commands work with
the server down; socket-backed ones exit 3 (NOT_RUNNING).
"""

import os
import subprocess
from importlib import metadata
from typing import List, Optional

import typer

from condor.cli.commands import accounts as accounts_cmd
from condor.cli.commands import agents as agents_cmd
from condor.cli.commands import doctor as doctor_cmd
from condor.cli.commands import init as init_cmd
from condor.cli.commands import logs as logs_cmd
from condor.cli.commands import memory as memory_cmd
from condor.cli.commands import performance as performance_cmd
from condor.cli.commands import reports as reports_cmd
from condor.cli.commands import routines as routines_cmd
from condor.cli.commands import runs as runs_cmd
from condor.cli.commands import serve as serve_cmd
from condor.cli.commands import start as start_cmd
from condor.cli.commands import status as status_cmd
from condor.cli.commands import stop as stop_cmd
from condor.cli.commands import update as update_cmd
from condor.cli.commands._common import REPO_ROOT
from condor.cli.output import SortedCommandsGroup

app = typer.Typer(
    name="condor",
    cls=SortedCommandsGroup,
    no_args_is_help=True,
    add_completion=False,
    help="Run, control, and observe Condor (one operator, one process, loopback-only).",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version() -> str:
    try:
        version = metadata.version("condor")
    except metadata.PackageNotFoundError:
        version = "unknown"
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except Exception:
        rev = ""
    return f"{version} (git {rev})" if rev else version


@app.callback(invoke_without_command=True)
def _root(
    version: Optional[bool] = typer.Option(
        None, "--version", help="Show the Condor version and exit.", is_eager=True
    ),
) -> None:
    if version:
        typer.echo(f"condor {_version()}")
        raise typer.Exit()
    # The whole app resolves config.yml, .env and store/ relative to the
    # repo root — anchor there no matter where the CLI is invoked from.
    os.chdir(REPO_ROOT)


SET_UP = "set up"
RUN_CONTROL = "run & control"
OBSERVE = "observe & maintain"

# Flat surface, alphabetical in --help (SortedCommandsGroup); grouped into
# hbot's ontology via help panels. Detail lives in `condor <command> -h`.
app.command("init", rich_help_panel=SET_UP)(init_cmd.init)
app.command("update", rich_help_panel=SET_UP)(update_cmd.update)
app.command("accounts", rich_help_panel=SET_UP)(accounts_cmd.accounts)

app.command("serve", rich_help_panel=RUN_CONTROL)(serve_cmd.serve)
app.command("start", rich_help_panel=RUN_CONTROL)(start_cmd.start)
app.command("stop", rich_help_panel=RUN_CONTROL)(stop_cmd.stop)

app.command("status", rich_help_panel=OBSERVE)(status_cmd.status)
app.command("doctor", rich_help_panel=OBSERVE)(doctor_cmd.doctor)
app.command("runs", rich_help_panel=OBSERVE)(runs_cmd.runs)
app.command("logs", rich_help_panel=OBSERVE)(logs_cmd.logs)
app.command("agents", rich_help_panel=OBSERVE)(agents_cmd.agents)
app.command("performance", rich_help_panel=OBSERVE)(performance_cmd.performance)
app.command("reports", rich_help_panel=OBSERVE)(reports_cmd.reports)
app.command("routines", rich_help_panel=OBSERVE)(routines_cmd.routines)
app.command("memory", rich_help_panel=OBSERVE)(memory_cmd.memory)


def main(argv: Optional[List[str]] = None) -> None:
    """Console-script entrypoint (``condor``); tests call ``main([...])``.

    Success returns normally (the process exits 0); failures propagate their
    stable-exit-code SystemExit.
    """
    try:
        app(args=argv)
    except SystemExit as e:
        if e.code not in (0, None):
            raise


if __name__ == "__main__":
    main()
