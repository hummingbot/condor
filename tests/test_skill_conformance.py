"""Every SKILL.md in the repo must conform to the agentskills.io spec
(refactor-05 Phase 1) plus OpenClaw's single-line-frontmatter constraint,
so the host-facing set installs natively in Claude Code / OpenClaw / Hermes
and agent-internal skills stay promotion-compatible.
"""

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")

SKILL_FILES = sorted(
    list(ROOT.glob("skills/*/SKILL.md"))
    + list(ROOT.glob("agents/*/skills/*/SKILL.md"))
)


def _frontmatter_lines(text: str) -> list[str]:
    assert text.startswith("---"), "SKILL.md must start with frontmatter"
    end = text.index("\n---", 3)
    return text[4:end].splitlines()


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_skill_md_conforms(path):
    text = path.read_text()
    lines = _frontmatter_lines(text)

    # OpenClaw constraint: every frontmatter entry is a single `key: value` line
    # (no YAML continuations / block scalars).
    for line in lines:
        assert re.match(r"^[A-Za-z0-9_-]+: ", line), (
            f"multiline or malformed frontmatter line: {line!r}"
        )

    meta = yaml.safe_load("\n".join(lines))

    # name: spec pattern, matches parent dir
    name = meta.get("name")
    assert name, "name is required"
    assert len(name) <= 64
    assert _NAME_RE.match(name), f"name {name!r} violates the spec pattern"
    assert name == path.parent.name, "name must match the parent directory"

    # description: required, 1-1024
    desc = meta.get("description")
    assert desc and isinstance(desc, str)
    assert 1 <= len(desc) <= 1024

    # metadata: flat string->string map with condor- prefixed keys
    md = meta.get("metadata")
    if md is not None:
        assert isinstance(md, dict)
        for k, v in md.items():
            assert isinstance(k, str) and k.startswith("condor-"), k
            assert isinstance(v, str), f"metadata[{k}] must be a string"

    # compatibility: <=500 chars when present
    compat = meta.get("compatibility")
    if compat is not None:
        assert isinstance(compat, str) and 1 <= len(compat) <= 500

    # no legacy top-level fields
    for legacy in ("when_to_use", "source", "created", "references_routine"):
        assert legacy not in meta, f"legacy top-level field {legacy!r} present"


def test_host_facing_skills_declare_mcp_dependency():
    """Skills in the host-visible root must gate on the Condor MCP server and
    carry the operate-via-MCP-only rule (refactor-05 §4.0)."""
    host = sorted(ROOT.glob("skills/*/SKILL.md"))
    assert host, "host-facing skills root is empty"
    for path in host:
        text = path.read_text()
        meta = yaml.safe_load("\n".join(_frontmatter_lines(text)))
        assert "Condor MCP" in (meta.get("compatibility") or ""), path
        assert "Operating rule" in text, f"{path} missing the MCP-only rule"


def test_claude_code_symlinks_fresh():
    """.claude/skills mirrors the host-facing set (per-skill symlinks)."""
    host = {p.name for p in (ROOT / "skills").iterdir() if p.is_dir()}
    linked = {
        p.name
        for p in (ROOT / ".claude" / "skills").iterdir()
        if (p / "SKILL.md").exists()
    }
    assert host == linked
