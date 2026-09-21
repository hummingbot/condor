import { FlaskConical } from "lucide-react";

import { Fact, LastDeedLine } from "@/components/agent/EntityCard";
import { tickCountdownLabel } from "@/components/agent/workspace/fleet";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { useSeconds } from "@/hooks/useSeconds";
import {
  groupLoopsByAgent,
  loopStats,
  type LiveFleetOwner,
  type LoopCardStats,
} from "@/hooks/useLiveLoops";
import { countdown } from "@/lib/agent-attribution";
import type { AgentSummary } from "@/lib/api";
import { formatCurrencyPnl, formatRelativeTime, pnlColor } from "@/lib/formatters";

/**
 * Every loop, from every agent, one click from the rail (FEAT-123) — grouped by
 * agent and led by the decision and the PnL, not a bare row of session/tick
 * facts (FEAT-125: Federico's "and should be better presented").
 *
 * A sheet in the pane's own slot — `AgentPanel`/`StrategySheet`/`AccountDock`'s
 * — rather than a fourth kind of overlay: opening it puts whatever the pane
 * held away, the same rule that already governs those three. A card's click is
 * `onOpenLoop`, the exact call the Agent panel's own strategy cards make
 * (`onPanelOpenStrategy` in `AgentChatTab`), generalised to an arbitrary agent.
 */
export function LoopsPanel({
  loops,
  isLoading,
  agents,
  onOpenLoop,
  onClose,
}: {
  loops: LiveFleetOwner[];
  isLoading: boolean;
  /** The roster `AgentChatTab` already holds — joined in for PnL/session facts `fleet-map` does not carry. */
  agents: AgentSummary[];
  onOpenLoop: (agentSlug: string, strategySlug: string) => void;
  onClose: () => void;
}) {
  // Alive only while a loop is actually running: a panel showing only paused
  // loops, or none at all, costs no interval (`useSeconds`'s own contract).
  const nowMs = useSeconds(loops.some((o) => o.live.status === "running"));
  const groups = groupLoopsByAgent(loops);

  return (
    <WorkspaceSheet
      title="Loops"
      subtitle="Every live tick loop, across every agent"
      onClose={onClose}
      fullscreen={false}
    >
      <div
        data-testid="loops-panel"
        className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-3"
      >
        {loops.length === 0 ? (
          <p className="text-[11px] text-[var(--color-text-muted)]">
            {isLoading ? "Loading…" : "Nothing looping right now"}
          </p>
        ) : (
          [...groups.entries()].map(([agentSlug, group]) => (
            <div key={agentSlug} className="flex flex-col gap-2">
              {/* Only when more than one agent has a live loop — the
                  single-agent case renders its card(s) directly, with no
                  heading standing in for a group of one. */}
              {groups.size > 1 && (
                <div
                  data-loop-group={agentSlug}
                  className="flex items-center gap-1.5"
                >
                  <span
                    className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                      group.some((o) => o.live.status === "running")
                        ? "bg-emerald-400"
                        : "bg-amber-400"
                    }`}
                  />
                  <span className="min-w-0 truncate text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
                    {group[0].agentName}
                  </span>
                </div>
              )}
              {group.map((owner) => (
                <LoopCard
                  key={`${owner.agentSlug}/${owner.strategySlug}`}
                  owner={owner}
                  stats={loopStats(owner, agents)}
                  nowMs={nowMs}
                  onOpen={() => onOpenLoop(owner.agentSlug, owner.strategySlug)}
                />
              ))}
            </div>
          ))
        )}
      </div>
    </WorkspaceSheet>
  );
}

/**
 * One loop, stated in order of visual weight: is it alive and what did it just
 * make, then the tick facts that back that up.
 *
 * No card-level failure wash — a failed deed colors `LastDeedLine`'s own box,
 * not the card, the same rule every other "this failed" state in this codebase
 * follows.
 */
function LoopCard({
  owner,
  stats,
  nowMs,
  onOpen,
}: {
  owner: LiveFleetOwner;
  /** `null` when the `["fleet-map"]`/`["agents"]` polls have not agreed yet — the card degrades, it does not guess. */
  stats: LoopCardStats | null;
  nowMs: number;
  onOpen: () => void;
}) {
  const { live } = owner;
  const running = live.status === "running";
  const nowSec = nowMs / 1000;
  const due = live.lastTickAt > 0 ? live.lastTickAt + live.frequencySec - nowSec : null;

  return (
    <button
      type="button"
      data-loop-row={`${owner.agentSlug}/${owner.strategySlug}`}
      onClick={onOpen}
      title={`Open ${owner.agentName} — ${owner.strategyName}`}
      className={`w-full rounded-lg border p-3 text-left transition-colors ${
        running
          ? "border-emerald-500/20 bg-emerald-500/[0.03] hover:bg-emerald-500/[0.06]"
          : "border-[var(--color-border)] bg-[var(--color-surface)] hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      <div className="mb-2 flex items-center gap-1.5">
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${
            running ? "bg-emerald-400" : "bg-amber-400"
          }`}
        />
        <span className="min-w-0 truncate text-[11px] font-semibold text-[var(--color-text)]">
          {owner.agentName}
        </span>
        <span className="min-w-0 truncate text-[11px] text-[var(--color-text-muted)]">
          {owner.strategyName}
        </span>
        {/* Absent, not zero, when the roster join misses — a card is never
            allowed to fabricate a PnL it has not actually resolved. */}
        {stats && (
          <span
            data-loop-pnl
            className="ml-auto shrink-0 font-mono text-[11px] font-semibold"
            style={{ color: pnlColor(stats.latestSessionPnl) }}
          >
            {formatCurrencyPnl(stats.latestSessionPnl, "$")}
          </span>
        )}
      </div>

      {live.lastDid ? (
        <LastDeedLine did={live.lastDid} />
      ) : (
        live.lastAction && (
          <p
            className="mb-3 truncate text-[11px] text-[var(--color-text-muted)]"
            title={live.lastAction}
          >
            {live.lastAction}
          </p>
        )
      )}

      <div className="grid grid-cols-4 gap-2 border-t border-[var(--color-border)]/50 pt-2">
        <Fact label="Ticks" value={String(live.tickCount)} />
        <Fact
          label="Next tick"
          value={
            due === null ? (
              "—"
            ) : (
              // Overdue is named as overdue, amber, the same rule
              // `LoopBar`/`AgentRow`'s own countdown already applies — no new
              // banner, the tile's own colour says it.
              <span className={due <= 0 ? "text-amber-400" : undefined}>
                {tickCountdownLabel(due)}
              </span>
            )
          }
          sub={`every ${countdown(live.frequencySec)}`}
        />
        <Fact
          label="Last tick"
          value={live.lastTickAt > 0 ? formatRelativeTime(live.lastTickAt) : "—"}
        />
        <Fact
          label="Runs"
          // FEAT-123's own honest wording when the roster join misses: a
          // session number, not a fabricated run count.
          value={stats ? String(stats.sessionCount) : `Session ${live.sessionNum}`}
          chip={
            stats?.experimentCount ? (
              <span
                className="flex items-center gap-0.5 rounded bg-amber-500/10 px-1 py-0.5 text-[9px] font-bold uppercase text-amber-400"
                title={`${stats.experimentCount} dry run${stats.experimentCount === 1 ? "" : "s"} / single ticks`}
              >
                <FlaskConical className="h-2.5 w-2.5" />
                {stats.experimentCount} dry
              </span>
            ) : undefined
          }
        />
      </div>
    </button>
  );
}
