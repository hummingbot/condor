"""User memory store — one fact per file, keyed by ``(assistant, user_id)``.

Replicates the Claude Code / Hummingbot memory pattern that already lives in
this repo: a file per fact (YAML frontmatter + markdown body) plus a small
``MEMORY.md`` index that is cheap to inject into a prompt. The body is read
on-demand via :meth:`MemoryStore.read`.

This module is pure filesystem logic with **no** MCP/Telegram dependencies so
it can run from the main process (prompt injection) and from the MCP
subprocess (the ``manage_memory`` tool) alike.

Each store lives under its assistant's home, keyed by ``user_id`` (FEAT-003);
:func:`condor.memory.paths.store_root` resolves the root. Layout on disk::

    {agent_home}/store/user_{user_id}/
        MEMORY.md            # injectable index: one line per memory
        memories/
            <slug>.md        # one fact per file (frontmatter + body)
        audit.log            # JSONL append-only of every write/delete
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from condor.frontmatter import parse_frontmatter, render_frontmatter, slugify
from condor.fsutil import atomic_write_text

from .paths import store_root

_VALID_TYPES = ("preference", "fact", "feedback", "reference")

# Cap audit.log to the newest N entries (mirrors config.yml's audit_log cap of
# 500, per CLAUDE.md). FEAT-003 creates one store per (assistant, user), so an
# uncapped append-only log would grow without bound on every write/delete.
_AUDIT_CAP = 500


def _slugify(name: str) -> str:
    """Slug with this store's historical ``"memory"`` fallback.

    Thin binding over :func:`condor.frontmatter.slugify` (ARCH-202) so
    memories and skills alike keep resolving empty/all-punctuation names to
    the same on-disk slug they always have.
    """
    return slugify(name, fallback="memory")


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_audit(
    audit_file: Path, action: str, target: str, summary: str, source: str
) -> None:
    """Append one JSONL entry to a shared ``audit.log``.

    Free function (not a method) so every per-assistant store — memories and
    skills alike — writes the same format to the *same* file. ``target`` is
    namespaced by caller
    (``memory:<slug>`` / ``skill:<slug>``) so ``/memory`` can tell them apart.
    """
    audit_file.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": _utcnow(),
        "source": source,
        "action": action,
        "target": target,
        "summary": (summary or "")[:200],
    }
    with audit_file.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    _trim_audit(audit_file)


def _atomic_write(path: Path, text: str) -> None:
    """Write atomically (tmp file + os.replace) within the same dir.

    Free function (not a method) so every per-assistant store — memories and
    skills alike — shares one implementation instead of a verbatim copy. The
    temp file is unique per writer, so two processes writing the same slug
    concurrently never share — and thus never tear — it; ``os.replace`` then
    publishes each writer's complete file atomically (FEAT-003 runs the same
    store from the main process and the MCP subprocess).

    Thin alias over :func:`condor.fsutil.atomic_write_text`, which is the one
    implementation of this dance in the codebase (ARCH-148).
    """
    atomic_write_text(path, text)


def _trim_audit(audit_file: Path, cap: int | None = None) -> None:
    """Bound ``audit.log`` to its newest ``cap`` entries.

    Only rewrites once the file exceeds ``2 * cap`` lines, so the trim cost is
    amortized away across appends instead of paid on every write. The rewrite
    is atomic (tmp file + ``os.replace``) so a concurrent reader/writer never
    sees a torn file — the same discipline :func:`_atomic_write` uses for
    memory files (FEAT-003 runs the same store from two processes).
    """
    if cap is None:
        cap = _AUDIT_CAP
    try:
        with audit_file.open("r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return
    if len(lines) <= cap * 2:
        return
    tail = lines[-cap:]
    _atomic_write(audit_file, "".join(tail))


# -- search helpers (shared with SkillStore) -----------------------------------

SNIPPET_CHARS = 200

# A term is a run of Unicode letters or digits — not ``[a-z0-9]``, which
# dropped every other script: "风险" tokenized to nothing and, read as "no
# query", matched everything. Underscore still splits ("routine_cookbook" is
# found by "cookbook"), as it did when only ASCII was kept.
_TERM = re.compile(r"[^\W_]+")
# Scripts written without spaces between words (Thai, CJK ideographs, kana,
# Hangul syllables): a "word" there is a whole clause, so their terms match
# anywhere inside one rather than only at its start.
_UNSPACED = re.compile(
    "[\u0e00-\u0e7f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    "\uac00-\ud7af\uf900-\ufaff]"
)


def query_terms(query: str | None) -> list[str]:
    """The distinct lowercase letter/digit terms of a query, in order."""
    return list(dict.fromkeys(_TERM.findall((query or "").lower())))


def is_blank_query(query: str | None) -> bool:
    """True for a query that asks for everything: missing or whitespace only.

    Distinct from a query that yields no :func:`query_terms` ("—", "!!!"):
    that one asked for something, so it must match nothing rather than
    everything and push the real hit past the result limit.
    """
    return not (query or "").strip()


def _starts_a_word(term: str, words: list[str]) -> bool:
    if _UNSPACED.search(term):
        return any(term in word for word in words)
    return any(word.startswith(term) for word in words)


def match_score(
    terms: list[str], weighted_fields: tuple[tuple[str, int], ...]
) -> tuple[int, int] | None:
    """Rank key for one document, or ``None`` when no term appears in it.

    ``(terms matched, sum of each matched term's best field weight)`` — so a
    document matching more of the query always outranks one matching less, and
    among equals a hit in the name beats one buried in the body.

    A term matches a word it *starts* ("binan" finds "binance"), not any
    substring: split queries produce short terms, and "re" from "re-centre"
    would otherwise match every "whenever" and "already". The exception is a
    term in a script written without spaces, where a "word" is a whole clause:
    "风险" matches anywhere in "控制风险管理".
    """
    fields = [
        (_TERM.findall((text or "").lower()), weight)
        for text, weight in weighted_fields
    ]
    matched = total = 0
    for term in terms:
        best = max(
            (weight for words, weight in fields if _starts_a_word(term, words)),
            default=0,
        )
        if best:
            matched += 1
            total += best
    return (matched, total) if matched else None


def snippet(body: str, terms: list[str], width: int = SNIPPET_CHARS) -> str:
    """About ``width`` characters of ``body``, around the first matched term."""
    text = " ".join((body or "").split())
    if len(text) <= width:
        return text
    lowered = text.lower()
    found = [
        re.search(
            (
                re.escape(term)
                if _UNSPACED.search(term)
                else rf"(?<![^\W_]){re.escape(term)}"
            ),
            lowered,
        )
        for term in terms
    ]
    positions = [match.start() for match in found if match]
    start = max(min(positions) - width // 4, 0) if positions else 0
    start = min(start, len(text) - width)
    piece = text[start : start + width]
    return ("…" if start else "") + piece + ("…" if start + width < len(text) else "")


class MemoryStore:
    """Per-assistant, per-user memory store.

    Keyed by ``(agent_slug, user_id)`` (FEAT-003): ``agent_slug`` set selects a
    trading agent's store, ``None`` the chat ``condor`` store. The root is
    resolved by :func:`condor.memory.paths.store_root`; the rest of this class
    (index, audit, atomic write, self-heal) is unchanged.
    """

    def __init__(self, user_id: int, agent_slug: str | None = None):
        self.user_id = user_id
        self.root = store_root(user_id, agent_slug)
        self.memories_dir = self.root / "memories"
        self.index_file = self.root / "MEMORY.md"
        self.audit_file = self.root / "audit.log"

    # -- public API --------------------------------------------------------

    def write(
        self,
        name: str,
        content: str,
        description: str,
        type: str = "fact",
        source: str = "chat",
    ) -> dict:
        """Create or overwrite a memory, then reindex and audit.

        Returns the saved record metadata. ``type`` is validated against the
        taxonomy; unknown values fall back to ``"fact"``.
        """
        if not name or not content or not description:
            return {"error": "name, content and description are required"}

        if type not in _VALID_TYPES:
            type = "fact"

        slug = _slugify(name)
        self.memories_dir.mkdir(parents=True, exist_ok=True)

        # Preserve original created date on overwrite.
        path = self.memories_dir / f"{slug}.md"
        existed = path.exists()
        created = _utcnow()
        if existed:
            existing_meta, _ = parse_frontmatter(path.read_text())
            created = existing_meta.get("created", created)

        meta = {
            "name": slug,
            "description": description.strip().replace("\n", " "),
            "type": type,
            "created": created,
            "source": source,
        }
        _atomic_write(path, render_frontmatter(meta, content.strip()))
        self._reindex()
        action = "update" if existed else "create"
        self._append_audit(action, f"memory:{slug}", meta["description"], source)
        return {
            "saved": True,
            "name": slug,
            "type": type,
            "description": meta["description"],
        }

    def read(self, name: str) -> str | None:
        """Return the full body of a memory, or ``None`` if absent."""
        path = self.memories_dir / f"{_slugify(name)}.md"
        if not path.exists():
            return None
        _, body = parse_frontmatter(path.read_text())
        return body

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Ranked keyword search over name + description + body.

        This is the single seam for upgrading to semantic retrieval later
        without changing any caller. See :meth:`search_with_total` for the
        matching rules; this is the same page without the count.
        """
        return self.search_with_total(query, limit)[0]

    def search_with_total(self, query: str, limit: int = 10) -> tuple[list[dict], int]:
        """The best ``limit`` matches and how many memories matched in all.

        The query is split into terms and a memory matches when any of them
        appears — "sol usdc slippage" used to be one substring, so it found
        nothing unless that exact phrase was stored, and the agent concluded
        there was no memory (ST-12). Ranked by how many terms matched, then by
        where (name > description > body), then newest first: the cut at
        ``limit`` used to fall on the newest memories. A blank query lists
        everything, newest first; a non-blank one with no letters or digits
        matches nothing.

        Hits carry a ~200-character ``snippet``, not the body — :meth:`read`
        is how a caller pays for the one it wants.
        """
        terms = query_terms(query)
        everything = is_blank_query(query)
        ranked = []
        # Oldest-first from _iter_memories; reversed so the stable sort below
        # leaves ties newest-first.
        for meta, body in reversed(list(self._iter_memories())):
            name = meta.get("name", "")
            description = meta.get("description", "")
            score = match_score(terms, ((name, 3), (description, 2), (body, 1)))
            if not everything and score is None:
                continue
            ranked.append(
                (
                    score or (0, 0),
                    {
                        "name": name,
                        "description": description,
                        "type": meta.get("type", "fact"),
                        "snippet": snippet(body, terms),
                    },
                )
            )
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [hit for _, hit in ranked[: max(limit, 0)]], len(ranked)

    def catalog(self) -> list[dict]:
        """Every memory's metadata, oldest first — no bodies.

        The structured twin of :meth:`list_index`, which returns the same set as
        one markdown blob for the prompt. Bodies stay behind :meth:`read` so a
        reader pays for the one memory they opened, not for all of them.
        """
        return [
            {
                "name": meta.get("name", ""),
                "description": meta.get("description", ""),
                "type": meta.get("type", "fact"),
                "created": meta.get("created", ""),
                "source": meta.get("source", ""),
            }
            for meta, _ in self._iter_memories()
        ]

    def list_index(self) -> str:
        """Return the contents of ``MEMORY.md`` (for prompt injection).

        Empty string when the user has no memories yet (so callers can inject
        nothing and avoid noise for new users).
        """
        if not self.index_file.exists():
            # Self-heal: rebuild from disk if memories exist but index is missing.
            if self.memories_dir.exists() and any(self.memories_dir.glob("*.md")):
                self._reindex()
            else:
                return ""
        return self.index_file.read_text().strip()

    def delete(self, name: str, source: str = "user") -> bool:
        """Delete a memory, then reindex and audit. Returns False if absent."""
        slug = _slugify(name)
        path = self.memories_dir / f"{slug}.md"
        if not path.exists():
            return False
        meta, _ = parse_frontmatter(path.read_text())
        path.unlink()
        self._reindex()
        self._append_audit(
            "delete", f"memory:{slug}", meta.get("description", ""), source
        )
        return True

    def audit(self, limit: int = 30) -> list[dict]:
        """Return the most recent audit entries (newest last)."""
        if not self.audit_file.exists():
            return []
        lines = self.audit_file.read_text().splitlines()
        entries: list[dict] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries

    # -- internals ---------------------------------------------------------

    def _iter_memories(self):
        """Yield (meta, body) for every memory, sorted by created date."""
        if not self.memories_dir.exists():
            return
        files = sorted(self.memories_dir.glob("*.md"))
        parsed = []
        for f in files:
            try:
                meta, body = parse_frontmatter(f.read_text())
            except Exception:
                continue
            meta.setdefault("name", f.stem)
            parsed.append((meta, body))
        parsed.sort(key=lambda mb: mb[0].get("created", ""))
        yield from parsed

    def _reindex(self) -> None:
        """Regenerate MEMORY.md from the memory files on disk.

        Rebuilding from the source of truth (not from an in-memory cache) makes
        the index robust against concurrent writers: each writer reconstructs
        the full index from what is actually on disk.
        """
        lines = ["# User Memory Index", ""]
        count = 0
        for meta, _ in self._iter_memories():
            name = meta.get("name", "")
            desc = meta.get("description", "")
            mtype = meta.get("type", "fact")
            lines.append(f"- [{name}] {desc} · {mtype}")
            count += 1
        if count == 0:
            # Nothing to index — remove a stale index so list_index() returns "".
            if self.index_file.exists():
                self.index_file.unlink()
            return
        self.root.mkdir(parents=True, exist_ok=True)
        _atomic_write(self.index_file, "\n".join(lines) + "\n")

    def _append_audit(
        self, action: str, target: str, summary: str, source: str
    ) -> None:
        append_audit(self.audit_file, action, target, summary, source)
