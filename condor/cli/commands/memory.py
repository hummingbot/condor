"""``condor memory`` — read-only view of the memory stores.

The bare verb prints the index (MEMORY.md); ``memory search <q>`` runs the
keyword search. Writes stay in chat (docs/cli-plan.md Tier 2).
"""

from typing import Optional

import typer

from condor.cli.output import ExitCode, echo, emit, fail, json_option


def memory(
    action: Optional[str] = typer.Argument(
        None, help="`search` to query; omit to print the index."
    ),
    query: Optional[str] = typer.Argument(None, help="Search terms (with `search`)."),
    agent: Optional[str] = typer.Option(
        None, "--agent", help="An agent's store instead of the global (chat) tier."
    ),
    limit: int = typer.Option(10, "--limit", "-n", help="Max search results."),
    as_json: bool = json_option(),
) -> None:
    """Show the memory index or search it (read-only; writes stay in chat)."""
    from condor.memory.store import MemoryStore

    store = MemoryStore(agent)

    if action is None:
        index = store.list_index()
        emit({"agent": agent, "index": index}, index or "_(empty)_", as_json)
        return

    if action != "search" or not query:
        fail(
            "usage: condor memory [search <query>] [--agent slug]",
            ExitCode.CONFIG_ERROR,
        )

    results = store.search(query, limit=limit)
    if as_json:
        emit({"agent": agent, "query": query, "results": results}, "", True)
        return
    if not results:
        echo("_(no matches)_")
        return
    for r in results:
        echo(f"## {r['name']} ({r['type']})\n{r['description']}\n\n{r['body']}\n")
