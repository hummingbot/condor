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

where ``{assistant_home}`` is ``agents/<slug>`` — ``agents/condor`` for the chat,
which a falsy ``agent_slug`` resolves (FEAT-033).

A skill folder may bundle **companion files** beside its ``SKILL.md`` — e.g.
config templates the playbook links. These implement *progressive disclosure*:
the injected index shows only the ``SKILL.md`` trigger, the companions stay out
of context, and the agent pulls one on demand via :meth:`SkillStore.read_file`.

**Inheritance (FEAT-031).** Condor's library is the base layer every agent
inherits, one skill at a time: a chat playbook whose frontmatter carries
``shared: true`` resolves for *every* agent too, as if it lived in the agent's
own dir. Three rules:

1. The agent's own library **shadows** an inherited skill of the same slug —
   that is how an agent specializes a playbook without forking the file.
2. Inheritance is **read-only**: ``create`` writes locally (and therefore
   shadows), while ``edit`` / ``delete`` / ``write_file`` on an inherited-only
   slug error out and name the create-to-shadow fix.
3. An **unflagged** chat skill is *not found* for an agent, not merely absent
   from its index — filtering the index but serving the body on request would
   be a leak with extra steps.

The chat itself inherits nothing: it *is* the publisher and already reads that
dir as its own.
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


def _is_shared(meta: dict) -> bool:
    """True if a skill's frontmatter publishes it to every agent (FEAT-031).

    Frontmatter is YAML, so ``shared: true`` parses as a bool; a hand-written
    ``shared: "yes"`` is accepted too. Anything else — including a missing key —
    keeps the skill private to its own library, which is the safe default: a
    published playbook lands in every agent's index and every tick prompt.
    """
    return str(meta.get("shared", "")).strip().lower() in ("true", "yes", "1")


