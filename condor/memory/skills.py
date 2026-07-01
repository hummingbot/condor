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
            <companion>.md   # optional attached reference files (templates, etc.)

where ``{assistant_home}`` is ``assistants/condor`` for the chat (``agent_slug``
None) or ``agents/<slug>`` for a trading agent / domain expert.

A skill folder may bundle **companion files** beside its ``SKILL.md`` — e.g.
config templates the playbook links. These implement *progressive disclosure*:
the injected index shows only the ``SKILL.md`` trigger, the companions stay out
of context, and the agent pulls one on demand via :meth:`SkillStore.read_file`.
"""

from __future__ import annotations

from pathlib import Path

from .paths import builtin_skills_root
from .store import _atomic_write, _parse_frontmatter, _render, _slugify, _utcnow


def _routine_exists(name: str, agent_slug: str | None = None) -> bool:
    """True if ``name`` is a routine this assistant can actually run.

    Validated against the *same* scope the runtime resolves routines in: a
    trading agent / domain expert (``agent_slug`` set) runs ONLY its own routines
    (``agents/<slug>/routines``) and never the chat's general library, so an agent
    skill's reference is checked against the agent's dir alone; the chat ``condor``
    (``agent_slug`` None) is checked against the global registry. A miss simply
    reports ``routine_ok=false`` (advisory; never fatal).
    """
    try:
        if agent_slug:
            from routines.base import (
                assistant_routines_dir,
                discover_routines_from_path,
            )

            own_dir = assistant_routines_dir(agent_slug)
            if not own_dir.exists():
                return False
            return name in discover_routines_from_path(own_dir, agent_slug=agent_slug)

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

        _atomic_write(path, _render(meta, body.strip()))
        result = {
            "saved": True,
            "name": slug,
            "description": meta["description"],
            "when_to_use": meta["when_to_use"],
        }
        if ref:
            result["references_routine"] = ref
            result["routine_ok"] = _routine_exists(ref, self.agent_slug)
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

        _atomic_write(path, _render(meta, body))
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
        When the skill bundles companion files (see :meth:`read_file`), their
        names are listed under ``files`` so the agent knows what it can pull.
        """
        slug = _slugify(name)
        if not self.skills_dir:
            return None
        skill_dir = self.skills_dir / slug
        path = skill_dir / "SKILL.md"
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
        files = self._companion_files(skill_dir)
        if files:
            result["files"] = files
        if ref:
            result["references_routine"] = ref
            result["routine_ok"] = _routine_exists(ref, self.agent_slug)
        return result

    def read_file(self, name: str, filename: str) -> dict:
        """Return the contents of a companion file bundled in a skill's folder.

        Companion files implement progressive disclosure: a skill's ``SKILL.md``
        links them (e.g. config templates) and the agent pulls one only when
        needed, so the bulk stays out of the prompt until requested. ``filename``
        must be a bare name living directly inside the skill folder — any path
        separator or traversal is rejected so a skill can never read outside its
        own directory.
        """
        if not self.skills_dir:
            return {"error": "this assistant has no skills library"}
        slug = _slugify(name)
        skill_dir = self.skills_dir / slug
        if not (skill_dir / "SKILL.md").exists():
            return {"error": f"Skill '{name}' not found"}

        fname = (filename or "").strip()
        if not fname or fname == "SKILL.md":
            return {"error": "filename is required (a companion file, not SKILL.md)"}
        # Companion files are flat inside the skill dir: reject any path component.
        if "/" in fname or "\\" in fname or Path(fname).name != fname:
            return {"error": f"Invalid file name '{filename}'"}

        target = skill_dir / fname
        # Defense in depth: the resolved path must stay within the skill folder.
        try:
            if not target.resolve().is_relative_to(skill_dir.resolve()):
                return {"error": f"Invalid file name '{filename}'"}
        except (OSError, ValueError):
            return {"error": f"Invalid file name '{filename}'"}
        if not target.is_file():
            return {
                "error": f"File '{filename}' not found in skill '{slug}'",
                "files": self._companion_files(skill_dir),
            }
        return {"skill": slug, "file": fname, "content": target.read_text()}

    @staticmethod
    def _companion_files(skill_dir: Path) -> list[str]:
        """Names of attached reference files in a skill folder (all but SKILL.md).

        Hidden/temp files (``.``-prefixed, incl. the atomic-write tmp files) are
        skipped so only authored companions surface.
        """
        if not skill_dir.is_dir():
            return []
        return sorted(
            f.name
            for f in skill_dir.iterdir()
            if f.is_file() and f.name != "SKILL.md" and not f.name.startswith(".")
        )

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
                    hit["routine_ok"] = _routine_exists(ref, self.agent_slug)
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
