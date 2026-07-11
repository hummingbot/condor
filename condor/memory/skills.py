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

    {skills_root}/
        <name>/
            SKILL.md         # agentskills.io frontmatter + steps
            <companion>.md   # optional attached reference files (templates, etc.)

where ``{skills_root}`` is the repo-root ``skills/`` for the chat
(``agent_slug`` None — the HOST-FACING library, also consumed natively by
Claude Code / OpenClaw / Hermes) or ``agents/<slug>/skills`` for a trading
agent / domain expert (agent-internal).

SKILL.md conforms to the agentskills.io spec (refactor-05 Phase 1): ``name``
(hyphenated, matches the dir) and ``description`` (what + when — the routing
trigger) top-level; Condor extras live under ``metadata`` as flat
``condor-*`` string keys; every frontmatter value is single-line (OpenClaw's
embedded parser reads single-line values only), with ``metadata`` rendered
as single-line JSON.

A skill folder may bundle **companion files** beside its ``SKILL.md`` — e.g.
config templates the playbook links. These implement *progressive disclosure*:
the injected index shows only the ``SKILL.md`` trigger, the companions stay out
of context, and the agent pulls one on demand via :meth:`SkillStore.read_file`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from .paths import builtin_skills_root
from .store import _atomic_write, _parse_frontmatter, _utcnow

# agentskills.io name rule: 1-64 chars, lowercase alnum + hyphens, no
# leading/trailing/consecutive hyphens; must match the parent dir name.
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def _skill_slug(name: str) -> str:
    """Spec-conformant skill slug: lowercase, hyphen-separated.

    Unlike the memory store's ``_slugify`` (underscores), skill names must
    satisfy the agentskills.io pattern so they load in any host.
    """
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s[:64] or "unnamed"


