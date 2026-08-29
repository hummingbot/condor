import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  ArrowLeft,
  Brain,
  CircleDot,
  ExternalLink,
  History,
  MessageSquareText,
  Plus,
  Repeat,
  ScrollText,
  Server,
  Trash2,
  Wrench,
} from "lucide-react";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { AgentKnowledge } from "@/components/agent/AgentKnowledge";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { ActivityFeed } from "@/components/agent/ActivityFeed";
import { EntityCard } from "@/components/agent/EntityCard";
import { BrainPicker } from "@/components/chat/BrainPicker";
import { ReportBrowser } from "@/components/routines/ReportBrowser";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { useSessionOptions } from "@/hooks/useChat";
import { CHAT_SLUG, type StrategySummary, api } from "@/lib/api";

// ── Create Strategy Dialog ──

function CreateStrategyDialog({
  agentSlug,
  open,
  onClose,
}: {
  agentSlug: string;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
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
      navigate(`/agents/${agentSlug}/strategies/${strategy.slug}`);
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

// ── Server pin ──

/**
 * Which server this Agent's tools trade on, wherever it runs.
 *
 * A pin beats the chat's ambient selection everywhere the Agent is used —
 * chatted, consulted or looped — so it is the Agent's decision, and this is the
 * page that owns it. Before this it could only be changed by hand-editing
 * AGENT.md front matter, which is why a locked chat chip links here.
 */
function ServerPinPicker({ slug, serverName }: { slug: string; serverName: string }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  // State, not a ref: the portalled panel only gets coordinates once a render
  // has handed it the resolved trigger element.
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  const { data: servers } = useQuery({
    queryKey: ["servers"],
    queryFn: api.getServers,
    enabled: open,
  });

  const pin = useMutation({
    mutationFn: (name: string) => api.updateAgentConfig(slug, { server_name: name }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent", slug] }),
  });

  const choose = (name: string) => {
    setOpen(false);
    if (name !== serverName) pin.mutate(name);
  };

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen((v) => !v)}
        disabled={pin.isPending}
        aria-expanded={open}
        aria-haspopup="listbox"
        title={
          serverName
            ? `Pinned to ${serverName} — every run uses this server`
            : "No pin: follows whichever server the chat is on"
        }
        className={`flex items-center gap-1 rounded border px-2.5 py-1 transition-colors disabled:opacity-50 ${
          serverName
            ? "border-emerald-500/30 bg-emerald-500/10 font-mono text-emerald-400 hover:border-emerald-500/60"
            : "border-dashed border-[var(--color-border)] bg-[var(--color-surface)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-text)]"
        }`}
      >
        <Server className="h-3 w-3" /> {serverName || "No server pin"}
      </button>

      {/* Portalled, not `absolute`: this is the last chip in a `flex-wrap` row
          inside `main`'s `overflow-auto`, so a 220px panel hanging off its left
          edge is both clipped and, on a narrow window, off the right of the
          page. The `maxHeight` also travels as a prop — a Tailwind `max-h-*`
          would lose to the inline height the portalled panel sets — so a long
          server list scrolls inside the panel instead of past the fold. */}
      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={() => setOpen(false)}
        align="left"
        maxHeight={288}
        role="listbox"
        className="min-w-[220px] py-0.5"
      >
        <div className="px-2.5 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Pin to server
        </div>
        {(servers ?? []).map((s) => (
          <button
            key={s.name}
            onClick={() => choose(s.name)}
            className={`flex w-full items-center gap-2 px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
              s.name === serverName
                ? "font-medium text-[var(--color-primary)]"
                : "text-[var(--color-text)]"
            }`}
          >
            <CircleDot
              className={`h-2.5 w-2.5 shrink-0 ${
                s.online ? "text-[var(--color-green)]" : "text-[var(--color-text-muted)]"
              }`}
            />
            <span className="truncate">{s.name}</span>
          </button>
        ))}
        <button
          onClick={() => choose("")}
          className={`mt-0.5 w-full border-t border-[var(--color-border)] px-2.5 py-1.5 text-left text-xs hover:bg-[var(--color-surface-hover)] ${
            serverName ? "text-[var(--color-text)]" : "font-medium text-[var(--color-primary)]"
          }`}
        >
          No pin — follow the chat's selection
        </button>
      </AnchoredMenu>
    </>
  );
}

/**
 * Which model this Agent answers on, wherever it runs.
 *
 * The chat's picker writes the same field through the same endpoint, so the
 * two doors cannot disagree — and hand-editing front matter stops being the
 * only way to move an Agent's brain. `BrainPicker` is reused rather than
 * reimplemented so this offers exactly the model list the chat does,
 * OpenRouter and custom endpoints included; `agentBindings` is left empty
 * because the identity is already decided — this page *is* the Agent.
 */
function ModelPicker({ slug, agentKey }: { slug: string; agentKey: string }) {
  const queryClient = useQueryClient();
  const { agents, customProviders } = useSessionOptions();

  const pick = useMutation({
    mutationFn: (key: string) => api.updateAgentConfig(slug, { agent_key: key }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["agent", slug] }),
  });

  return (
    <BrainPicker
      agents={agents}
      customProviders={customProviders}
      selectedAgentKey={agentKey}
      onSelect={(sel) => {
        if (sel.agentKey !== undefined && sel.agentKey !== agentKey) {
          pick.mutate(sel.agentKey);
        }
      }}
      disabled={pick.isPending}
    />
  );
}

