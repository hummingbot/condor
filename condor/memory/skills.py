"""Skill library — editable *playbooks* belonging to an assistant.

A skill is a markdown *playbook*: know-how the agent can follow (when to apply +
steps), optionally **referencing** an existing Condor routine for the executable
part. Skills are **general to the assistant**, not per user: a library belongs to
an assistant and is shared across everyone using it. (What is *per-user* is memory
— see :class:`condor.memory.store.MemoryStore`.)

The library is **editable at runtime**: the agent can ``read``/``search`` skills
and also ``create``/``edit``/``delete`` them via the ``manage_skill`` tool. Repo-
shipped playbooks are simply files already present in the dir; they live in the
same library and can be refined like any other (so edits are version-controlled).

Like the memory store this is pure filesystem logic with **no** MCP/Telegram
dependencies, so it runs from the main process (prompt injection) and from the
MCP subprocess (the ``manage_skill`` tool) alike.

Layout on disk — keyed by the assistant only (``agent_slug``), via
:func:`condor.memory.paths.builtin_skills_root`::

    {assistant_home}/skills/
        <slug>/
            SKILL.md         # frontmatter + steps

where ``{assistant_home}`` is ``assistants/condor`` for the chat (``agent_slug``
None) or ``agents/<slug>`` for a trading agent / domain expert.
"""

from __future__ import annotations

from .paths import builtin_skills_root
from .store import _parse_frontmatter, _render, _slugify, _utcnow


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
    """Per-assistant, editable skill library.

    Keyed by ``agent_slug`` alone (skills are general to the assistant, not
    per-user): ``None`` resolves the chat ``condor`` library, a slug resolves a
    trading agent's / expert's library. The root is :func:`builtin_skills_root`.
    """

    def __init__(self, agent_slug: str | None = None):
        self.agent_slug = agent_slug
        # The assistant's skills dir (repo-shipped + runtime-created playbooks).
        self.skills_dir = builtin_skills_root(agent_slug)

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
        """Create or overwrite a skill in this assistant's library."""
        if not self.skills_dir:
            return {"error": "this assistant has no skills library"}
        if not name or not description or not when_to_use or not body:
            return {"error": "name, description, when_to_use and body are required"}

        slug = _slugify(name)
        path = self.skills_dir / slug / "SKILL.md"

        # Preserve the original created date on overwrite.
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

    def edit(self, name: str, **fields) -> dict:
        """Patch fields of a skill, preserving the rest.

        Accepts ``description``, ``when_to_use``, ``body``, ``references_routine``
        (pass ``references_routine=""`` to clear the reference).
        """
        if not self.skills_dir:
            return {"error": "this assistant has no skills library"}
        slug = _slugify(name)
        path = self.skills_dir / slug / "SKILL.md"
        if not path.exists():
            return {"error": f"Skill '{name}' not found"}

        meta, body = _parse_frontmatter(path.read_text())
        if fields.get("description"):
            meta["description"] = fields["description"].strip().replace("\n", " ")
        if fields.get("when_to_use"):
            meta["when_to_use"] = fields["when_to_use"].strip().replace("\n", " ")
        if fields.get("references_routine") is not None:
            ref = fields["references_routine"].strip()
            if ref:
                meta["references_routine"] = ref
            else:
                meta.pop("references_routine", None)
        if fields.get("body"):
            body = fields["body"].strip()

        self._atomic_write(path, _render(meta, body))
        return self.read(slug) or {"saved": True, "name": slug}

    def delete(self, name: str) -> bool:
        """Delete a skill (and its now-empty folder)."""
        if not self.skills_dir:
            return False
        slug = _slugify(name)
        skill_dir = self.skills_dir / slug
        path = skill_dir / "SKILL.md"
        if not path.exists():
            return False
        path.unlink()
        try:
            skill_dir.rmdir()
        except OSError:
            pass  # other files present — leave the folder
        return True

    def read(self, name: str) -> dict | None:
        """Return a skill's frontmatter + body, or ``None`` if absent.

        Validates ``references_routine`` against the routine registry and
        surfaces ``routine_ok`` so the agent won't invoke a broken reference.
        """
        slug = _slugify(name)
        if not self.skills_dir:
            return None
        path = self.skills_dir / slug / "SKILL.md"
        if not path.exists():
            return None
        meta, body = _parse_frontmatter(path.read_text())
        ref = meta.get("references_routine")
        result = {
            "name": meta.get("name", slug),
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
        """Injectable skills index: one line per playbook the assistant ships.

        Computed live from disk (never persisted). Empty string when the
        assistant ships no skills, so callers add no noise.
        """
        return "\n".join(self._index_lines()).strip()

    # -- internals ---------------------------------------------------------

    def _iter_skills(self):
        """Yield (meta, body) for every skill, sorted by slug.

        Authored playbooks have no per-user ``created`` ordering, so slug order
        gives a stable injection order.
        """
        if not self.skills_dir or not self.skills_dir.exists():
            return
        for f in sorted(self.skills_dir.glob("*/SKILL.md")):
            try:
                meta, body = _parse_frontmatter(f.read_text())
            except Exception:
                continue
            meta.setdefault("name", f.parent.name)
            yield meta, body

    def _index_lines(self) -> list[str]:
        """One index line per skill (name + trigger + optional routine link)."""
        lines: list[str] = []
        for meta, _ in self._iter_skills():
            name = meta.get("name", "")
            when = meta.get("when_to_use", "")
            ref = meta.get("references_routine")
            line = f"- [{name}] {when}"
            if ref:
                line += f"  (→ routine: {ref})"
            lines.append(line)
        return lines

    @staticmethod
    def _atomic_write(path, text: str) -> None:
        """Write atomically (tmp file + os.replace) within the same dir."""
        import os

        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text)
        os.replace(tmp, path)
