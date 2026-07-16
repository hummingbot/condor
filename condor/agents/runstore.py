"""RunStore — one append-only JSONL event stream per run (§7.1).

    agents/{slug}/runs/{run_id}.jsonl

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
  ~16KB spills to ``runs/{run_id}.artifacts/`` and the event keeps a
  path+hash reference.
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
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RUN_KINDS = {"session", "experiment", "delegation", "consult", "scheduled"}

EVENT_TYPES = {
    "run_started",
    "tick_started",
    "tick_completed",
    "tool_call",
    "permission",
    "state_snapshot",
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

    def _artifacts_dir(self) -> Path:
        d = self.path.with_name(f"{self.run_id}.artifacts")
        d.mkdir(mode=0o700, exist_ok=True)
        return d

    def _spill_artifact(self, seq: int, type_: str, payload: dict) -> dict:
        raw = json.dumps(payload, ensure_ascii=False, default=str)
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        name = f"{seq:06d}-{type_}.json"
        apath = self._artifacts_dir() / name
        apath.write_text(raw)
        os.chmod(apath, 0o600)
        return {
            "artifact": f"{self.run_id}.artifacts/{name}",
            "sha256": digest,
            "bytes": len(raw.encode("utf-8")),
            "preview": raw[:_ARTIFACT_PREVIEW_CHARS],
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
                event["payload"] = self._spill_artifact(self._seq, type_, payload)
                line = json.dumps(event, ensure_ascii=False, default=str)
            self._f.write(line + "\n")
            self._f.flush()
            if type_ in _FSYNC_TYPES or (
                type_ == "tool_call" and payload.get("mutating")
            ):
                os.fsync(self._f.fileno())
            return event

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
        return self._root / agent_slug / "runs"

    def run_path(self, agent_slug: str, run_id: str) -> Path:
        return self.runs_dir(agent_slug) / f"{run_id}.jsonl"

    def find_run_path(self, run_id: str) -> Path | None:
        """Locate a run stream by opaque id (checks live writers first)."""
        w = self._writers.get(run_id)
        if w is not None:
            return w.path
        if not self._root.exists():
            return None
        for candidate in self._root.glob(f"*/runs/{run_id}.jsonl"):
            return candidate
        return None

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
        runs_dir = self.runs_dir(agent_slug)
        runs_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(runs_dir, 0o700)
        display_seq = sum(1 for _ in runs_dir.glob("*.jsonl")) + 1
        path = self.run_path(agent_slug, run_id)
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
        path = self.run_path(agent_slug, run_id)
        if not path.exists():
            found = self.find_run_path(run_id)
            if found is None:
                raise UnknownRunError(f"unknown run: {run_id}")
            path = found
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
        path = self.run_path(agent_slug, run_id)
        if not path.exists():
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
        """Run summaries for one agent, newest first."""
        runs_dir = self.runs_dir(agent_slug)
        if not runs_dir.exists():
            return []
        paths = sorted(
            runs_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
        )
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
        return sorted(p.parent.name for p in self._root.glob("*/runs") if p.is_dir())

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
        for path in self._root.glob("*/runs/*.jsonl"):
            run_id = path.stem
            if self.is_open(run_id):
                continue
            tail = self._tail_event(path)
            if tail is None or tail.get("type") == "run_ended":
                continue
            agent_slug = path.parent.parent.name
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
