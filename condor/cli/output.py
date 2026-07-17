"""Output helpers + the stable exit-code contract shared by all condor commands.

Adopted verbatim from ``hbot`` (docs/cli-plan.md — "contract first"): the CLI
emits a token-economic **Markdown** format by default — tables for lists of
records, key-value for single records — that serves both humans and agents.
The run/observe commands also take ``--json`` for a machine-readable object
with raw values. Either way, the machine contract for outcomes is the stable
**exit code** (branch on it), and errors go to stderr as
``Error: <message> (code N)``.
"""

import json
from enum import IntEnum
from typing import Any, List, NoReturn, Optional, Sequence

import typer
from typer.core import TyperGroup


class SortedCommandsGroup(TyperGroup):
    """A Typer group that lists its sub-commands alphabetically in --help
    instead of registration order (hbot rule 3: flat, alphabetical surface)."""

    def list_commands(self, ctx: "typer.Context") -> List[str]:
        return sorted(super().list_commands(ctx))


class ExitCode(IntEnum):
    """Stable exit codes so an agentic harness can branch on outcomes."""

    SUCCESS = 0
    ERROR = 1  # generic failure
    NOT_FOUND = 2  # target (agent/run/report/account) does not exist
    NOT_RUNNING = 3  # `condor serve` is not up (control socket unavailable)
    CONFIG_ERROR = 4  # missing/invalid config, arguments, or credentials
    TIMEOUT = 5  # operation did not complete in time


def cell(v: Any) -> str:
    """Format one value for a Markdown cell/line: compact, single-line, pipe-escaped."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:g}"
    return str(v).replace("|", "\\|").replace("\n", " ")


def render_table(
    rows: Sequence[dict],
    columns: Optional[List[str]] = None,
    title: Optional[str] = None,
) -> str:
    """Render a list of records as a Markdown table (token-economic format)."""
    head = f"## {title}\n\n" if title else ""
    rows = list(rows)
    if not rows:
        return head + "_(none)_"
    cols = columns or list(rows[0].keys())
    lines = [
        "| " + " | ".join(cols) + " |",
        "| " + " | ".join("---" for _ in cols) + " |",
    ]
    lines += ["| " + " | ".join(cell(r.get(c)) for c in cols) + " |" for r in rows]
    return head + "\n".join(lines)


def render_kv(record: dict, title: Optional[str] = None) -> str:
    """Render a single record as a Markdown key-value block."""
    head = f"## {title}\n\n" if title else ""
    if not record:
        return head + "_(empty)_"
    return head + "\n".join(f"- {k}: {cell(v)}" for k, v in record.items())


def echo(text: str) -> None:
    typer.echo(text)


def emit(payload: Any, markdown: str, as_json: bool) -> None:
    """Print ``markdown`` (the default surface) or ``payload`` as JSON when
    ``--json`` was passed. ``payload`` carries the raw values (numbers stay
    numbers; Decimals and other non-JSON types serialize via ``str``)."""
    typer.echo(json.dumps(payload, indent=2, default=str) if as_json else markdown)


def json_option() -> Any:
    """The shared ``--json`` flag declaration, so every command words it identically."""
    return typer.Option(
        False, "--json", help="Emit machine-readable JSON instead of Markdown."
    )


def fail(message: str, code: ExitCode) -> NoReturn:
    """Print an error to stderr and exit with the stable ``code``.

    Raises SystemExit (not typer.Exit) so it carries the code even when a
    helper is called directly, outside a click context (e.g. unit tests).
    """
    typer.echo(f"Error: {message} (code {int(code)})", err=True)
    raise SystemExit(int(code))
