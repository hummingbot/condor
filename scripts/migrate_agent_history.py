"""One-shot migration to the refactor-01b agent-level history layout.

Moves per-strategy operational state UP to the agent and converts delegation
transcripts into ``kind: delegation`` sessions::

    agents/{slug}/strategies/{sslug}/sessions/session_N   -> agents/{slug}/sessions/session_M (+ meta.yml)
    agents/{slug}/strategies/{sslug}/dry_runs/experiment_N.md -> agents/{slug}/dry_runs/experiment_M.md
    agents/{slug}/strategies/{sslug}/learnings.md         -> agents/{slug}/learnings.md (concat)
    agents/{slug}/strategies/{sslug}/config.yml           -> merged into strategy.md default_config
    agents/{slug}/strategies/{sslug}/shutdown.md          -> agents/{slug}/shutdown.md (if absent)
    agents/{slug}/delegations/{task_id}.md                -> agents/{slug}/sessions/session_M/transcript.md

Sessions/experiments are renumbered per-agent in mtime order so history
interleaves chronologically. Migrated tick sessions keep their legacy
composite ``controller_id`` ("{slug}.{sslug}_{N}") in meta.yml so historical
executor attribution survives the rename. Timestamps: only what is known is
recorded (mtime -> ended_at for delegations; journal ctime -> started_at for
tick sessions); nothing is fabricated.

Run from the repo root with the main process STOPPED (a live TickEngine or
delegation would be writing into the tree mid-move):

    python scripts/migrate_agent_history.py [--dry-run]

A full backup is taken first (``agents.pre-01b.bak/``) — agents/ is untracked
in git and the post-migration code has no legacy readers.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).parent.parent
AGENTS = ROOT / "agents"
BACKUP = ROOT / "agents.pre-01b.bak"

_DELEGATION_STATUS_RE = re.compile(r"^- \*\*Status:\*\* (\w+)", re.MULTILINE)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _write_meta(session_dir: Path, meta: dict) -> None:
    (session_dir / "meta.yml").write_text(
        yaml.dump(meta, default_flow_style=False, sort_keys=False)
    )


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    text = text.strip()
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    meta = yaml.safe_load(text[3:end].strip()) or {}
    return meta, text[end + 3 :].strip()


def migrate_agent(agent_dir: Path, dry_run: bool) -> list[str]:
    log: list[str] = []
    slug = agent_dir.name

    moves: list[tuple[str, ...]] = []  # (kind, src, ... ) collected in mtime order

    # -- collect per-strategy state --
    strategies_dir = agent_dir / "strategies"
    tick_sessions: list[tuple[float, Path, str, int]] = []  # (mtime, dir, sslug, n)
    experiments: list[tuple[float, Path]] = []
    for sdir in sorted(strategies_dir.iterdir()) if strategies_dir.exists() else []:
        if not sdir.is_dir():
            continue
        sslug = sdir.name
        for name in ("sessions", "trading_sessions"):
            sess_root = sdir / name
            if not sess_root.exists():
                continue
            for d in sess_root.iterdir():
                if d.is_dir() and d.name.startswith("session_"):
                    try:
                        n = int(d.name.split("_", 1)[1])
                    except ValueError:
                        continue
                    tick_sessions.append((d.stat().st_mtime, d, sslug, n))
        for name in ("dry_runs", "experiments"):
            exp_root = sdir / name
            if not exp_root.exists():
                continue
            for f in exp_root.glob("experiment_*.md"):
                experiments.append((f.stat().st_mtime, f))

        # learnings: concat into the agent-level file
        learnings = sdir / "learnings.md"
        if learnings.exists():
            target = agent_dir / "learnings.md"
            log.append(f"  learnings: {learnings.relative_to(ROOT)} -> {target.name}")
            if not dry_run:
                text = learnings.read_text()
                if target.exists():
                    target.write_text(
                        target.read_text().rstrip()
                        + f"\n\n<!-- merged from strategies/{sslug} -->\n"
                        + text
                    )
                else:
                    target.write_text(text)
                learnings.unlink()

        # strategy config.yml: merge into default_config frontmatter
        cfg_path = sdir / "config.yml"
        if cfg_path.exists():
            log.append(f"  config.yml of {sslug} -> strategy.md default_config")
            if not dry_run:
                cfg = yaml.safe_load(cfg_path.read_text()) or {}
                md_path = sdir / "strategy.md"
                meta, body = _parse_frontmatter(md_path.read_text())
                merged = dict(meta.get("default_config") or {})
                merged.update(cfg)
                meta["default_config"] = merged
                fm = yaml.dump(
                    meta, default_flow_style=False, allow_unicode=True, sort_keys=False
                ).strip()
                md_path.write_text(f"---\n{fm}\n---\n\n{body}\n")
                cfg_path.unlink()

        # strategy shutdown.md: promote to agent level when the agent has none
        sd_path = sdir / "shutdown.md"
        if sd_path.exists():
            target = agent_dir / "shutdown.md"
            if target.exists():
                log.append(
                    f"  WARNING: dropping {sd_path.relative_to(ROOT)} — the agent "
                    "already has shutdown.md (strategy tier no longer exists)"
                )
                if not dry_run:
                    sd_path.unlink()
            else:
                log.append(f"  shutdown.md of {sslug} -> agent level")
                if not dry_run:
                    sd_path.rename(target)

    # -- delegations --
    delegations: list[tuple[float, Path]] = []
    dele_dir = agent_dir / "delegations"
    if dele_dir.exists():
        for f in sorted(dele_dir.glob("*.md")):
            delegations.append((f.stat().st_mtime, f))

    # -- renumber: tick sessions + delegations interleave by mtime --
    all_sessions = [("tick", m, d, sslug, n) for m, d, sslug, n in tick_sessions] + [
        ("delegation", m, f, None, None) for m, f in delegations
    ]
    all_sessions.sort(key=lambda t: t[1])

    next_num = 1
    sessions_root = agent_dir / "sessions"
    for kind, mtime, src, sslug, old_n in all_sessions:
        target = sessions_root / f"session_{next_num}"
        if kind == "tick":
            legacy_id = f"{slug}.{sslug}_{old_n}"
            log.append(
                f"  session: {src.relative_to(ROOT)} -> sessions/session_{next_num} "
                f"(strategy={sslug}, controller_id={legacy_id})"
            )
            if not dry_run:
                sessions_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(target))
                journal = target / "journal.md"
                meta = {
                    "kind": "tick_loop",
                    "strategy": sslug,
                    "status": "stopped",
                    "controller_id": legacy_id,
                }
                if journal.exists():
                    meta["started_at"] = _iso(journal.stat().st_ctime)
                _write_meta(target, meta)
        else:
            text = src.read_text()
            m = _DELEGATION_STATUS_RE.search(text)
            status = m.group(1) if m else "done"
            log.append(
                f"  delegation: {src.name} -> sessions/session_{next_num} "
                f"(status={status}, ended_at from mtime)"
            )
            if not dry_run:
                sessions_root.mkdir(parents=True, exist_ok=True)
                target.mkdir(parents=True)
                shutil.move(str(src), str(target / "transcript.md"))
                task = ""
                tm = re.search(r"## Task\n\n(.+?)(?=\n\n## |\Z)", text, re.DOTALL)
                if tm:
                    task = tm.group(1).strip()[:500]
                _write_meta(
                    target,
                    {
                        "kind": "delegation",
                        "status": status,
                        "task": task,
                        "legacy_task_id": src.stem,
                        "ended_at": _iso(mtime),
                    },
                )
        next_num += 1

    # -- experiments renumber by mtime --
    experiments.sort(key=lambda t: t[0])
    for i, (mtime, src) in enumerate(experiments, start=1):
        log.append(f"  experiment: {src.relative_to(ROOT)} -> dry_runs/experiment_{i}.md")
        if not dry_run:
            (agent_dir / "dry_runs").mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(agent_dir / "dry_runs" / f"experiment_{i}.md"))

    # -- prune emptied legacy dirs --
    if not dry_run:
        for sdir in (
            sorted(strategies_dir.iterdir()) if strategies_dir.exists() else []
        ):
            if not sdir.is_dir():
                continue
            for name in ("sessions", "trading_sessions", "dry_runs", "experiments"):
                d = sdir / name
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()
        if dele_dir.exists() and not any(dele_dir.iterdir()):
            dele_dir.rmdir()

    # -- risk baseline check for trading agents (delegations now require one) --
    agent_md = agent_dir / "AGENT.md"
    if agent_md.exists():
        meta, body = _parse_frontmatter(agent_md.read_text())
        if meta.get("server_required", True) and not meta.get("risk_limits"):
            # Seed from the single strategy's default_config when unambiguous;
            # otherwise warn loudly — numbers must come from the user.
            candidates = []
            for sdir in (
                sorted(strategies_dir.iterdir()) if strategies_dir.exists() else []
            ):
                md = sdir / "strategy.md"
                if md.exists():
                    smeta, _ = _parse_frontmatter(md.read_text())
                    rl = (smeta.get("default_config") or {}).get("risk_limits")
                    if rl:
                        candidates.append(rl)
            if len(candidates) == 1:
                log.append(
                    f"  AGENT.md: seeding risk_limits baseline from its strategy "
                    f"default_config: {candidates[0]}"
                )
                if not dry_run:
                    meta["risk_limits"] = candidates[0]
                    fm = yaml.dump(
                        meta,
                        default_flow_style=False,
                        allow_unicode=True,
                        sort_keys=False,
                    ).strip()
                    agent_md.write_text(f"---\n{fm}\n---\n\n{body}\n")
            else:
                log.append(
                    "  WARNING: trading agent has NO risk_limits baseline "
                    f"({len(candidates)} strategy candidates) — delegations to it "
                    "will error until `risk_limits:` is set in AGENT.md or passed "
                    "per call"
                )

    return log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    args = parser.parse_args()

    if not AGENTS.exists():
        print(f"No agents/ dir at {AGENTS}")
        return 1

    if not args.dry_run:
        if BACKUP.exists():
            print(f"Backup {BACKUP} already exists — remove it first (refusing to overwrite).")
            return 1
        shutil.copytree(AGENTS, BACKUP)
        print(f"Backed up agents/ -> {BACKUP}")

    total = 0
    for agent_dir in sorted(AGENTS.iterdir()):
        if not agent_dir.is_dir() or agent_dir.name.startswith("_"):
            continue
        log = migrate_agent(agent_dir, args.dry_run)
        if log:
            print(f"{agent_dir.name}:")
            for line in log:
                print(line)
            total += len(log)
    print(f"\n{'Would apply' if args.dry_run else 'Applied'} {total} change(s).")
    if args.dry_run:
        print("Re-run without --dry-run to apply (backup is taken automatically).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
