import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleDot, Plus, Repeat } from "lucide-react";
import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { EntityCard } from "@/components/agent/EntityCard";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api, type StrategySummary } from "@/lib/api";

/**
 * The loops this agent owns — read, created, opened and deleted.
 *
 * This was the agent page's own body, injected into `AgentKnowledge` through a
 * `slots.strategies` prop. It is a section of the agent, not of the page, so a
 * second host — the chat's agent panel (FEAT-081) — that forgot to inject it
 * would have shown an agent with no strategies at all. Built in here instead,
 * so every host gets the same seven sections and none of them can forget one.
 *
 * It reads `["agent", slug]` rather than the brain's `StrategyCard` list: the
 * grid shows PnL, sessions and live instances, which only the detail endpoint
 * carries. That endpoint is not cheap — it prices every session's executors
 * through the Hummingbot API — so the poll is conditional (PERF-305): an idle
 * agent is read once and a live one keeps the 5s cadence, because "running" is
 * exactly when a card's PnL and instances can still change. The agent page
 * polls the same key unconditionally and react-query takes the shortest
 * interval among observers, so page behaviour is unchanged; it is the chat's
 * agent panel, where nothing else observes the key, that used to pay 5s of
 * Hummingbot round-trips for an agent with no loop running.
 *
 * Opening a strategy hands it to `onOpenStrategy` when the host has somewhere
 * to put it — the chat's workspace pane does, and opens the same workbench the
 * page renders. Without one (the agent's own page) it navigates as before. It
 * used to always navigate, on the theory that starting a loop with real money
 * belonged on a page of its own; but the guard on that is the start dialog and
 * its confirmation, not the width of the window, and the cost was losing the
 * conversation that named the strategy in the first place.
 */
export function AgentStrategies({
  slug,
  dense = false,
  onOpenStrategy,
}: {
  slug: string;
  /** One column: the pane is 400–700px, where three cards are three slivers. */
  dense?: boolean;
  /**
   * Open this strategy in the host's own surface instead of navigating away.
   * Absent = the host is a page, and a page navigates.
   */
  onOpenStrategy?: (strategySlug: string) => void;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [showCreate, setShowCreate] = useState(false);
  const [deleteStrategy, setDeleteStrategy] = useState<StrategySummary | null>(
    null,
  );

  const { data: agent } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug),
    enabled: !!slug,
    // Only a live loop can change these cards; an idle agent is read once.
    refetchInterval: (q) =>
      q.state.data?.strategies.some((s) => s.status === "running") ? 5000 : false,
  });

  /** Both catalogues count strategies, so both are re-read after a change. */
  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: ["agent", slug] });
    queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
  };

  // Every Agent is loopable. This brings its implicit default playbook on disk
  // so the normal strategy UI (config, start, sessions) can drive the loop.
  const createDefaultLoop = useMutation({
    mutationFn: () => api.createDefaultStrategy(slug),
    onSuccess: (strategy) => {
      refresh();
      openNewStrategy(strategy.slug);
    },
  });

  /**
   * One door, wherever this is hosted.
   *
   * On a page that door is the run screen with its Runs disclosure open
   * (FEAT-099, FEAT-119): a card is a summary of what a loop has been *doing*,
   * and the runs are what it has been doing. The workbench is one disclosure
   * further down the same screen — it is where you operate a strategy, not
   * where you read it.
   *
   * The chat's pane still opens the workbench in the pane: three panes do not
   * fit a 640px column.
   */
  function openStrategy(strategySlug: string) {
    if (onOpenStrategy) onOpenStrategy(strategySlug);
    else
      navigate(
        `/agents/${slug}?open=runs&strategy=${encodeURIComponent(strategySlug)}`,
      );
  }

  /**
   * A strategy that has just been created has no runs to read.
   *
   * It needs a playbook and a Start, which is the workbench — so creation lands
   * there rather than on an empty rail.
   */
  function openNewStrategy(strategySlug: string) {
    if (onOpenStrategy) onOpenStrategy(strategySlug);
    else
      navigate(
        `/agents/${slug}?open=playbook&strategy=${encodeURIComponent(strategySlug)}`,
      );
  }

  const deleteMut = useMutation({
    mutationFn: () => api.deleteStrategy(slug, deleteStrategy!.slug),
    onSuccess: () => {
      refresh();
      setDeleteStrategy(null);
    },
  });

  const strategies = agent?.strategies ?? [];

  return (
    <>
      {strategies.length === 0 ? (
        <div className="flex h-56 flex-col items-center justify-center rounded-xl border border-dashed border-[var(--color-border)] bg-[var(--color-surface)]/50">
          <CircleDot className="mb-3 h-9 w-9 text-[var(--color-text-muted)]/30" />
          <p className="mb-1 text-sm font-medium text-[var(--color-text)]">
            No strategies yet
          </p>
          <p className="mb-4 max-w-md text-center text-xs text-[var(--color-text-muted)]">
            This agent can already be consulted and delegated to. To let it run on a
            loop, start from its default playbook — its own brain drives each tick —
            or write a dedicated one.
          </p>
          <div className="flex flex-wrap items-center justify-center gap-2">
            <button
              onClick={() => createDefaultLoop.mutate()}
              disabled={createDefaultLoop.isPending}
              className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
            >
              <CircleDot className="h-4 w-4" />
              {createDefaultLoop.isPending ? "Creating…" : "Use default loop"}
            </button>
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-sm font-medium text-[var(--color-text)]"
            >
              <Plus className="h-4 w-4" />
              New Strategy
            </button>
          </div>
          {createDefaultLoop.isError && (
            <p className="mt-3 text-xs text-[var(--color-red)]">
              Could not create the default loop.
            </p>
          )}
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-end">
            <button
              onClick={() => setShowCreate(true)}
              className="flex items-center gap-1.5 rounded-lg bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white transition-all hover:shadow-lg hover:shadow-[var(--color-primary)]/20"
            >
              <Plus className="h-3.5 w-3.5" />
              New Strategy
            </button>
          </div>
          {/* Up to three across on the page, one down the pane. Driven by the
              host's own `layout` rather than by a media query, because the
              window is wide in both cases — it is the column that is narrow. */}
          <div
            className={`grid gap-4 ${
              dense ? "grid-cols-1" : "grid-cols-1 md:grid-cols-2 lg:grid-cols-3"
            }`}
          >
            {strategies.map((strategy) => (
              <EntityCard
                key={strategy.slug}
                entity={strategy}
                icon={Repeat}
                deleteLabel="Delete strategy"
                onClick={() => openStrategy(strategy.slug)}
                onDelete={() => setDeleteStrategy(strategy)}
              />
            ))}
          </div>
        </div>
      )}

      <CreateStrategyDialog
        agentSlug={slug}
        open={showCreate}
        onCreated={openNewStrategy}
        onClose={() => setShowCreate(false)}
      />

      <ConfirmDialog
        open={!!deleteStrategy}
        title="Delete Strategy"
        isPending={deleteMut.isPending}
        isError={deleteMut.isError}
        errorText="Failed to delete strategy. It may be running."
        onConfirm={() => deleteMut.mutate()}
        onClose={() => setDeleteStrategy(null)}
      >
        Delete{" "}
        <strong className="text-[var(--color-text)]">
          {deleteStrategy?.name}
        </strong>
        ? This cannot be undone.
      </ConfirmDialog>
    </>
  );
}

