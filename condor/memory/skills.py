"""User skill store — hybrid *playbooks* keyed by ``(assistant, user_id)``.

A skill is a markdown *playbook*: know-how the agent can follow (when to apply +
steps), optionally **referencing** an existing Condor routine for the executable
part. This mirrors :class:`condor.memory.store.MemoryStore` 1:1 — one file per
skill (YAML frontmatter + markdown body) plus a small ``SKILLS.md`` index that is
cheap to inject into a prompt. The body is read on-demand via
:meth:`SkillStore.read`, which also validates ``references_routine`` so the agent
never invokes a routine that no longer exists.

Like the memory store this is pure filesystem logic with **no** MCP/Telegram
dependencies, so it runs from the main process (prompt injection) and from the
MCP subprocess (the ``manage_skill`` tool) alike. It shares the per-(assistant, user)
``audit.log`` with the memory store (target ``skill:<slug>``).

Layout on disk (extends the memory layout, same per-assistant root)::

    {assistant_home}/store/user_{user_id}/
        skills/
            SKILLS.md            # injectable index: one line per skill
            <slug>/
                SKILL.md         # frontmatter + steps
        audit.log                # SHARED with MemoryStore (target skill:<slug>)
"""

from __future__ import annotations

from pathlib import Path

from .paths import store_root
from .store import _parse_frontmatter, _render, _slugify, _utcnow, append_audit


def _routine_exists(name: str) -> bool:
    """True if ``name`` is a discoverable global routine.

    Validated against the global routine registry only — agent-local routines
    live under a strategy dir the store has no handle to, so a reference to one
    simply reports ``routine_ok=false`` here (advisory; never fatal).
    """
    try:
        from routines.base import discover_routines

        return name in discover_routines(force_reload=False)
    except Exception:
        return False