// ── Agent Detail Page ──

export function AgentDetail() {
  const { slug } = useParams<{ slug: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const [showCreate, setShowCreate] = useState(false);
  const [showDeleteConfirm, setShowDeleteConfirm] = useState(false);
  const [deleteStrategy, setDeleteStrategy] = useState<StrategySummary | null>(null);
  const [showRoutinesBrowser, setShowRoutinesBrowser] = useState(false);

  // Routine instances for ReportBrowser (routines live at the agent level,
  // shared across all of this agent's strategies)
  const { data: routineInstances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    enabled: showRoutinesBrowser,
    refetchInterval: 5000,
  });

  const deleteAgentMutation = useMutation({
    mutationFn: () => api.deleteAgent(slug!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agents"] });
      navigate("/");
    },
  });

  // Every Agent is loopable. This brings its implicit default playbook on disk
  // so the normal strategy UI (config, start, sessions) can drive the loop.
  const createDefaultLoopMutation = useMutation({
    mutationFn: () => api.createDefaultStrategy(slug!),
    onSuccess: (strategy) => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
      navigate(`/agents/${slug}/strategies/${strategy.slug}`);
    },
  });

  const deleteStrategyMutation = useMutation({
    mutationFn: () => api.deleteStrategy(slug!, deleteStrategy!.slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["agent", slug] });
      queryClient.invalidateQueries({ queryKey: ["agent-brain", slug] });
      setDeleteStrategy(null);
    },
  });

  const { data: agent, isLoading, error } = useQuery({
    queryKey: ["agent", slug],
    queryFn: () => api.getAgent(slug!),
    enabled: !!slug,
    refetchInterval: 5000,
  });

  if (error && !agent) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center">
        <div className="max-w-sm rounded-lg border border-red-500/30 bg-[var(--color-surface)] p-8 text-center">
          <AlertCircle className="mx-auto mb-3 h-10 w-10 text-[var(--color-red)]" />
          <h2 className="mb-1 text-lg font-semibold">Failed to Load Agent</h2>
          <p className="text-sm text-[var(--color-text-muted)]">
            {error instanceof Error ? error.message : "An unexpected error occurred."}
          </p>
          <button
            onClick={() => navigate("/")}
            className="mt-4 inline-flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
          >
            <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
          </button>
        </div>
      </div>
    );
  }

  if (isLoading || !agent) {
    return (
      <div className="flex h-64 items-center justify-center text-[var(--color-text-muted)]">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const strategies = agent.strategies || [];
  const running = strategies.filter((s) => s.status === "running");
  const isRunning = running.length > 0;

  /**
   * This page's Strategies tab — the grid, not the panel's read-only list.
   *
   * `AgentKnowledge` renders a link list for the chat sheet, where a strategy is
   * something you glance at; here it is something you create, open and delete,
   * so the page hands its own body into that slot rather than the panel growing
   * two modes.
   */
  const strategiesTab =
    strategies.length === 0 ? (
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
        <div className="flex items-center gap-2">
          <button
            onClick={() => createDefaultLoopMutation.mutate()}
            disabled={createDefaultLoopMutation.isPending}
            className="flex items-center gap-2 rounded-lg bg-[var(--color-primary)] px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
          >
            <CircleDot className="h-4 w-4" />
            {createDefaultLoopMutation.isPending ? "Creating…" : "Use default loop"}
          </button>
          <button
            onClick={() => setShowCreate(true)}
            className="flex items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2 text-sm font-medium text-[var(--color-text)]"
          >
            <Plus className="h-4 w-4" />
            New Strategy
          </button>
        </div>
        {createDefaultLoopMutation.isError && (
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
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          {strategies.map((strategy) => (
            <EntityCard
              key={strategy.slug}
              entity={strategy}
              icon={Repeat}
              deleteLabel="Delete strategy"
              onClick={() => navigate(`/agents/${agent.slug}/strategies/${strategy.slug}`)}
              onDelete={() => setDeleteStrategy(strategy)}
            />
          ))}
        </div>
      </div>
    );

  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-6">
        <button
          onClick={() => navigate("/")}
          className="mb-3 flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
        >
          <ArrowLeft className="h-3.5 w-3.5" /> Back to Agents
        </button>

        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]">
                <Brain className="h-4 w-4" />
              </div>
              <h1 className="text-xl font-bold text-[var(--color-text)]">{agent.name}</h1>
            </div>
            {agent.description && (
              <p className="mt-1 text-sm text-[var(--color-text-muted)]">{agent.description}</p>
            )}
            <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
              <span className="rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 font-mono">
                {agent.slug}
              </span>
              <ModelPicker slug={agent.slug} agentKey={agent.agent_key} />
              {agent.tools && agent.tools.length > 0 && (
                <span className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
                  <Wrench className="h-3 w-3" /> {agent.tools.length} tool{agent.tools.length !== 1 ? "s" : ""}
                </span>
              )}
              <ServerPinPicker slug={agent.slug} serverName={agent.server_name} />
            </div>
          </div>
          <div className="flex items-center gap-2">
            {/* `/?agent=` and not `/agents?agent=`: the chat workspace is `/`,
                and the `/agents` route is a bare Navigate to `/` that drops the
                query string with it — so this used to land on the chat home
                without focusing anyone. It matters more now that Knowledge in
                the chat comes straight here: this is the way back.

                Labelled "Open chat", not "Chat": it lands in the workspace,
                which is a different surface from the bubble docked on this
                page, and it continues the live conversation with this agent
                when there is one rather than always starting another. The bare
                verb read as "go back to the conversation I am in" and cost a
                tester their place in it. */}
            <button
              onClick={() => navigate(`/?agent=${encodeURIComponent(agent.slug)}`)}
              className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
              title={`Open your chat with ${agent.name} in the workspace — continues the live conversation if there is one`}
            >
              <MessageSquareText className="h-3.5 w-3.5" />
              <span className="hidden sm:inline">Open chat</span>
            </button>
            {/* Condor is the default agent: deleting its AGENT.md would leave
                every unbound session without instructions or a model, so the
                store refuses it. Its page is a normal destination now that the
                chat's Knowledge link lands here, so the button is not offered
                rather than offered and refused. */}
            {agent.slug !== CHAT_SLUG && (
              <button
                onClick={() => setShowDeleteConfirm(true)}
                disabled={isRunning}
                className="flex items-center gap-1.5 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-semibold text-red-400 transition-all hover:bg-red-500/20 disabled:cursor-not-allowed disabled:opacity-30"
                title={isRunning ? "Stop all strategies before deleting" : "Delete agent"}
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span className="hidden sm:inline">Delete</span>
              </button>
            )}
          </div>
        </div>
      </div>

      {/* The same panel the chat opens behind a conversation, with this page's
          own Strategies grid dropped into that tab and Activity added — so
          reading an agent and changing it are one place, whichever door you
          came through. */}
      <AgentKnowledge
        slug={agent.slug}
        slots={{
          strategies: strategiesTab,
          routinesAction: (
            <div className="flex shrink-0 items-center gap-1.5">
              <button
                onClick={() => setShowRoutinesBrowser(true)}
                className="flex shrink-0 items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
                title="Every run these routines produced, and their reports"
              >
                <ScrollText className="h-3 w-3" /> Reports
              </button>
              {/* The full-width library, which is no longer in the main nav
                  (FEAT-077) — this and the chat's pane are its doors. */}
              <Link
                to={`/routines?agent=${agent.slug}`}
                className="flex shrink-0 items-center gap-1 px-1 py-1 text-[11px] text-[var(--color-text-muted)]/70 transition-colors hover:text-[var(--color-text)]"
                title="The full library, on its own page"
              >
                <ExternalLink className="h-3 w-3" /> Full library
              </Link>
            </div>
          ),
        }}
        extraTabs={[
          {
            id: "activity",
            label: "Activity",
            icon: <History className="h-3.5 w-3.5" />,
            // Everything this agent actually did — the tasks handed to it in
            // the background and the consults it answered — this run's and
            // every earlier one, read back from disk (FEAT-058).
            render: () => <ActivityFeed agent={agent.slug} />,
          },
        ]}
      />

      {/* Routines ReportBrowser (full-screen overlay, filtered to this agent) */}
      {showRoutinesBrowser && (
        <ReportBrowser
          initialSourceTypeFilter={slug}
          instances={routineInstances}
          onClose={() => setShowRoutinesBrowser(false)}
        />
      )}

      {/* Create Strategy Dialog */}
      <CreateStrategyDialog
        agentSlug={agent.slug}
        open={showCreate}
        onClose={() => setShowCreate(false)}
      />

      {/* Delete Agent Confirmation */}
      <ConfirmDialog
        open={showDeleteConfirm}
        title="Delete Agent"
        isPending={deleteAgentMutation.isPending}
        isError={deleteAgentMutation.isError}
        errorText="Failed to delete agent. It may have running strategies."
        onConfirm={() => deleteAgentMutation.mutate()}
        onClose={() => setShowDeleteConfirm(false)}
      >
        Delete <strong className="text-[var(--color-text)]">{agent.name}</strong> and all its strategies? This cannot be undone.
      </ConfirmDialog>

      {/* Delete Strategy Confirmation */}
      <ConfirmDialog
        open={!!deleteStrategy}
        title="Delete Strategy"
        isPending={deleteStrategyMutation.isPending}
        isError={deleteStrategyMutation.isError}
        errorText="Failed to delete strategy. It may be running."
        onConfirm={() => deleteStrategyMutation.mutate()}
        onClose={() => setDeleteStrategy(null)}
      >
        Delete <strong className="text-[var(--color-text)]">{deleteStrategy?.name}</strong>? This cannot be undone.
      </ConfirmDialog>
    </div>
  );
}