def _render_skill(meta: dict, body: str) -> str:
    """Render spec-shaped frontmatter + body.

    Not the shared ``store._render``: that emits block-style YAML, and
    OpenClaw's embedded parser supports single-line frontmatter values only.
    Every value is emitted on one line; ``description``/``compatibility`` as
    JSON strings (valid single-line YAML scalars, colon/quote-safe) and
    ``metadata`` as a single-line JSON object of string values.
    """
    lines = ["---", f"name: {meta['name']}"]
    lines.append(f"description: {json.dumps(meta['description'], ensure_ascii=False)}")
    if meta.get("license"):
        lines.append(f"license: {json.dumps(meta['license'], ensure_ascii=False)}")
    if meta.get("compatibility"):
        lines.append(
            f"compatibility: {json.dumps(meta['compatibility'], ensure_ascii=False)}"
        )
    metadata = {
        k: str(v) for k, v in (meta.get("metadata") or {}).items() if str(v)
    }
    if metadata:
        lines.append(f"metadata: {json.dumps(metadata, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def _skill_meta_get(meta: dict, key: str) -> str:
    """Read a ``condor-*`` value from a skill's metadata map."""
    md = meta.get("metadata") or {}
    return str(md.get(key, "") or "")


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
        body: str,
        references_routine: str | None = None,
        source: str = "chat",
        compatibility: str | None = None,
    ) -> dict:
        """Create or overwrite a skill in this assistant's library.

        ``description`` is the routing trigger per the agentskills.io spec: it
        must say what the skill does AND when to use it, on a single line,
        1-1024 chars.
        """
        if not self.skills_dir:
            return {"error": "this assistant has no skills library"}
        if not name or not description or not body:
            return {"error": "name, description and body are required"}

        slug = _skill_slug(name)
        description = description.strip().replace("\n", " ")
        if len(description) > 1024:
            return {
                "error": f"description is {len(description)} chars — the spec "
                "caps it at 1024. Trim it (it should state what the skill does "
                "and when to use it)."
            }
        path = self.skills_dir / slug / "SKILL.md"

        # Preserve the original created date on overwrite.
        created = _utcnow()
        if path.exists():
            existing_meta, _ = _parse_frontmatter(path.read_text())
            created = _skill_meta_get(existing_meta, "condor-created") or created

        metadata = {"condor-source": source, "condor-created": created}
        ref = (references_routine or "").strip()
        if ref:
            metadata["condor-references-routine"] = ref
        meta = {
            "name": slug,
            "description": description,
            "compatibility": (compatibility or "").strip(),
            "metadata": metadata,
        }

        _atomic_write(path, _render_skill(meta, body.strip()))
        result = {
            "saved": True,
            "name": slug,
            "description": description,
        }
        if ref:
            result["references_routine"] = ref
            result["routine_ok"] = _routine_exists(ref, self.agent_slug)
        return result

    def edit(self, name: str, **fields) -> dict:
        """Patch fields of a skill, preserving the rest.

        Accepts ``description``, ``body``, ``references_routine`` (pass
        ``references_routine=""`` to clear the reference), ``compatibility``.
        """
        if not self.skills_dir:
            return {"error": "this assistant has no skills library"}
        slug = _skill_slug(name)
        path = self.skills_dir / slug / "SKILL.md"
        if not path.exists():
            return {"error": f"Skill '{name}' not found"}

        meta, body = _parse_frontmatter(path.read_text())
        meta.setdefault("name", slug)
        meta["metadata"] = dict(meta.get("metadata") or {})
        if fields.get("description"):
            desc = fields["description"].strip().replace("\n", " ")
            if len(desc) > 1024:
                return {"error": "description exceeds the spec's 1024-char cap"}
            meta["description"] = desc
        if fields.get("compatibility") is not None:
            meta["compatibility"] = fields["compatibility"].strip()
        if fields.get("references_routine") is not None:
            ref = fields["references_routine"].strip()
            if ref:
                meta["metadata"]["condor-references-routine"] = ref
            else:
                meta["metadata"].pop("condor-references-routine", None)
        if fields.get("body"):
            body = fields["body"].strip()

        _atomic_write(path, _render_skill(meta, body))
        return self.read(slug) or {"saved": True, "name": slug}

    def delete(self, name: str) -> bool:
        """Delete a skill (and its now-empty folder)."""
        if not self.skills_dir:
            return False
        slug = _skill_slug(name)
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
        slug = _skill_slug(name)
        if not self.skills_dir:
            return None
        skill_dir = self.skills_dir / slug
        path = skill_dir / "SKILL.md"
        if not path.exists():
            return None
        meta, body = _parse_frontmatter(path.read_text())
        ref = _skill_meta_get(meta, "condor-references-routine")
        result = {
            "name": meta.get("name", slug),
            "description": meta.get("description", ""),
            "body": body,
        }
        files = self._companion_files(skill_dir)
        if files:
            result["files"] = files
        if ref:
            result["references_routine"] = ref
            result["routine_ok"] = _routine_exists(ref, self.agent_slug)
        return result

    def _resolve_companion(
        self, name: str, filename: str
    ) -> tuple[Path | None, dict | None]:
        """Validate a companion-file reference and resolve its target path.

        Shared guard sequence for :meth:`read_file` / :meth:`write_file`:
        skills-dir presence, skill existence, bare-filename checks (no
        ``SKILL.md``, no path separators) and the resolve()/is_relative_to
        traversal defense, so a skill can never touch anything outside its own
        folder. Returns ``(target, None)`` on success or ``(None, error_dict)``
        on failure.
        """
        if not self.skills_dir:
            return None, {"error": "this assistant has no skills library"}
        slug = _skill_slug(name)
        skill_dir = self.skills_dir / slug
        if not (skill_dir / "SKILL.md").exists():
            return None, {"error": f"Skill '{name}' not found"}

        fname = (filename or "").strip()
        if not fname or fname == "SKILL.md":
            return None, {
                "error": "filename is required (a companion file, not SKILL.md)"
            }
        # Companion files are flat inside the skill dir: reject any path component.
        if "/" in fname or "\\" in fname or Path(fname).name != fname:
            return None, {"error": f"Invalid file name '{filename}'"}

        target = skill_dir / fname
        # Defense in depth: the resolved path must stay within the skill folder.
        try:
            if not target.resolve().is_relative_to(skill_dir.resolve()):
                return None, {"error": f"Invalid file name '{filename}'"}
        except (OSError, ValueError):
            return None, {"error": f"Invalid file name '{filename}'"}
        return target, None

    def read_file(self, name: str, filename: str) -> dict:
        """Return the contents of a companion file bundled in a skill's folder.

        Companion files implement progressive disclosure: a skill's ``SKILL.md``
        links them (e.g. config templates) and the agent pulls one only when
        needed, so the bulk stays out of the prompt until requested. ``filename``
        must be a bare name living directly inside the skill folder — any path
        separator or traversal is rejected (see :meth:`_resolve_companion`) so a
        skill can never read outside its own directory.
        """
        target, error = self._resolve_companion(name, filename)
        if error:
            return error
        skill_dir = target.parent
        slug = skill_dir.name
        if not target.is_file():
            return {
                "error": f"File '{filename}' not found in skill '{slug}'",
                "files": self._companion_files(skill_dir),
            }
        return {"skill": slug, "file": target.name, "content": target.read_text()}

    def write_file(self, name: str, filename: str, content: str) -> dict:
        """Create or overwrite a companion file bundled beside a skill's SKILL.md.

        The write counterpart to :meth:`read_file`: same flat namespace, same
        traversal guards, so a skill can only ever write inside its own folder.
        ``SKILL.md`` is off-limits here — edit the playbook itself through
        :meth:`edit` (which preserves frontmatter). The skill must already
        exist; this only manages its attached reference files (config templates,
        etc.). Prefer this over a raw filesystem write so path resolution goes
        through the slug and the companion index stays consistent.
        """
        if not self.skills_dir:
            return {"error": "this assistant has no skills library"}
        if content is None:
            return {"error": "content is required for write_file"}
        target, error = self._resolve_companion(name, filename)
        if error:
            return error

        skill_dir = target.parent
        created = not target.exists()
        _atomic_write(target, content)
        return {
            "saved": True,
            "created": created,
            "skill": skill_dir.name,
            "file": target.name,
            "files": self._companion_files(skill_dir),
        }

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
        """Keyword/substring search over name + description + body.

        Single seam for upgrading to semantic retrieval later without changing
        any caller (mirrors :meth:`MemoryStore.search`).
        """
        q = (query or "").lower().strip()
        results: list[dict] = []
        for meta, body in self._iter_skills():
            haystack = (
                f"{meta.get('name', '')} {meta.get('description', '')} {body}"
            ).lower()
            if not q or q in haystack:
                ref = _skill_meta_get(meta, "condor-references-routine")
                hit = {
                    "name": meta.get("name", ""),
                    "description": meta.get("description", ""),
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
        """One index line per skill (name + description/trigger + routine link)."""
        lines: list[str] = []
        for meta, _ in self._iter_skills():
            name = meta.get("name", "")
            desc = meta.get("description", "")
            ref = _skill_meta_get(meta, "condor-references-routine")
            line = f"- [{name}] {desc}"
            if ref:
                line += f"  (→ routine: {ref})"
            lines.append(line)
        return lines