class SkillStore:
    """Per-assistant, editable skill library.

    Keyed by ``agent_slug`` alone (skills are general to the assistant, not
    per-user): ``None`` resolves the chat ``condor`` library, a slug resolves a
    trading agent's / expert's library. The root is :func:`builtin_skills_root`.

    An agent reads from **two** roots: its own (writable) plus the ``shared``
    slice of Condor's library it inherits — see the module docstring.
    """

    def __init__(self, agent_slug: str | None = None):
        self.agent_slug = agent_slug
        # The assistant's own skills dir (repo-shipped + runtime-created
        # playbooks). It is the WRITABLE root, and no longer the whole truth of
        # what this assistant can read — see ``inherited_dir``.
        self.skills_dir = builtin_skills_root(agent_slug)
        # Read-only base layer: Condor's ``shared: true`` playbooks, which every
        # agent inherits. The chat IS the publisher, so it inherits nothing (it
        # already reads that dir as ``skills_dir``) — compared by resolved dir
        # rather than by slug because ``None`` and ``"condor"`` are both the chat
        # (FEAT-033), and inheriting from itself would list every shared playbook
        # twice.
        chat_dir = builtin_skills_root(None)
        self.inherited_dir = None if self.skills_dir == chat_dir else chat_dir

    # -- resolution --------------------------------------------------------

    def _resolve_skill_dir(self, slug: str) -> tuple[Path | None, bool]:
        """``(skill_dir, is_inherited)`` for ``slug`` — own library shadows Condor's.

        The single choke point every read routes through. An inherited hit must
        carry ``shared: true``; an unflagged Condor skill is NOT FOUND for an
        agent, not merely absent from its index. ``(None, False)`` when the slug
        resolves nowhere.
        """
        if self.skills_dir and (self.skills_dir / slug / "SKILL.md").exists():
            return self.skills_dir / slug, False
        if self.inherited_dir:
            path = self.inherited_dir / slug / "SKILL.md"
            if path.exists():
                try:
                    meta, _ = _parse_frontmatter(path.read_text())
                except OSError:
                    return None, False
                if _is_shared(meta):
                    return self.inherited_dir / slug, True
        return None, False

    def _readonly_error(self, slug: str) -> dict | None:
        """Guard for the write paths: error dict if ``slug`` is inherited-only.

        Deliberately an error rather than copy-on-write: silently forking the
        playbook for one agent while everyone else keeps the original is a
        divergence nobody would notice. The message names the fix.
        """
        skill_dir, inherited = self._resolve_skill_dir(slug)
        if skill_dir is not None and inherited:
            return {
                "error": (
                    f"'{slug}' is inherited from Condor (read-only). Create a "
                    "local skill with the same name to specialize it."
                )
            }
        return None

    # -- public API --------------------------------------------------------

    def create(
        self,
        name: str,
        description: str,
        when_to_use: str,
        body: str,
        references_routine: str | None = None,
        source: str = "chat",
        shared: bool | None = None,
    ) -> dict:
        """Create or overwrite a skill in this assistant's library.

        ``shared`` publishes the playbook to every agent (FEAT-031) and is
        honored **only** in Condor's own library — an agent cannot publish, so
        the flag is ignored when ``agent_slug`` is set. ``None`` keeps whatever
        the skill already had, so re-creating a published playbook does not
        silently unpublish it.
        """
        if not self.skills_dir:
            return {"error": "this assistant has no skills library"}
        if not name or not description or not when_to_use or not body:
            return {"error": "name, description, when_to_use and body are required"}

        slug = _slugify(name)
        path = self.skills_dir / slug / "SKILL.md"

        # Preserve the original created date (and publication) on overwrite.
        created = _utcnow()
        was_shared = False
        if path.exists():
            existing_meta, _ = _parse_frontmatter(path.read_text())
            created = existing_meta.get("created", created)
            was_shared = _is_shared(existing_meta)

        meta = {
            "name": slug,
            "description": description.strip().replace("\n", " "),
            "when_to_use": when_to_use.strip().replace("\n", " "),
            "created": created,
            "source": source,
        }
        publish = was_shared if shared is None else bool(shared)
        if publish and not self.agent_slug:
            meta["shared"] = True
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
        if meta.get("shared"):
            result["shared"] = True
        if ref:
            result["references_routine"] = ref
            result["routine_ok"] = _routine_exists(ref, self.agent_slug)
        return result

    def edit(self, name: str, **fields) -> dict:
        """Patch fields of a skill, preserving the rest.

        Accepts ``description``, ``when_to_use``, ``body``, ``references_routine``
        (pass ``references_routine=""`` to clear the reference) and ``shared``
        (publish/unpublish, chat library only — see :meth:`create`). Editing a
        skill this assistant merely *inherits* is refused.
        """
        if not self.skills_dir:
            return {"error": "this assistant has no skills library"}
        slug = _slugify(name)
        guard = self._readonly_error(slug)
        if guard:
            return guard
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
        if fields.get("shared") is not None and not self.agent_slug:
            if fields["shared"]:
                meta["shared"] = True
            else:
                meta.pop("shared", None)

        _atomic_write(path, _render(meta, body))
        return self.read(slug) or {"saved": True, "name": slug}

    def delete(self, name: str) -> bool | dict:
        """Delete a skill (and its now-empty folder).

        ``True`` on success, ``False`` when the slug is unknown, and an error
        dict when it resolves only to an inherited (read-only) skill.
        """
        if not self.skills_dir:
            return False
        slug = _slugify(name)
        guard = self._readonly_error(slug)
        if guard:
            return guard
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
        A playbook inherited from Condor is flagged ``inherited: true`` — the
        reader's view of the publisher's ``shared`` flag.
        """
        slug = _slugify(name)
        skill_dir, inherited = self._resolve_skill_dir(slug)
        if skill_dir is None:
            return None
        path = skill_dir / "SKILL.md"
        meta, body = _parse_frontmatter(path.read_text())
        ref = meta.get("references_routine")
        result = {
            "name": meta.get("name", slug),
            "description": meta.get("description", ""),
            "when_to_use": meta.get("when_to_use", ""),
            "body": body,
        }
        if inherited:
            result["inherited"] = True
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
        folder. The guard is anchored on the *resolved* skill dir, so it covers
        an inherited skill's folder exactly as it covers an own one. Returns
        ``(target, None)`` on success or ``(None, error_dict)`` on failure.
        """
        if not self.skills_dir:
            return None, {"error": "this assistant has no skills library"}
        slug = _slugify(name)
        skill_dir, _ = self._resolve_skill_dir(slug)
        if skill_dir is None:
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
        guard = self._readonly_error(_slugify(name))
        if guard:
            return guard
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
        """Yield (meta, body) for every skill this assistant can read.

        Own playbooks first (sorted by slug), then the ``shared`` slice of
        Condor's library whose slug was not already yielded — so shadowing falls
        out of iteration order and :meth:`list_index` / :meth:`search` inherit it
        with no code of their own. Authored playbooks have no per-user
        ``created`` ordering, so slug order gives a stable injection order.
        """
        seen: set[str] = set()
        for root, inherited in ((self.skills_dir, False), (self.inherited_dir, True)):
            if not root or not root.exists():
                continue
            for f in sorted(root.glob("*/SKILL.md")):
                slug = f.parent.name
                if slug in seen:
                    continue
                try:
                    meta, body = _parse_frontmatter(f.read_text())
                except Exception:
                    continue
                if inherited and not _is_shared(meta):
                    continue
                seen.add(slug)
                meta.setdefault("name", slug)
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
