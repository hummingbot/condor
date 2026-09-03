import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft,
  Brain,
  CircleDot,
  MessageSquareText,
  Server,
  Trash2,
  Wrench,
} from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { AgentControls } from "@/components/agent/AgentControls";
import { BrainPicker } from "@/components/chat/BrainPicker";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useSessionOptions } from "@/hooks/useChat";
import { CHAT_SLUG, api, type AgentDetail, type StrategyDetail } from "@/lib/api";

// ── Server pin ──

/**
 * Which server this Agent's tools trade on, wherever it runs.
 *
 * A pin beats the chat's ambient selection everywhere the Agent is used —
 * chatted, consulted or looped — so it is the Agent's decision, and this is the
 * surface that owns it. Before this it could only be changed by hand-editing
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
          inside a header the workspace scrolls nothing of, so a 220px panel
          hanging off its left edge is both clipped and, on a narrow window, off
          the right of the page. The `maxHeight` also travels as a prop — a
          Tailwind `max-h-*` would lose to the inline height the portalled panel
          sets — so a long server list scrolls inside the panel instead of past
          the fold. */}
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
 * because the identity is already decided — this workspace *is* the Agent.
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

/**
 * Who this agent is, and everything you can do to it — on every view.
 *
 * The three shells this replaces each owned a slice of this row and none owned
 * all of it: the agent page had the pickers and no loop state, the workbench
 * had the loop controls and no identity, and the Lab had neither — not even
 * the agent's name. So reading a run meant not being able to see whether the
 * loop that wrote it was still running, and stopping it meant two navigations.
 *
 * It does not unmount when `?view=` changes, which is the whole point: the
 * header, the loop bar and the spine are the frame, and going deeper only ever
 * swaps the body inside it.
 */
export function WorkspaceHeader({
  agent,
  strategy,
  isRunning,
  onAskAgent,
  onDelete,
}: {
  agent: AgentDetail;
  /** The strategy in scope, once loaded — what the loop controls act on. */
  strategy: StrategyDetail | null;
  isRunning: boolean;
  /** Carry a request to the workspace at `/`, which is the surface that can send it. */
  onAskAgent: () => void;
  onDelete: () => void;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-[var(--color-border)] px-4 py-2.5">
      <div className="flex min-w-0 flex-wrap items-center gap-2 text-xs text-[var(--color-text-muted)]">
        {/* The way back, which the Lab had not at all and the agent page spent
            a whole row on. A link and not a `navigate`, so it can be opened in
            a tab like every other address in here. */}
        <Link
          to="/"
          className="flex items-center gap-1 transition-colors hover:text-[var(--color-text)]"
          title="Back to your agents"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
        </Link>
        <div className="flex h-6 w-6 items-center justify-center rounded-md bg-[var(--color-surface-hover)]">
          <Brain className="h-3.5 w-3.5" />
        </div>
        <h1 className="truncate text-sm font-bold text-[var(--color-text)]">
          {agent.name}
        </h1>
        {/* The only live indicator outside the chat rail, until now. */}
        {isRunning && (
          <span
            data-live-badge
            className="flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider text-emerald-400"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> Live
          </span>
        )}
        <ModelPicker slug={agent.slug} agentKey={agent.agent_key} />
        {agent.tools && agent.tools.length > 0 && (
          <span className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1">
            <Wrench className="h-3 w-3" /> {agent.tools.length} tool
            {agent.tools.length !== 1 ? "s" : ""}
          </span>
        )}
        <ServerPinPicker slug={agent.slug} serverName={agent.server_name} />
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {/* Start/pause/stop, lifted out of the workbench so the loop can be
            operated from the run you are reading rather than only from the
            page that happens to own the strategy. */}
        {strategy && (
          <AgentControls
            slug={agent.slug}
            sslug={strategy.slug}
            status={strategy.status}
            defaultContext={
              strategy.default_trading_context ||
              (strategy.config.trading_context as string) ||
              ""
            }
            agentConfig={strategy.config}
          />
        )}
        {/* Labelled "Open chat", not "Chat": it lands in the workspace at `/`,
            which is a different surface, and it continues the live conversation
            with this agent when there is one rather than always starting
            another. */}
        <button
          onClick={onAskAgent}
          className="flex items-center gap-1.5 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-semibold text-[var(--color-text-muted)] transition-all hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
          title={`Open your chat with ${agent.name} in the workspace — continues the live conversation if there is one`}
        >
          <MessageSquareText className="h-3.5 w-3.5" />
          <span className="hidden sm:inline">Open chat</span>
        </button>
        {/* Condor is the default agent: deleting its AGENT.md would leave every
            unbound session without instructions or a model, so the store
            refuses it. The button is not offered rather than offered and
            refused. */}
        {agent.slug !== CHAT_SLUG && (
          <button
            onClick={onDelete}
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
  );
}
