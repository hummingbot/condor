"""Markdown exports of RunStore streams (§7.1).

Markdown is a GENERATED view now — journals, experiment snapshots, and
delegation transcripts are all renderings of the same event stream, and
nothing ever parses markdown back.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def _fmt_ts(ts: float | None) -> str:
    if not ts:
        return "-"
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _render_tool_call(n: int, payload: dict) -> str:
    name = payload.get("name") or payload.get("title") or "unknown"
    status = payload.get("status") or ""
    block = [f"🔧 **{n}. {name}** ({status})"]
    if payload.get("input"):
        inp = payload["input"]
        inp_str = (
            json.dumps(inp, indent=2, default=str) if isinstance(inp, dict) else str(inp)
        )
        block.append(f"**Input:**\n```json\n{inp_str}\n```")
    if payload.get("output"):
        out = str(payload["output"])
        if len(out) > 2000:
            out = out[:2000] + "\n… (truncated)"
        block.append(f"**Output:**\n```\n{out}\n```")
    if payload.get("artifact"):
        block.append(
            f"_(payload spilled to artifact `{payload['artifact']}`, "
            f"{payload.get('bytes', 0)} bytes)_"
        )
    return "\n".join(block)


def render_run_markdown(meta: dict, events: list[dict]) -> str:
    """One human-readable markdown document for a run — header, then the
    chronological event narrative."""
    parts: list[str] = []
    parts.append(
        f"# {meta.get('agent_slug', '?')} — {meta.get('kind', 'run')} "
        f"#{meta.get('display_seq', '?')} ({meta.get('run_id', '')})"
    )
    header = [
        f"- **Status:** {meta.get('status', 'unknown')}"
        + (f" ({meta['reason']})" if meta.get("reason") else ""),
        f"- **Started:** {_fmt_ts(meta.get('started_at'))}",
        f"- **Ended:** {_fmt_ts(meta.get('ended_at'))}",
    ]
    if meta.get("model"):
        header.append(f"- **Model:** {meta['model']}")
    if meta.get("source_spec_hash"):
        header.append(
            f"- **Spec:** source `{meta['source_spec_hash'][:12]}…` / "
            f"resolved `{meta.get('resolved_spec_hash', '')[:12]}…`"
        )
    if meta.get("scheduled_for"):
        header.append(f"- **Scheduled for:** {meta['scheduled_for']}")
    parts.append("\n".join(header))

    tool_n = 0
    for ev in events:
        etype = ev.get("type")
        payload: dict[str, Any] = ev.get("payload", {}) or {}
        ts = _fmt_ts(ev.get("ts"))
        if etype == "run_started":
            if payload.get("task"):
                parts.append(f"## Task\n\n{payload['task']}")
        elif etype == "tick_started":
            parts.append(f"## Tick #{ev.get('tick', '?')} — {ts}")
        elif etype == "tool_call":
            tool_n += 1
            parts.append(_render_tool_call(tool_n, payload))
        elif etype == "permission":
            status = payload.get("status") or payload.get("decision") or "?"
            parts.append(
                f"🔐 **Permission** [{payload.get('approval_id', '')}] {status}"
                + (f" — {payload.get('summary', '')}" if payload.get("summary") else "")
            )
        elif etype == "state_snapshot":
            if payload.get("decision"):
                parts.append(f"📌 **Decision:** {payload['decision']}")
            if payload.get("state"):
                parts.append(f"🧭 **State:**\n\n{payload['state']}")
            if payload.get("result"):
                parts.append(f"## Result\n\n{payload['result']}")
            if payload.get("response"):
                parts.append(f"💬 {payload['response']}")
        elif etype == "directive":
            mark = "acked" if payload.get("acked") else "queued"
            parts.append(f"🧾 **Directive** ({mark}): {payload.get('text', '')}")
        elif etype == "notification":
            parts.append(f"🔔 {payload.get('text', '')}")
        elif etype == "error":
            parts.append(f"❌ **Error:** {payload.get('message', '')}")
        elif etype == "tick_completed":
            metrics = payload.get("metrics") or {}
            bits = [f"actions={payload.get('actions', 0)}"]
            if metrics:
                bits.append(f"pnl={metrics.get('total_pnl', 0):+.4f}")
                bits.append(f"exposure={metrics.get('total_exposure', 0):.2f}")
                bits.append(f"open={metrics.get('open_count', 0)}")
            parts.append(f"_(tick #{ev.get('tick', '?')} complete — {', '.join(bits)})_")
        elif etype == "run_ended":
            parts.append(
                f"## Run ended — {payload.get('status', '?')} — {ts}"
                + (f"\n\n{payload['reason']}" if payload.get("reason") else "")
            )
    return "\n\n".join(parts) + "\n"