class SkillStore:
    """Per-assistant, per-user skill store.

    Keyed by ``(agent_slug, user_id)`` (FEAT-003), mirroring
    :class:`condor.memory.store.MemoryStore`: the root is resolved by
    :func:`condor.memory.paths.store_root` and the per-(assistant, user)
    ``audit.log`` is shared with the memory store.
    """

    def __init__(self, user_id: int, agent_slug: str | None = None):
        self.user_id = user_id
        self.root = store_root(user_id, agent_slug)
        self.skills_dir = self.root / "skills"
        self.index_file = self.skills_dir / "SKILLS.md"
        self.audit_file = self.root / "audit.log"  # shared with MemoryStore

    # -- public API --------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        when_to_use: str,
        body: str,
        references_routine: str | None = None,
        source: str = "chat",
    ) -> dict:
        """Create or overwrite a skill, then reindex and audit."""
        if not name or not description or not when_to_use or not body:
            return {"error": "name, description, when_to_use and body are required"}

        slug = _slugify(name)
        skill_dir = self.skills_dir / slug
        path = skill_dir / "SKILL.md"

        # Preserve original created date on overwrite.
        created = _utcnow()
        if path.exists():
            existing_meta, _ = _parse_frontmatter(path.read_text())
            created = existing_meta.get("created", created)

        meta = {
            "name": slug,
            "description": description.strip().replace("\n", " "),
            "when_to_use": when_to_use.strip().replace("\n", " "),
            "created": created,
            "source": source,
        }
        ref = (references_routine or "").strip()
        if ref:
            meta["references_routine"] = ref

        self._atomic_write(path, _render(meta, body.strip()))
        self._reindex()
        append_audit(
            self.audit_file, "write", f"skill:{slug}", meta["description"], source
        )
        result = {
            "saved": True,
            "name": slug,
            "description": meta["description"],
            "when_to_use": meta["when_to_use"],
        }
        if ref:
            result["references_routine"] = ref
            result["routine_ok"] = _routine_exists(ref)
        return result

    def read(self, name: str) -> dict | None:
        """Return a skill's frontmatter + body, or ``None`` if absent.

        Validates ``references_routine`` against the routine registry and
        surfaces ``routine_ok`` so the agent won't invoke a broken reference.
        """
        path = self.skills_dir / _slugify(name) / "SKILL.md"
        if not path.exists():
            return None
        meta, body = _parse_frontmatter(path.read_text())
        ref = meta.get("references_routine")
        result = {
            "name": meta.get("name", _slugify(name)),
            "description": meta.get("description", ""),
            "when_to_use": meta.get("when_to_use", ""),
            "body": body,
        }
        if ref:
            result["references_routine"] = ref
            result["routine_ok"] = _routine_exists(ref)
        return result

    def search(self, query: str, limit: int = 10) -> list[dict]:
        """Keyword/substring search over name + when_to_use + description + body.

        Single seam for upgrading to semantic retrieval later without changing
        any caller (mirrors :meth:`MemoryStore.search`).
        """
        q = (query or "").lower().strip()
        results: list[dict] = []
        for meta, body in self._iter_skills():
            haystack = (
                f"{meta.get('name', '')} {meta.get('when_to_use', '')} "
                f"{meta.get('description', '')} {body}"
            ).lower()
            if not q or q in haystack:
                ref = meta.get("references_routine")
                hit = {
                    "name": meta.get("name", ""),
                    "description": meta.get("description", ""),
                    "when_to_use": meta.get("when_to_use", ""),
                    "body": body,
                }
                if ref:
                    hit["references_routine"] = ref
                    hit["routine_ok"] = _routine_exists(ref)
                results.append(hit)
            if len(results) >= limit:
                break
        return results

    def list_index(self) -> str:
        """Return the contents of ``SKILLS.md`` (for prompt injection).

        Empty string when the user has no skills yet, so callers inject nothing
        and avoid noise for new users.
        """
        if not self.index_file.exists():
            # Self-heal: rebuild from disk if skills exist but the index is gone.
            if self.skills_dir.exists() and any(self.skills_dir.glob("*/SKILL.md")):
                self._reindex()
            else:
                return ""
        return self.index_file.read_text().strip()

    def edit(self, name: str, source: str = "chat", **fields) -> dict:
        """Patch one or more fields of a skill, preserving the rest.

        Accepts ``description``, ``when_to_use``, ``body``, ``references_routine``
        (pass ``references_routine=""`` to clear the reference).
        """
        slug = _slugify(name)
        path = self.skills_dir / slug / "SKILL.md"
        if not path.exists():
            return {"error": f"Skill '{name}' not found"}

        meta, body = _parse_frontmatter(path.read_text())
        if "description" in fields and fields["description"]:
            meta["description"] = fields["description"].strip().replace("\n", " ")
        if "when_to_use" in fields and fields["when_to_use"]:
            meta["when_to_use"] = fields["when_to_use"].strip().replace("\n", " ")
        if "references_routine" in fields and fields["references_routine"] is not None:
            ref = fields["references_routine"].strip()
            if ref:
                meta["references_routine"] = ref
            else:
                meta.pop("references_routine", None)
        if "body" in fields and fields["body"]:
            body = fields["body"].strip()

        self._atomic_write(path, _render(meta, body))
        self._reindex()
        append_audit(
            self.audit_file,
            "write",
            f"skill:{slug}",
            meta.get("description", ""),
            source,
        )
        return self.read(slug) or {"saved": True, "name": slug}

    def delete(self, name: str, source: str = "user") -> bool:
        """Delete a skill (and its folder), then reindex and audit."""
        slug = _slugify(name)
        skill_dir = self.skills_dir / slug
        path = skill_dir / "SKILL.md"
        if not path.exists():
            return False
        meta, _ = _parse_frontmatter(path.read_text())
        path.unlink()
        # Remove the now-empty skill folder (ignore if other files were added).
        try:
            skill_dir.rmdir()
        except OSError:
            pass
        self._reindex()
        append_audit(
            self.audit_file,
            "delete",
            f"skill:{slug}",
            meta.get("description", ""),
            source,
        )
        return True

    # -- internals ---------------------------------------------------------

    def _iter_skills(self):
        """Yield (meta, body) for every skill, sorted by created date."""
        if not self.skills_dir.exists():
            return
        files = sorted(self.skills_dir.glob("*/SKILL.md"))
        parsed = []
        for f in files:
            try:
                meta, body = _parse_frontmatter(f.read_text())
            except Exception:
                continue
            meta.setdefault("name", f.parent.name)
            parsed.append((meta, body))
        parsed.sort(key=lambda mb: mb[0].get("created", ""))
        yield from parsed

    def _reindex(self) -> None:
        """Regenerate SKILLS.md from the skill files on disk.

        Rebuilds from the source of truth (not an in-memory cache) so concurrent
        writers each reconstruct the full index from what is actually on disk.
        """
        lines = ["# User Skills Index", ""]
        count = 0
        for meta, _ in self._iter_skills():
            name = meta.get("name", "")
            when = meta.get("when_to_use", "")
            ref = meta.get("references_routine")
            line = f"- [{name}] {when}"
            if ref:
                line += f"  (→ routine: {ref})"
            lines.append(line)
            count += 1
        if count == 0:
            # Nothing to index — drop a stale index so list_index() returns "".
            if self.index_file.exists():
                self.index_file.unlink()
            return
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        self._atomic_write(self.index_file, "\n".join(lines) + "\n")

    @staticmethod
    def _atomic_write(path: Path, text: str) -> None:
        """Write atomically (tmp file + os.replace) within the same dir."""
        import os

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