// ── Create Strategy Dialog ──

function CreateStrategyDialog({
  agentSlug,
  open,
  onCreated,
  onClose,
}: {
  agentSlug: string;
  open: boolean;
  /** Land on the new strategy the same way opening an existing one lands. */
  onCreated: (strategySlug: string) => void;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  useEscapeKey(open, onClose);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [defaultContext, setDefaultContext] = useState("");

  const createMutation = useMutation({
    mutationFn: () =>
      api.createStrategy(agentSlug, { name, description, default_trading_context: defaultContext }),
    onSuccess: (strategy) => {
      queryClient.invalidateQueries({ queryKey: ["agent", agentSlug] });
      // The knowledge panel counts strategies off its own query.
      queryClient.invalidateQueries({ queryKey: ["agent-brain", agentSlug] });
      onClose();
      setName("");
      setDescription("");
      setDefaultContext("");
      onCreated(strategy.slug);
    },
  });

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] p-6 shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <h2 className="mb-4 text-lg font-semibold text-[var(--color-text)]">New Strategy</h2>

        <div className="space-y-4">
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Name
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. BRL Market Maker"
              className="w-full rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
              autoFocus
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Description
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="What does this strategy do?"
              rows={2}
              className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
            />
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Default Trading Context
            </label>
            <textarea
              value={defaultContext}
              onChange={(e) => setDefaultContext(e.target.value)}
              placeholder="e.g. Provide liquidity on BRL pairs, tight spreads, rebalance hourly..."
              rows={3}
              className="w-full resize-none rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-2 text-sm text-[var(--color-text)] placeholder-[var(--color-text-muted)]/50 outline-none transition-colors focus:border-[var(--color-primary)]"
            />
            <p className="mt-1 text-[11px] text-[var(--color-text-muted)]">
              Tactic that guides this playbook's tick decisions. Can be overridden per session.
            </p>
          </div>
        </div>

        <div className="mt-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="rounded-lg px-4 py-2 text-sm text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <button
            onClick={() => createMutation.mutate()}
            disabled={!name.trim() || createMutation.isPending}
            className="rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white transition-opacity disabled:opacity-40"
          >
            {createMutation.isPending ? "Creating..." : "Create Strategy"}
          </button>
        </div>
        {createMutation.isError && (
          <p className="mt-3 text-xs text-red-400">Failed to create strategy.</p>
        )}
      </div>
    </div>
  );
}
