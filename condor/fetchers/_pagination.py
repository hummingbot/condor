"""One cursor-walk, shared by every paginated Hummingbot endpoint we read.

The backend paginates with an opaque cursor, and the same walk was hand-written
at five call sites before this module existed. The copies drifted: CORR-112 and
CORR-113 fixed the same three terminal conditions twice on the same day in
``fetchers/executors.py`` and ``fetchers/positions.py``, and CORR-125 then had
to add the non-advancing-cursor guard to the two copies that had never received
it. Every fix to this logic had to be applied N times, and twice it was not.

A walk ends on any of four conditions, and all four are load-bearing:

- **empty page** — nothing came back, so nothing more will.
- **no cursor** — the server says this was the last page.
- **short page** — fewer rows than the limit we asked for is the same signal,
  for servers that always echo a cursor.
- **non-advancing cursor** — the server handed back the cursor we sent it.
  Re-issuing it re-requests the identical page forever; without this guard the
  walk runs to its cap appending duplicate rows, which is silent corruption
  rather than a hang (inflated PnL, duplicated broadcasts) (CORR-125).

The cap is on *rows accumulated*, never on iterations: the four conditions above
already end a stalled walk, so the cap only exists to bound a genuinely enormous
history. Each caller keeps its own cap — they are separate budget decisions.
"""

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any


def next_cursor(result: Any) -> str | None:
    """The cursor for the following page, or ``None`` when there is none.

    Four spellings are accepted because the backend has used all four: top-level
    ``next_cursor`` or ``cursor``, then the same two nested under ``pagination``.
    Reading ``cursor`` — the field an API is most likely to echo back rather than
    advance — is why the non-advancing guard in :func:`walk_pages` exists.
    """
    if not isinstance(result, dict):
        return None
    cursor = result.get("next_cursor") or result.get("cursor")
    if not cursor:
        pagination = result.get("pagination")
        if isinstance(pagination, dict):
            cursor = pagination.get("next_cursor") or pagination.get("cursor")
    return cursor or None


async def walk_pages(
    fetch_page: Callable[..., Awaitable[Any]],
    extract_rows: Callable[[Any], list],
    *,
    page_size: int,
    max_items: int,
    first_page_size: int | None = None,
    on_truncated: Callable[[], None] | None = None,
) -> AsyncIterator[list]:
    """Yield each page of rows in turn, walking the cursor to exhaustion.

    ``fetch_page`` is called as ``fetch_page(limit=..., cursor=...)``, with
    ``cursor`` omitted entirely on the first request rather than passed as
    ``None`` — several endpoints reject an explicit null. Bind every other
    argument with :func:`functools.partial`.

    Pages are yielded *before* the terminal conditions are evaluated, so a
    consumer that renders as it goes (``ws_manager``'s progressive broadcast)
    sees the final page like any other.

    Args:
        extract_rows: Pulls the row list out of one response envelope. Its count
            is what the cap counts.
        page_size: Rows requested per page. Clamped down to what is left of the
            cap, so the walk never fetches rows it would discard.
        max_items: Stop once this many rows have been yielded.
        first_page_size: A smaller budget for the first request only, for
            consumers that want something on screen before the long pages land.
        on_truncated: Called when the *cap* is what ended the walk, as opposed to
            the history genuinely running out. Callers that must not report a
            truncated result as a complete one log from here.
    """
    fetched = 0
    page_num = 0
    cursor: str | None = None

    while True:
        remaining = max_items - fetched
        if remaining <= 0:
            if on_truncated is not None:
                on_truncated()
            return

        budget = first_page_size if (page_num == 0 and first_page_size) else page_size
        limit = min(budget, remaining)
        kwargs: dict[str, Any] = {"limit": limit}
        if cursor:
            kwargs["cursor"] = cursor

        result = await fetch_page(**kwargs)
        page = extract_rows(result)
        fetched += len(page)
        yield page

        following = next_cursor(result)
        if not page:
            return
        if not following or len(page) < limit:
            return
        if following == cursor:
            return
        cursor = following
        page_num += 1


async def collect_pages(
    fetch_page: Callable[..., Awaitable[Any]],
    extract_rows: Callable[[Any], list],
    *,
    page_size: int,
    max_items: int,
    first_page_size: int | None = None,
    on_truncated: Callable[[], None] | None = None,
) -> list:
    """Walk every page and return the accumulated rows.

    The whole-history form of :func:`walk_pages`, for callers with no per-page
    work to do.
    """
    rows: list = []
    async for page in walk_pages(
        fetch_page,
        extract_rows,
        page_size=page_size,
        max_items=max_items,
        first_page_size=first_page_size,
        on_truncated=on_truncated,
    ):
        rows.extend(page)
    return rows
