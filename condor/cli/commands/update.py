"""``condor update`` — pull the latest Condor from the current branch + uv sync.

The CLI replacement for the retired bot-admin /update flow (wraps
utils.updater). Restart ``condor serve`` afterwards to pick up the code.
"""

import asyncio

import typer

from condor.cli.output import ExitCode, echo, emit, fail, json_option, render_kv


def update(
    check_only: bool = typer.Option(
        False,
        "--check-only",
        help="Only report whether updates are available; don't pull.",
    ),
    as_json: bool = json_option(),
) -> None:
    """Update Condor from its remote branch and sync dependencies."""
    from utils.updater import check_for_updates, install_dependencies, pull_updates

    async def run() -> None:
        info = await check_for_updates()
        if info["error"]:
            fail(info["error"], ExitCode.ERROR)
        record = {
            "branch": info["branch"],
            "local": info["local_commit"],
            "remote": info["remote_commit"],
            "behind": info["commits_behind"],
            "up_to_date": info["up_to_date"],
        }
        if info["up_to_date"] or check_only:
            emit(
                record,
                render_kv(
                    record, title="update --check-only" if check_only else "update"
                ),
                as_json,
            )
            if not info["up_to_date"] and not as_json:
                echo(f"\n{info['commit_log']}")
            return
        echo(f"{info['commits_behind']} commit(s) behind:\n{info['commit_log']}")
        ok, msg = await pull_updates()
        if not ok:
            fail(msg, ExitCode.ERROR)
        echo(msg)
        ok, msg = await install_dependencies()
        if not ok:
            fail(msg, ExitCode.ERROR)
        record["pulled"] = True
        record["note"] = "dependencies synced — restart Condor to apply: condor serve"
        emit(record, render_kv(record, title="update"), as_json)

    asyncio.run(run())
