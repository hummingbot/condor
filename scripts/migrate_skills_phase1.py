"""Refactor-05 Phase 1: migrate every SKILL.md to the agentskills.io format.

Per skill:
- rename dir + ``name`` to the hyphenated spec form (underscores are invalid);
- fold ``when_to_use`` into ``description`` ("<description> Use when <trigger>")
  — one routing field, per spec; warn (never truncate) if the result >1024;
- move ``source``/``created``/``references_routine`` into flat ``metadata``
  string keys (``condor-source`` / ``condor-created`` /
  ``condor-references-routine``);
- re-render frontmatter single-line (OpenClaw's parser constraint).

Host-facing library move: ``assistants/condor/skills/*`` → repo-root
``skills/`` (serves Condor chat + Claude Code + OpenClaw + Hermes from one
dir); each host-facing skill gains a ``compatibility`` line and the
operate-via-MCP-only rule appended to its body. ``.claude/skills/<name>``
symlinks are created for Claude Code.

Historical artifacts (dry_runs, session transcripts) are records — NOT
rewritten. Skills are git-tracked; run from a clean-ish tree and review the
diff. Usage:

    python scripts/migrate_skills_phase1.py [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
HOST_SKILLS = ROOT / "skills"
CLAUDE_SKILLS = ROOT / ".claude" / "skills"

MCP_ONLY_RULE = """
## Operating rule (host deployments)

Condor's `agents/` and `assistants/` trees are its runtime state. Operate
Condor ONLY via the `mcp__condor__*` tools — never by reading or editing
those files directly. If the Condor MCP server is not connected, tell the
user to connect it instead of improvising against the filesystem.
"""

COMPATIBILITY = "Requires the Condor MCP server (mcp__condor__* tools) connected"


def _hyphenate(name: str) -> str:
    s = (name or "").lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return re.sub(r"-+", "-", s).strip("-")[:64]


def _parse(text: str) -> tuple[dict, str]:
    text = text.strip()
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    return yaml.safe_load(text[3:end].strip()) or {}, text[end + 3 :].strip()


def _render(meta: dict, body: str) -> str:
    lines = ["---", f"name: {meta['name']}"]
    lines.append(f"description: {json.dumps(meta['description'], ensure_ascii=False)}")
    if meta.get("compatibility"):
        lines.append(f"compatibility: {json.dumps(meta['compatibility'], ensure_ascii=False)}")
    md = {k: str(v) for k, v in (meta.get("metadata") or {}).items() if str(v)}
    if md:
        lines.append(f"metadata: {json.dumps(md, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n" + body.strip() + "\n"


def migrate_skill(skill_dir: Path, host_facing: bool, dry_run: bool) -> list[str]:
    log: list[str] = []
    path = skill_dir / "SKILL.md"
    meta, body = _parse(path.read_text())

    old_name = str(meta.get("name", skill_dir.name))
    new_name = _hyphenate(old_name)

    desc = str(meta.get("description", "")).strip().replace("\n", " ")
    when = str(meta.get("when_to_use", "")).strip().replace("\n", " ")
    if when:
        # Fold the trigger into the description unless already contained.
        if when.lower() not in desc.lower():
            trigger = when[0].lower() + when[1:] if when else when
            trigger = re.sub(r"^when(ever)?\s+", "", trigger)
            desc = f"{desc.rstrip('.')}. Use when {trigger}"
        if not desc.endswith("."):
            desc += "."
    if len(desc) > 1024:
        log.append(
            f"  WARNING {new_name}: folded description is {len(desc)} chars "
            "(spec cap 1024) — trim manually"
        )

    metadata = {}
    if meta.get("source"):
        metadata["condor-source"] = str(meta["source"])
    if meta.get("created"):
        metadata["condor-created"] = str(meta["created"])
    if meta.get("references_routine"):
        metadata["condor-references-routine"] = str(meta["references_routine"])

    new_meta = {"name": new_name, "description": desc, "metadata": metadata}
    if host_facing:
        new_meta["compatibility"] = COMPATIBILITY
        if "Operating rule" not in body:
            body = body.rstrip() + "\n" + MCP_ONLY_RULE

    target_dir = skill_dir if skill_dir.name == new_name else skill_dir.parent / new_name
    log.append(
        f"  {skill_dir.name} -> {target_dir.name}"
        + (" [host-facing: +compatibility, +MCP-only rule]" if host_facing else "")
    )
    if not dry_run:
        if target_dir != skill_dir:
            skill_dir.rename(target_dir)
        (target_dir / "SKILL.md").write_text(_render(new_meta, body))
    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    total: list[str] = []

    # 1. Host-facing library: assistants/condor/skills/* -> skills/
    legacy_chat = ROOT / "assistants" / "condor" / "skills"
    if legacy_chat.exists():
        total.append(f"host-facing: {legacy_chat.relative_to(ROOT)} -> skills/")
        if not args.dry_run:
            HOST_SKILLS.mkdir(exist_ok=True)
        for d in sorted(legacy_chat.iterdir()):
            if not d.is_dir() or not (d / "SKILL.md").exists():
                continue
            dest = HOST_SKILLS / d.name
            total.append(f"  move {d.name}")
            if not args.dry_run:
                d.rename(dest)
        if not args.dry_run and not any(legacy_chat.iterdir()):
            legacy_chat.rmdir()

    # 2. Migrate frontmatter: host-facing set + every agent's local skills
    host_root = HOST_SKILLS if HOST_SKILLS.exists() else legacy_chat
    if host_root.exists():
        for d in sorted(host_root.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                total += migrate_skill(d, host_facing=True, dry_run=args.dry_run)
    agents_dir = ROOT / "agents"
    for agent in sorted(agents_dir.iterdir()) if agents_dir.exists() else []:
        skills = agent / "skills"
        if not skills.is_dir() or agent.name.startswith("_"):
            continue
        total.append(f"{agent.name}:")
        for d in sorted(skills.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                total += migrate_skill(d, host_facing=False, dry_run=args.dry_run)

    # 3. .claude/skills symlinks for Claude Code
    if HOST_SKILLS.exists():
        total.append(".claude/skills symlinks:")
        if not args.dry_run:
            CLAUDE_SKILLS.mkdir(parents=True, exist_ok=True)
        for d in sorted(HOST_SKILLS.iterdir()) if not args.dry_run else []:
            if not d.is_dir():
                continue
            link = CLAUDE_SKILLS / d.name
            if link.is_symlink() or link.exists():
                continue
            link.symlink_to(Path("..") / ".." / "skills" / d.name)
            total.append(f"  {link.relative_to(ROOT)} -> skills/{d.name}")

    for line in total:
        print(line)
    print(f"\n{'Would apply' if args.dry_run else 'Applied'}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
