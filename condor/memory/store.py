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

    {assistant_home}/store/user_{user_id}/
        MEMORY.md            # injectable index: one line per memory
        memories/
            <slug>.md        # one fact per file (frontmatter + body)
        audit.log            # JSONL append-only of every write/delete
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .paths import store_root

_VALID_TYPES = ("preference", "fact", "feedback", "reference")


def _slugify(name: str) -> str:
    """Convert a memory name to a filesystem-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s-]+", "_", s)
    return s.strip("_") or "memory"


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse YAML frontmatter and markdown body (mirrors strategy.py)."""
    text = text.strip()
    if not text.startswith("---"):
        return {}, text

    end = text.find("---", 3)
    if end == -1:
        return {}, text

    frontmatter_str = text[3:end].strip()
    body = text[end + 3 :].strip()

    try:
        meta = yaml.safe_load(frontmatter_str) or {}
    except yaml.YAMLError:
        meta = {}

    return meta, body


def _render(meta: dict, body: str) -> str:
    """Render YAML frontmatter + markdown body."""
    frontmatter = yaml.dump(
        meta, default_flow_style=False, allow_unicode=True, sort_keys=False
    ).strip()
    return f"---\n{frontmatter}\n---\n\n{body}\n"


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
            existing_meta, _ = _parse_frontmatter(path.read_text())
            created = existing_meta.get("created", created)

        meta = {
            "name": slug,
            "description": description.strip().replace("\n", " "),
            "type": type,
            "created": created,
            "source": source,
        }
        self._atomic_write(path, _render(meta, content.strip()))
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
        _, body = _parse_frontmatter(path.read_text())
        return body

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword/substring search over name + description + body.

        This is the single seam for upgrading to semantic retrieval later
        without changing any caller.
        """
        q = (query or "").lower().strip()
        results: list[dict] = []
        for meta, body in self._iter_memories():
            haystack = (
                f"{meta.get('name', '')} {meta.get('description', '')} {body}".lower()
            )
            if not q or q in haystack:
                results.append(
                    {
                        "name": meta.get("name", ""),
                        "description": meta.get("description", ""),
                        "type": meta.get("type", "fact"),
                        "body": body,
                    }
                )
            if len(results) >= limit:
                break
        return results

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
        meta, _ = _parse_frontmatter(path.read_text())
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
                meta, body = _parse_frontmatter(f.read_text())
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
        self._atomic_write(self.index_file, "\n".join(lines) + "\n")

    def _append_audit(
        self, action: str, target: str, summary: str, source: str
    ) -> None:
        append_audit(self.audit_file, action, target, summary, source)

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Write atomically (tmp file + os.replace) within the same dir."""
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
