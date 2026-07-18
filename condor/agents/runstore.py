"""RunStore — one append-only JSONL event stream per run (§7.1).

    agents/{slug}/runs/{run_id}/{run_id}.jsonl          # session/scheduled
    agents/{slug}/experiments/{run_id}/{run_id}.jsonl   # experiment (dry-run)
    agents/{slug}/consults/{run_id}/{run_id}.jsonl      # consult
    agents/{slug}/delegations/{run_id}/{run_id}.jsonl   # delegation

Every run is a folder named by its ULID that holds its own ``{run_id}.jsonl``
stream plus any companions/spills — so a run's whole footprint is one
self-contained directory. Each kind lands in its own top-level dir under the
agent folder (siblings of one another): ``runs/`` is the live session history,
while ``experiments/`` / ``consults/`` / ``delegations/`` keep dry-runs and
one-shot tasks out of it.

- **Run identity:** ``run_id`` is an opaque ULID. The ``run_started`` event
  carries explicit ``agent_slug``, ``kind`` (session|experiment|delegation|
  consult|scheduled), a display ``seq``, the frozen spec + both content
  hashes (§5.3), and the AccountRefs in play (§6.2b). The legacy suffix
  grammars (``_N``, ``_eN``, ``-dN``, ``-cTS``) are gone — nothing parses a
  run id.
- **Serialized writer:** one writer per run in the main process (a lock, not
  a parser: all emitters funnel through :meth:`RunStore.emit`; the MCP
  subprocess emits via the control socket, never by writing files).
- **Durability:** every event is written+flushed immediately; financial
  events (``run_started``/``run_ended``/``permission`` and mutating
  ``tool_call``) additionally fsync. A partial final line is ignored on read
  and truncated before the next append.
- **Redaction + size caps:** payloads pass a secret redactor; an event over
  ~16KB spills as a human-readable MARKDOWN file alongside the jsonl in the
  run folder — a per-event ``{HH-MM-SS}Z-{type}.md`` file (timestamps UTC) —
  and the event keeps a name+hash reference (hash over the markdown as
  written).
- **Companions:** two human-facing files sit next to the jsonl in the run
  folder, written via :meth:`RunStore.write_companion` — ``prompt.md`` (the
  run's prompt, written once by ``run_agent`` for every kind) and
  ``journal.md`` (one line per tick, appended by the tick engine). Generated
  views only: nothing parses them back.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RUN_KINDS = {"session", "experiment", "delegation", "consult", "scheduled"}

# Each run kind lands in its own top-level dir under the agent folder, siblings
# of one another. Sessions (and their scheduled variant) are the live history in
# ``runs/``; dry-runs and one-shot tasks get their own dirs so they never
# clutter it.
_KIND_DIR = {
    "session": "runs",
    "scheduled": "runs",
    "experiment": "experiments",
    "consult": "consults",
    "delegation": "delegations",
}
# All the per-agent dirs that hold run streams (for readers that sweep them all).
_RUN_DIRS = ("runs", "experiments", "consults", "delegations")

EVENT_TYPES = {
    "run_started",
    "tick_started",
    "tick_completed",
    "tool_call",
    "permission",
    "state_snapshot",
    "context_changed",
    "directive",
    "notification",
    "error",
    "run_ended",
}

# Always-fsync event types; a tool_call fsyncs when its payload is mutating.
_FSYNC_TYPES = {"run_started", "run_ended", "permission"}

MAX_EVENT_BYTES = 16 * 1024
_ARTIFACT_PREVIEW_CHARS = 512


# ---------------------------------------------------------------------------
# ULID (Crockford base32, 48-bit ms timestamp + 80 bits randomness)
# ---------------------------------------------------------------------------

_B32 = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid() -> str:
    ts = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = secrets.randbits(80)
    value = (ts << 80) | rand
    chars = []
    for _ in range(26):
        chars.append(_B32[value & 0x1F])
        value >>= 5
    return "".join(reversed(chars))


_ULID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")


def is_run_id(value: str) -> bool:
    return bool(_ULID_RE.match(value or ""))


def ulid_datetime(run_id: str) -> datetime:
    """Start time encoded in a ULID (first 10 chars = 48-bit ms timestamp, UTC)."""
    if not is_run_id(run_id):
        raise ValueError(f"not a ULID run id: {run_id!r}")
    ms = 0
    for ch in run_id[:10]:
        ms = (ms << 5) | _B32.index(ch)
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

# Key names whose values are sealed wholesale (case-insensitive substring).
_SENSITIVE_KEY_PARTS = (
    "private_key",
    "secret",
    "passphrase",
    "password",
    "api_key",
    "credential",
    "mnemonic",
    "seed_phrase",
)

# Value patterns redacted inside free text: EVM 32-byte hex keys and
# secret-key-length base58 tokens (solana keypairs are 87-88 chars; pubkeys
# are 32-44 and kept; the upper bound stops arbitrary long text matching).
_VALUE_PATTERNS = [
    re.compile(r"0x[0-9a-fA-F]{64}"),
    re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{80,96}\b"),
]

REDACTED = "[REDACTED]"


def _redact_text(text: str) -> str:
    for pat in _VALUE_PATTERNS:
        text = pat.sub(REDACTED, text)
    return text


def redact(value: Any) -> Any:
    """Deep-copy ``value`` with sensitive keys sealed and secret-shaped
    strings scrubbed. Applied to every event payload before persistence."""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if any(part in kl for part in _SENSITIVE_KEY_PARTS):
                out[k] = REDACTED
            else:
                out[k] = redact(v)
        return out
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


# ---------------------------------------------------------------------------
# Torn-tail handling
# ---------------------------------------------------------------------------


def _scan_good_prefix(path: Path) -> tuple[int, int]:
    """Return (byte_length_of_good_prefix, last_good_seq).

    A "good" line ends with a newline and parses as JSON. A torn final line
    (no trailing newline, or unparseable after a crash mid-write) is excluded;
    an unparseable line in the MIDDLE of the file is corruption and raises.
    """
    data = path.read_bytes()
    if not data:
        return 0, 0
    good_end = 0
    last_seq = 0
    start = 0
    n = len(data)
    while start < n:
        nl = data.find(b"\n", start)
        if nl == -1:
            break  # torn tail — excluded
        line = data[start : nl + 1]
        try:
            ev = json.loads(line)
            last_seq = int(ev.get("seq", last_seq))
        except (json.JSONDecodeError, ValueError, TypeError):
            if nl + 1 < n:
                raise CorruptRunError(
                    f"corrupt run stream (mid-file bad line) at {path}"
                )
            break  # bad FINAL line — treat as torn
        good_end = nl + 1
        start = nl + 1
    return good_end, last_seq


class CorruptRunError(RuntimeError):
    pass


class UnknownRunError(KeyError):
    pass


# ---------------------------------------------------------------------------
# Artifact rendering
# ---------------------------------------------------------------------------


def _render_artifact_md(
    type_: str, ts: float, run_id: str, seq: int, payload: dict
) -> str:
    """One spilled payload as a human-readable markdown document.

    Artifacts are forensic — nothing parses them back (the same one-way rule
    as ``exports.py``); the stream event keeps the sha256 of this exact text.
    String values (e.g. a tick's prompt) are written verbatim; everything
    else as fenced JSON.
    """
    when = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )
    lines = [f"# {type_} — {when}", "", f"- **run:** {run_id}", f"- **seq:** {seq}", ""]
    for key, value in payload.items():
        lines.append(f"## {key}")
        lines.append("")
        if isinstance(value, str):
            lines.append(value)
        else:
            lines.append("```json")
            lines.append(json.dumps(value, indent=2, ensure_ascii=False, default=str))
            lines.append("```")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Per-run writer
# ---------------------------------------------------------------------------


class _RunWriter:
    """Serialized appender for one run stream. Not shared across processes."""

    def __init__(self, path: Path, run_id: str, agent_slug: str):
        self.path = path
        self.run_id = run_id
        self.agent_slug = agent_slug
        self._lock = threading.Lock()
        self._closed = False
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        if path.exists():
            good_end, last_seq = _scan_good_prefix(path)
            if good_end != path.stat().st_size:
                with open(path, "r+b") as f:
                    f.truncate(good_end)
                log.warning("truncated torn tail of %s at %d bytes", path, good_end)
            self._seq = last_seq
        else:
            path.touch(mode=0o600)
            self._seq = 0
        os.chmod(path, 0o600)
        self._f = open(path, "a", encoding="utf-8")

    def _run_dir(self) -> Path:
        # Companions + spills live alongside the jsonl in the run's own folder
        # (``runs/{run_id}/``), so a run's whole footprint is one directory.
        return self.path.parent

    def _spill_artifact(self, seq: int, type_: str, payload: dict, ts: float) -> dict:
        text = _render_artifact_md(type_, ts, self.run_id, seq, payload)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        adir = self._run_dir()
        stamp = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%H-%M-%S")
        name = f"{stamp}Z-{type_}.md"
        if (adir / name).exists():  # two same-type spills in one second
            name = f"{stamp}Z-{type_}-{seq}.md"
        apath = adir / name
        apath.write_text(text)
        os.chmod(apath, 0o600)
        return {
            "artifact": name,  # sibling of the jsonl in the run folder
            "sha256": digest,
            "bytes": len(text.encode("utf-8")),
            "preview": text[:_ARTIFACT_PREVIEW_CHARS],
        }

    def emit(
        self,
        type_: str,
        payload: dict | None = None,
        *,
        tick: int | None = None,
        tool_call_id: str | None = None,
        executor_id: str | None = None,
    ) -> dict:
        if type_ not in EVENT_TYPES:
            raise ValueError(f"unknown run event type: {type_!r}")
        payload = redact(payload or {})
        with self._lock:
            if self._closed:
                raise UnknownRunError(f"run {self.run_id} writer is closed")
            self._seq += 1
            event: dict[str, Any] = {
                "v": 1,
                "seq": self._seq,
                "ts": time.time(),
                "type": type_,
                "run_id": self.run_id,
            }
            if tick is not None:
                event["tick"] = tick
            if tool_call_id:
                event["tool_call_id"] = tool_call_id
            if executor_id:
                event["executor_id"] = executor_id
            event["payload"] = payload
            line = json.dumps(event, ensure_ascii=False, default=str)
            if len(line.encode("utf-8")) > MAX_EVENT_BYTES:
                event["payload"] = self._spill_artifact(
                    self._seq, type_, payload, event["ts"]
                )
                line = json.dumps(event, ensure_ascii=False, default=str)
            self._f.write(line + "\n")
            self._f.flush()
            if type_ in _FSYNC_TYPES or (
                type_ == "tool_call" and payload.get("mutating")
            ):
                os.fsync(self._f.fileno())
            return event

    def write_companion(self, name: str, text: str, *, append: bool = False) -> Path:
        """Write a human-facing companion file (``prompt.md``, ``journal.md``)
        into the run folder, next to the jsonl. One-way: nothing parses these
        back. Text passes the same value redactor as event payloads."""
        path = self._run_dir() / name
        with self._lock:
            with open(path, "a" if append else "w", encoding="utf-8") as f:
                f.write(_redact_text(text))
        os.chmod(path, 0o600)
        return path

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._f.flush()
                os.fsync(self._f.fileno())
            finally:
                self._f.close()


# ---------------------------------------------------------------------------
# RunStore
# ---------------------------------------------------------------------------


def _default_root() -> Path:
    from condor.agents.agent import agents_data_root

    return agents_data_root()


class RunStore:
    """The one operational history: start/emit/end + read/list/sweep."""

    def __init__(self, root: Path | None = None):
        self._root = Path(root) if root else _default_root()
        self._writers: dict[str, _RunWriter] = {}
        self._lock = threading.Lock()

    # -- paths ----------------------------------------------------------

    def runs_dir(self, agent_slug: str) -> Path:
        """The sessions dir. Other kinds live in sibling dirs — see
        :meth:`kind_dir` / :meth:`all_run_paths`."""
        return self._root / agent_slug / "runs"

    def kind_dir(self, agent_slug: str, kind: str = "") -> Path:
        """The top-level dir holding a kind's runs (``runs``/``experiments``/
        ``consults``/``delegations``); unknown kinds fall back to ``runs``."""
        return self._root / agent_slug / _KIND_DIR.get(kind, "runs")

    def run_dir(self, agent_slug: str, run_id: str, kind: str = "") -> Path:
        """The run's own folder inside its kind dir."""
        return self.kind_dir(agent_slug, kind) / run_id

    def run_path(self, agent_slug: str, run_id: str, kind: str = "") -> Path:
        return self.run_dir(agent_slug, run_id, kind) / f"{run_id}.jsonl"

    def all_run_paths(self, agent_slug: str):
        """Every run stream for an agent, across all kind dirs (unordered)."""
        base = self._root / agent_slug
        for d in _RUN_DIRS:
            kd = base / d
            if kd.exists():
                yield from kd.rglob("*.jsonl")

    def find_run_path(self, run_id: str) -> Path | None:
        """Locate a run stream by opaque id (checks live writers first). The
        stream is always ``.../{kind_dir}/{run_id}/{run_id}.jsonl``; only the
        kind dir varies, so try each."""
        w = self._writers.get(run_id)
        if w is not None:
            return w.path
        if not self._root.exists():
            return None
        for d in _RUN_DIRS:
            for candidate in self._root.glob(f"*/{d}/{run_id}/{run_id}.jsonl"):
                return candidate
        return None

    @staticmethod
    def slug_from_path(path: Path) -> str:
        """The agent slug that owns a run path — the parent of its kind-dir
        ancestor (``runs``/``experiments``/``consults``/``delegations``)."""
        for p in path.parents:
            if p.name in _RUN_DIRS:
                return p.parent.name
        raise ValueError(f"not a run path (no kind-dir ancestor): {path}")

    # -- writing ---------------------------------------------------------

    def start_run(
        self,
        agent_slug: str,
        kind: str,
        *,
        payload: dict | None = None,
        run_id: str | None = None,
    ) -> str:
        """Open a new run stream and persist ``run_started`` (fsynced).

        ``payload`` carries the caller's identity block (frozen spec, both
        spec hashes, AccountRefs, model, scheduled_for, task, ...); the store
        adds ``agent_slug``, ``kind`` and the display ``seq``.
        """
        if kind not in RUN_KINDS:
            raise ValueError(f"unknown run kind: {kind!r} (one of {sorted(RUN_KINDS)})")
        run_id = run_id or new_ulid()
        kind_dir = self.kind_dir(agent_slug, kind)
        kind_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(kind_dir, 0o700)
        # display_seq counts every run of the agent, across all kind dirs.
        display_seq = sum(1 for _ in self.all_run_paths(agent_slug)) + 1
        path = self.run_path(agent_slug, run_id, kind)
        if path.exists():
            raise ValueError(f"run id collision: {run_id}")
        with self._lock:
            writer = _RunWriter(path, run_id, agent_slug)
            self._writers[run_id] = writer
        started = dict(payload or {})
        started["agent_slug"] = agent_slug
        started["kind"] = kind
        started["display_seq"] = display_seq
        writer.emit("run_started", started)
        return run_id

    def emit(
        self,
        run_id: str,
        type_: str,
        payload: dict | None = None,
        *,
        tick: int | None = None,
        tool_call_id: str | None = None,
        executor_id: str | None = None,
    ) -> dict:
        writer = self._writers.get(run_id)
        if writer is None:
            raise UnknownRunError(f"no open writer for run {run_id}")
        return writer.emit(
            type_,
            payload,
            tick=tick,
            tool_call_id=tool_call_id,
            executor_id=executor_id,
        )

    def write_companion(
        self, run_id: str, name: str, text: str, *, append: bool = False
    ) -> Path:
        """Write/append a companion file into an OPEN run's artifacts dir."""
        writer = self._writers.get(run_id)
        if writer is None:
            raise UnknownRunError(f"no open writer for run {run_id}")
        return writer.write_companion(name, text, append=append)

    def end_run(self, run_id: str, status: str, reason: str = "") -> None:
        """Persist ``run_ended`` (fsynced) and close the writer. Idempotent
        for an already-closed run."""
        writer = self._writers.pop(run_id, None)
        if writer is None:
            return
        try:
            writer.emit("run_ended", {"status": status, "reason": reason})
        finally:
            writer.close()

    def is_open(self, run_id: str) -> bool:
        return run_id in self._writers

    def open_run_ids(self) -> list[str]:
        return list(self._writers)

    def close_all(self) -> None:
        for run_id in list(self._writers):
            self.end_run(run_id, "interrupted", "process shutdown")

    # -- reading ----------------------------------------------------------

    def read_events(self, agent_slug: str, run_id: str) -> list[dict]:
        """All good events of one run, in order. Torn final line ignored."""
        path = self.find_run_path(run_id)
        if path is None:
            raise UnknownRunError(f"unknown run: {run_id}")
        events: list[dict] = []
        data = path.read_bytes()
        start, n = 0, len(data)
        while start < n:
            nl = data.find(b"\n", start)
            if nl == -1:
                break
            line = data[start : nl + 1]
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if nl + 1 < n:
                    raise CorruptRunError(
                        f"corrupt run stream (mid-file bad line) at {path}"
                    )
                break
            start = nl + 1
        return events

    @staticmethod
    def _tail_event(path: Path) -> dict | None:
        """Last good event of a stream (cheap: reads a tail window)."""
        size = path.stat().st_size
        window = min(size, 64 * 1024)
        with open(path, "rb") as f:
            f.seek(size - window)
            data = f.read()
        lines = data.split(b"\n")
        for raw in reversed(lines):
            if not raw.strip():
                continue
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                continue  # torn tail or window cut mid-line
        return None

    @staticmethod
    def _head_event(path: Path) -> dict | None:
        with open(path, "rb") as f:
            raw = f.readline()
        if not raw.endswith(b"\n"):
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def run_meta(self, agent_slug: str, run_id: str) -> dict:
        """Summary of one run from its first + last events."""
        path = self.find_run_path(run_id)
        if path is None:
            raise UnknownRunError(f"unknown run: {agent_slug}/{run_id}")
        return self._meta_from_path(path, agent_slug)

    def _meta_from_path(self, path: Path, agent_slug: str) -> dict:
        head = self._head_event(path)
        tail = self._tail_event(path)
        run_id = path.stem
        meta: dict[str, Any] = {
            "run_id": run_id,
            "agent_slug": agent_slug,
            "kind": "",
            "display_seq": 0,
            "started_at": None,
            "ended_at": None,
            "status": "unknown",
            "reason": "",
        }
        if head and head.get("type") == "run_started":
            p = head.get("payload", {})
            meta["kind"] = p.get("kind", "")
            meta["display_seq"] = p.get("display_seq", 0)
            meta["started_at"] = head.get("ts")
            meta["model"] = p.get("model", "")
            meta["source_spec_hash"] = p.get("source_spec_hash", "")
            meta["resolved_spec_hash"] = p.get("resolved_spec_hash", "")
            if p.get("scheduled_for"):
                meta["scheduled_for"] = p["scheduled_for"]
            if p.get("task"):
                meta["task"] = p["task"]
        if tail and tail.get("type") == "run_ended":
            meta["status"] = tail.get("payload", {}).get("status", "unknown")
            meta["reason"] = tail.get("payload", {}).get("reason", "")
            meta["ended_at"] = tail.get("ts")
        elif self.is_open(run_id):
            meta["status"] = "running"
        else:
            # run_started without run_ended and no live writer: interrupted
            # stream awaiting the startup sweep.
            meta["status"] = "interrupted"
        if tail is not None:
            meta["event_count"] = tail.get("seq", 0)
        return meta

    def list_runs(
        self, agent_slug: str, *, kind: str | None = None, limit: int = 50
    ) -> list[dict]:
        """Run summaries for one agent, newest first (across all kind dirs)."""
        # A kind filter narrows to that one dir; otherwise sweep them all.
        if kind:
            kd = self.kind_dir(agent_slug, kind)
            candidates = kd.rglob("*.jsonl") if kd.exists() else ()
        else:
            candidates = self.all_run_paths(agent_slug)
        paths = sorted(candidates, key=lambda p: p.stat().st_mtime, reverse=True)
        out: list[dict] = []
        for path in paths:
            meta = self._meta_from_path(path, agent_slug)
            if kind and meta.get("kind") != kind:
                continue
            out.append(meta)
            if len(out) >= limit:
                break
        return out

    def agent_slugs_with_runs(self) -> list[str]:
        if not self._root.exists():
            return []
        slugs = {
            p.parent.name
            for d in _RUN_DIRS
            for p in self._root.glob(f"*/{d}")
            if p.is_dir()
        }
        return sorted(slugs)

    # -- startup recovery --------------------------------------------------

    def sweep_interrupted(self) -> list[dict]:
        """Synthesize ``run_ended {status: interrupted}`` for every stream
        that has ``run_started`` but no ``run_ended`` (§4.2 — engines are
        memory-only; runs do not survive process death). Returns the pending
        ``permission`` events found in those runs so the approval layer can
        void them (``interrupted_void``)."""
        voided: list[dict] = []
        if not self._root.exists():
            return voided
        paths = (
            p for d in _RUN_DIRS for p in self._root.glob(f"*/{d}/**/*.jsonl")
        )
        for path in paths:
            run_id = path.stem
            if self.is_open(run_id):
                continue
            tail = self._tail_event(path)
            if tail is None or tail.get("type") == "run_ended":
                continue
            agent_slug = self.slug_from_path(path)
            # Collect pending permissions before appending (state of record).
            pending = self._pending_permissions(agent_slug, run_id)
            writer = _RunWriter(path, run_id, agent_slug)
            try:
                for perm in pending:
                    writer.emit(
                        "permission",
                        {
                            "approval_id": perm.get("approval_id", ""),
                            "decision": "interrupted_void",
                            "channel": "startup_sweep",
                        },
                        tool_call_id=perm.get("tool_call_id") or None,
                        executor_id=perm.get("executor_id") or None,
                    )
                    voided.append({**perm, "agent_slug": agent_slug, "run_id": run_id})
                writer.emit(
                    "run_ended",
                    {"status": "interrupted", "reason": "process restart"},
                )
            finally:
                writer.close()
        return voided

    def _pending_permissions(self, agent_slug: str, run_id: str) -> list[dict]:
        """Permission requests in one run with no terminal decision yet."""
        by_id: dict[str, dict] = {}
        for ev in self.read_events(agent_slug, run_id):
            if ev.get("type") != "permission":
                continue
            p = ev.get("payload", {})
            aid = p.get("approval_id", "")
            if not aid:
                continue
            if p.get("status") == "pending":
                by_id[aid] = {
                    "approval_id": aid,
                    "summary": p.get("summary", ""),
                    "tool_call_id": ev.get("tool_call_id", ""),
                    "executor_id": ev.get("executor_id", ""),
                }
            elif p.get("decision"):
                by_id.pop(aid, None)
        return list(by_id.values())


# ---------------------------------------------------------------------------
# Process-wide store
# ---------------------------------------------------------------------------

_store: RunStore | None = None


def get_run_store() -> RunStore:
    global _store
    if _store is None:
        _store = RunStore()
    return _store


def set_run_store(store: RunStore | None) -> None:
    """Test seam: swap the process-wide store (None resets to default)."""
    global _store
    _store = store
