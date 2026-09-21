import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { useSeconds } from "@/hooks/useSeconds";
import { loopSummaryLabel, type LiveFleetOwner } from "@/hooks/useLiveLoops";

/**
 * Every loop, from every agent, one click from the rail (FEAT-123).
 *
 * A sheet in the pane's own slot — `AgentPanel`/`StrategySheet`/`AccountDock`'s
 * — rather than a fourth kind of overlay: opening it puts whatever the pane
 * held away, the same rule that already governs those three. A row's click is
 * `onOpenLoop`, the exact call the Agent panel's own strategy cards make
 * (`onPanelOpenStrategy` in `AgentChatTab`), generalised to an arbitrary agent.
 */
export function LoopsPanel({
  loops,
  isLoading,
  onOpenLoop,
  onClose,
}: {
  loops: LiveFleetOwner[];
  isLoading: boolean;
  onOpenLoop: (agentSlug: string, strategySlug: string) => void;
  onClose: () => void;
}) {
  // Alive only while a loop is actually running: a panel showing only paused
  // loops, or none at all, costs no interval (`useSeconds`'s own contract).
  const nowMs = useSeconds(loops.some((o) => o.live.status === "running"));

  return (
    <WorkspaceSheet
      title="Loops"
      subtitle="Every live tick loop, across every agent"
      onClose={onClose}
      fullscreen={false}
    >
      <div
        data-testid="loops-panel"
        className="flex min-h-0 flex-1 flex-col overflow-y-auto"
      >
        {loops.length === 0 ? (
          <p className="p-3 text-[11px] text-[var(--color-text-muted)]">
            {isLoading ? "Loading…" : "Nothing looping right now"}
          </p>
        ) : (
          loops.map((owner) => (
            <LoopRow
              key={`${owner.agentSlug}/${owner.strategySlug}`}
              owner={owner}
              nowMs={nowMs}
              onOpen={() => onOpenLoop(owner.agentSlug, owner.strategySlug)}
            />
          ))
        )}
      </div>
    </WorkspaceSheet>
  );
}

function LoopRow({
  owner,
  nowMs,
  onOpen,
}: {
  owner: LiveFleetOwner;
  nowMs: number;
  onOpen: () => void;
}) {
  const { live } = owner;
  const running = live.status === "running";
  return (
    <button
      type="button"
      data-loop-row={`${owner.agentSlug}/${owner.strategySlug}`}
      onClick={onOpen}
      title={`Open ${owner.agentName} — ${owner.strategyName}`}
      className="flex w-full flex-col gap-0.5 border-b border-[var(--color-border)] px-3 py-2 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
    >
      <div className="flex items-baseline gap-1.5">
        <span
          className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${
            running ? "bg-emerald-400" : "bg-amber-400"
          }`}
        />
        <span className="min-w-0 truncate text-[11px] font-semibold text-[var(--color-text)]">
          {owner.agentName}
        </span>
        <span className="min-w-0 truncate text-[11px] text-[var(--color-text-muted)]">
          {owner.strategyName}
        </span>
      </div>
      <div className="pl-3 font-mono text-[10px] text-[var(--color-text-muted)]">
        {loopSummaryLabel(live, nowMs)}
      </div>
    </button>
  );
}
