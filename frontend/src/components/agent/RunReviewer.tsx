import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  FileDown,
  ListTree,
  Loader2,
  Lock,
  Megaphone,
  X,
} from "lucide-react";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { AgentPnlChart } from "@/components/agent/AgentPnlChart";
import { KindBadge, RUN_STATUS_COLORS, TRADING_KINDS } from "@/components/agent/runStyles";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { type RunEvent, type RunMeta, api } from "@/lib/api";
import {
  formatCompactUsd,
  formatCurrencyPnl,
  formatDateTime,
  formatToolName,
} from "@/lib/formatters";

// ── Payload helpers (event payloads are untyped JSON) ──

function asText(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

function asNumber(v: unknown): number {
  return typeof v === "number" ? v : 0;
}

// PnL timeline from tick_completed events (metrics.total_pnl over time).
function eventsToPnlPoints(events: RunEvent[]): { time: number; value: number }[] {
  const points: { time: number; value: number }[] = [];
  for (const ev of events) {
    if (ev.type !== "tick_completed") continue;
    const metrics = (ev.payload?.metrics ?? {}) as Record<string, unknown>;
    if (typeof metrics.total_pnl !== "number") continue;
    points.push({ time: Math.floor(ev.ts), value: metrics.total_pnl });
  }
  return points;
}

// ── Tool call chip (expandable input/output) ──

function ToolCallChip({ payload }: { payload: Record<string, unknown> }) {
  const [expanded, setExpanded] = useState(false);
  const name = asText(payload.name) || "unknown";
  const status = asText(payload.status);
  const input = asText(payload.input);
  const output = asText(payload.output);
  const hasDetails = !!(input || output);
  const isOk = status === "success" || status === "completed";
  const isErr = status === "error" || status === "failed";
  const dotColor = isOk ? "bg-emerald-400" : isErr ? "bg-red-400" : "bg-[var(--color-text-muted)]";

  return (
    <div className="w-full rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)]">
      <button
        onClick={() => hasDetails && setExpanded(!expanded)}
        className={`flex w-full items-center justify-between px-2.5 py-1.5 text-left transition-colors ${hasDetails ? "hover:bg-[var(--color-surface-hover)]" : "cursor-default"}`}
      >
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
          <span className="text-[11px] font-medium text-[var(--color-text)]">{formatToolName(name)}</span>
          <span className="font-mono text-[10px] text-[var(--color-text-muted)]/60">{name}</span>
          {payload.mutating === true && (
            <span className="rounded bg-amber-500/10 px-1 py-0.5 text-[8px] font-bold uppercase text-amber-400">mutating</span>
          )}
        </div>
        {hasDetails &&
          (expanded ? <ChevronDown className="h-3 w-3 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3 w-3 text-[var(--color-text-muted)]" />)}
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-[var(--color-border)]/30 p-3">
          {input && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Input</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">{input}</pre>
            </div>
          )}
          {output && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Output</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">{output}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── One event row ──

function EventRow({ ev }: { ev: RunEvent }) {
  const p = ev.payload ?? {};
  switch (ev.type) {
    case "run_started": {
      const task = asText(p.task);
      if (!task) return null;
      return (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Task</h4>
          <div className="chat-markdown text-sm text-[var(--color-text)]">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{task}</ReactMarkdown>
          </div>
        </div>
      );
    }
    case "tick_started":
      return (
        <div className="flex items-center gap-2 pt-2">
          <span className="rounded-md bg-[var(--color-surface-hover)] px-2 py-0.5 font-mono text-xs font-bold text-[var(--color-text)]">
            Tick #{ev.tick ?? "?"}
          </span>
          <span className="text-[10px] text-[var(--color-text-muted)]">{formatDateTime(ev.ts * 1000)}</span>
          <span className="h-px flex-1 bg-[var(--color-border)]/50" />
        </div>
      );
    case "tick_completed": {
      const metrics = (p.metrics ?? {}) as Record<string, unknown>;
      const pnl = asNumber(metrics.total_pnl);
      return (
        <div className="flex flex-wrap items-center gap-3 px-1 text-[11px] text-[var(--color-text-muted)]">
          <span>tick #{ev.tick ?? "?"} complete</span>
          <span>{asNumber(p.actions)} action{asNumber(p.actions) !== 1 ? "s" : ""}</span>
          {"total_pnl" in metrics && (
            <span className={pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}>
              PnL {formatCurrencyPnl(pnl)}
            </span>
          )}
          {"open_count" in metrics && <span>{asNumber(metrics.open_count)} open</span>}
          {typeof p.duration === "number" && <span>{(p.duration as number).toFixed(1)}s</span>}
        </div>
      );
    }
    case "tool_call":
      return <ToolCallChip payload={p} />;
    case "permission":
      return (
        <div className="flex items-center gap-2 rounded-md border border-amber-500/20 bg-amber-500/5 px-2.5 py-1.5 text-[11px] text-amber-400">
          <Lock className="h-3 w-3 shrink-0" />
          <span className="font-medium">{asText(p.decision) || asText(p.status) || "pending"}</span>
          <span className="min-w-0 truncate text-[var(--color-text-muted)]">{asText(p.summary)}</span>
        </div>
      );
    case "state_snapshot": {
      const blocks: { label: string; text: string }[] = [];
      for (const key of ["decision", "state", "result", "response"] as const) {
        const text = asText(p[key]);
        if (text) blocks.push({ label: key, text });
      }
      if (blocks.length === 0) return null;
      return (
        <div className="space-y-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          {blocks.map((b) => (
            <div key={b.label}>
              <h4 className="mb-1.5 text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">{b.label}</h4>
              <div className="chat-markdown text-sm text-[var(--color-text)]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{b.text}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      );
    }
    case "directive":
      return (
        <div className="flex items-center gap-2 rounded-md border border-sky-500/20 bg-sky-500/5 px-2.5 py-1.5 text-[11px] text-sky-400">
          <Megaphone className="h-3 w-3 shrink-0" />
          <span>Directive {p.acked ? "(acked)" : "(queued)"}:</span>
          <span className="min-w-0 truncate text-[var(--color-text)]">{asText(p.text)}</span>
        </div>
      );
    case "notification":
      return (
        <div className="px-1 text-[11px] text-[var(--color-text-muted)]">🔔 {asText(p.text)}</div>
      );
    case "error":
      return (
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-3">
          <div className="mb-1 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-red-400">
            <AlertTriangle className="h-3.5 w-3.5" /> Error
          </div>
          <pre className="whitespace-pre-wrap font-mono text-xs text-red-300">{asText(p.message)}</pre>
        </div>
      );
    case "run_ended":
      return (
        <div className="flex items-center gap-2 border-t border-[var(--color-border)]/50 pt-3 text-xs">
          <span className={`font-bold uppercase ${RUN_STATUS_COLORS[asText(p.status)] ?? "text-[var(--color-text-muted)]"}`}>
            {asText(p.status) || "ended"}
          </span>
          {asText(p.reason) && <span className="text-[var(--color-text-muted)]">{asText(p.reason)}</span>}
          <span className="ml-auto text-[10px] text-[var(--color-text-muted)]">{formatDateTime(ev.ts * 1000)}</span>
        </div>
      );
    default:
      return null;
  }
}

// ── Executors summary for one run (controller_id == run_id) ──

function RunExecutorsPanel({ runId }: { runId: string }) {
  const { data } = useQuery({
    queryKey: ["run-executors", runId],
    queryFn: () => api.getRunExecutors(runId),
    refetchInterval: 10000,
  });
  const perf = data?.performance;
  const executors = data?.executors ?? [];
  if (!perf || executors.length === 0) return null;
  const pnlColor = perf.total_pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5">
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Net PnL</span>
          <span className={`font-mono text-sm font-semibold ${pnlColor}`}>{formatCurrencyPnl(perf.total_pnl)}</span>
        </div>
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Volume</span>
          <span className="font-mono text-sm text-[var(--color-text)]">{formatCompactUsd(perf.volume)}</span>
        </div>
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Fees</span>
          <span className="font-mono text-sm text-[var(--color-text-muted)]">{formatCompactUsd(perf.fees)}</span>
        </div>
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Executors</span>
          <span className="text-sm text-[var(--color-text)]">
            {executors.length}
            {perf.open_count > 0 && <span className="ml-1 text-emerald-400">({perf.open_count} open)</span>}
          </span>
        </div>
        <div>
          <span className="block text-[9px] uppercase tracking-wider text-[var(--color-text-muted)]">Win Rate</span>
          <span className="font-mono text-sm text-[var(--color-text)]">{(perf.win_rate * 100).toFixed(0)}%</span>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
        <table className="w-full text-xs">
          <thead>
            <tr className="text-left text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              <th className="px-3 py-2">Pair</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Side</th>
              <th className="px-3 py-2">Status</th>
              <th className="px-3 py-2 text-right">PnL</th>
              <th className="px-3 py-2 text-right">Volume</th>
            </tr>
          </thead>
          <tbody>
            {executors.map((ex) => (
              <tr key={ex.id} className="border-t border-[var(--color-border)]/40 font-mono">
                <td className="px-3 py-1.5 text-[var(--color-text)]">{ex.pair}</td>
                <td className="px-3 py-1.5 text-[var(--color-text-muted)]">{ex.type}</td>
                <td className={`px-3 py-1.5 ${ex.side?.toLowerCase() === "buy" || ex.side?.toLowerCase().includes("long") ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                  {ex.side || "—"}
                </td>
                <td className="px-3 py-1.5 text-[var(--color-text-muted)]">{ex.status}</td>
                <td className={`px-3 py-1.5 text-right ${ex.pnl >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]"}`}>
                  {formatCurrencyPnl(ex.pnl)}
                </td>
                <td className="px-3 py-1.5 text-right text-[var(--color-text-muted)]">{formatCompactUsd(ex.volume)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ── Run Reviewer (full-screen overlay: run history sidebar + event stream) ──

interface RunReviewerProps {
  slug: string;
  agentName: string;
  initialRunId: string;
  onClose: () => void;
}

export function RunReviewer({ slug, agentName, initialRunId, onClose }: RunReviewerProps) {
  const [selectedRunId, setSelectedRunId] = useState(initialRunId);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [showMarkdown, setShowMarkdown] = useState(false);

  useEscapeKey(true, onClose);

  const { data: runsData } = useQuery({
    queryKey: ["agent", slug, "runs"],
    queryFn: () => api.getAgentRuns(slug, undefined, 100),
    refetchInterval: 10000,
  });
  const runs = useMemo<RunMeta[]>(() => runsData?.runs ?? [], [runsData]);
  const selected = runs.find((r) => r.run_id === selectedRunId);

  const { data: detail } = useQuery({
    queryKey: ["run", selectedRunId, "events"],
    queryFn: () => api.getRun(selectedRunId, true),
    enabled: !!selectedRunId && !showMarkdown,
    refetchInterval: selected?.status === "running" ? 5000 : false,
  });

  const { data: exportData } = useQuery({
    queryKey: ["run", selectedRunId, "export"],
    queryFn: () => api.getRunExport(selectedRunId),
    enabled: !!selectedRunId && showMarkdown,
  });

  const events = useMemo<RunEvent[]>(() => detail?.events ?? [], [detail]);
  const pnlPoints = useMemo(() => eventsToPnlPoints(events), [events]);
  const meta = detail ?? selected;
  const isTrading = !!meta && TRADING_KINDS.has(meta.kind as string);
  const currentIdx = runs.findIndex((r) => r.run_id === selectedRunId);

  return (
    <div className="fixed inset-0 z-50 flex bg-[var(--color-bg)]">
      {/* Left sidebar: run history */}
      <div
        className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all ${
          sidebarCollapsed ? "w-12" : "w-72"
        }`}
      >
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2.5">
          {!sidebarCollapsed && (
            <span className="text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Runs</span>
          )}
          <button
            onClick={() => setSidebarCollapsed(!sidebarCollapsed)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          >
            {sidebarCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronLeft className="h-3.5 w-3.5" />}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {runs.map((r) => {
            const isActive = r.run_id === selectedRunId;
            if (sidebarCollapsed) {
              return (
                <button
                  key={r.run_id}
                  onClick={() => setSelectedRunId(r.run_id)}
                  className={`flex w-full items-center justify-center py-3 text-xs font-bold transition-colors ${
                    isActive
                      ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                  }`}
                  title={`${r.kind} #${r.display_seq}`}
                >
                  {r.display_seq}
                </button>
              );
            }
            return (
              <button
                key={r.run_id}
                onClick={() => setSelectedRunId(r.run_id)}
                className={`w-full px-3 py-2.5 text-left transition-all ${
                  isActive
                    ? "border-l-2 border-l-[var(--color-primary)] bg-[var(--color-primary)]/5"
                    : "border-l-2 border-l-transparent hover:bg-[var(--color-surface-hover)]"
                }`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="flex min-w-0 items-center gap-1.5">
                    <KindBadge kind={r.kind} />
                    <span className={`text-xs font-medium ${isActive ? "text-[var(--color-text)]" : "text-[var(--color-text-muted)]"}`}>
                      #{r.display_seq}
                    </span>
                  </span>
                  <span className={`text-[10px] font-bold uppercase ${RUN_STATUS_COLORS[r.status] ?? "text-[var(--color-text-muted)]"}`}>
                    {r.status}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center gap-1.5">
                  {r.task && (
                    <span className="min-w-0 truncate text-[10px] text-[var(--color-text-muted)]">{r.task.split("\n")[0]}</span>
                  )}
                  <span className="ml-auto shrink-0 text-[10px] text-[var(--color-text-muted)]/60">
                    {r.started_at ? formatDateTime(r.started_at * 1000) : ""}
                  </span>
                </div>
              </button>
            );
          })}
          {runs.length === 0 && (
            <p className="p-4 text-xs text-[var(--color-text-muted)]">No runs yet.</p>
          )}
        </div>

        {!sidebarCollapsed && (
          <div className="border-t border-[var(--color-border)] px-3 py-2 text-[9px] text-[var(--color-text-muted)]/40">
            esc close
          </div>
        )}
      </div>

      {/* Main content */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar */}
        <div className="flex items-center justify-between border-b border-[var(--color-border)] px-4 py-2">
          <div className="flex min-w-0 items-center gap-3">
            {meta && <KindBadge kind={meta.kind} />}
            <h2 className="text-sm font-semibold text-[var(--color-text)]">
              Run #{meta?.display_seq ?? "?"}
            </h2>
            {meta && (
              <span className={`text-xs font-bold uppercase ${RUN_STATUS_COLORS[meta.status] ?? "text-[var(--color-text-muted)]"}`}>
                {meta.status}
                {meta.reason ? ` · ${meta.reason}` : ""}
              </span>
            )}
            {meta?.model && (
              <span className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 font-mono text-[10px] text-[var(--color-text-muted)]">
                {meta.model}
              </span>
            )}
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowMarkdown((v) => !v)}
              className={`flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-all ${
                showMarkdown
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`}
              title="Toggle markdown transcript"
            >
              {showMarkdown ? <ListTree className="h-3.5 w-3.5" /> : <FileDown className="h-3.5 w-3.5" />}
              {showMarkdown ? "Events" : "Transcript"}
            </button>
            <span className="text-xs text-[var(--color-text-muted)]">{agentName}</span>
            {currentIdx >= 0 && (
              <span className="text-[10px] text-[var(--color-text-muted)]">
                {runs.length - currentIdx} / {runs.length}
              </span>
            )}
            <button
              onClick={onClose}
              className="ml-1 rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close (Esc)"
            >
              <X className="h-4 w-4" />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-y-auto p-4">
          {!meta ? (
            <div className="flex h-32 items-center justify-center">
              <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
            </div>
          ) : (
            <div className="mx-auto max-w-4xl space-y-4">
              {/* Meta chips */}
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-[var(--color-text-muted)]">
                <span className="font-mono">{meta.run_id}</span>
                {meta.started_at && <span>started {formatDateTime(meta.started_at * 1000)}</span>}
                {meta.ended_at && <span>ended {formatDateTime(meta.ended_at * 1000)}</span>}
                {typeof meta.event_count === "number" && <span>{meta.event_count} events</span>}
                {meta.scheduled_for && <span>scheduled for {meta.scheduled_for}</span>}
              </div>

              {/* Executors + PnL (trading runs) */}
              {isTrading && <RunExecutorsPanel runId={meta.run_id} />}
              {pnlPoints.length > 1 && !showMarkdown && (
                <AgentPnlChart data={pnlPoints} height={220} title="PnL Timeline" />
              )}

              {showMarkdown ? (
                exportData ? (
                  <div className="chat-markdown rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5 text-sm text-[var(--color-text)]">
                    <ReactMarkdown remarkPlugins={[remarkGfm]}>{exportData.markdown}</ReactMarkdown>
                  </div>
                ) : (
                  <div className="flex h-32 items-center justify-center">
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
                  </div>
                )
              ) : detail ? (
                events.length <= 1 ? (
                  <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
                    <Check className="mx-auto mb-2 h-5 w-5 opacity-40" />
                    No events recorded for this run.
                  </p>
                ) : (
                  <div className="space-y-2">
                    {events.map((ev) => (
                      <EventRow key={ev.seq} ev={ev} />
                    ))}
                  </div>
                )
              ) : (
                <div className="flex h-32 items-center justify-center">
                  <Loader2 className="h-5 w-5 animate-spin text-[var(--color-text-muted)]" />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
