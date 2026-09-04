import {
  ArrowUpRight,
  Bot,
  ChevronDown,
  MessageSquare,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Zap,
} from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { ConversationList } from "@/components/chat/ConversationList";
import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useWorkspacePane } from "@/hooks/useWorkspacePane";
import { CHAT_SLUG, type AgentSummary, type ConversationMeta } from "@/lib/api";
import { homePath } from "@/lib/homeView";
import { CHAT_RAIL_OPEN_KEY } from "@/lib/sessionState";

function readOpen(): boolean {
  return localStorage.getItem(CHAT_RAIL_OPEN_KEY) !== "false";
}

/**
 * Who you can talk to, and what you already said.
 *
 * Collapses to a strip of icons, which is how the workspace stays readable
 * when a report opens beside the conversation: four columns of chrome around
 * one transcript is more than any window has, and of the four this is the one
 * you are least likely to be reading while you talk. So it steps back on its
 * own when the pane takes the room and comes back the moment the pane closes —
 * a loan, not a preference, so a report never quietly rewrites how the
 * workspace opens tomorrow. Collapsing it by hand does set that preference.
 *
 * Below `md` it is neither: an overlay the header's button summons, which is
 * what `open`/`onClose` drive.
 *
 * `memo`, because the tab above re-renders 20 times a second while a reply
 * streams and this is the most expensive thing on the screen — every agent in
 * the registry and a hundred conversations. Every prop it takes is a scalar or
 * a stable identity for that reason.
 */
export const ChatRail = memo(function ChatRail({
  agents,
  runningTasks,
  activeSlug,
  hasSession,
  liveIds,
  activeId,
  open,
  onClose,
  onTalk,
  onNew,
  onOpenConversation,
}: {
  agents: AgentSummary[];
  /** Delegations running anywhere, for the live strip's count. */
  runningTasks: number;
  /** Bound agent of the conversation on screen, `""` for Condor. */
  activeSlug: string;
  /** Whether a conversation is on screen at all, so no row lights up before. */
  hasSession: boolean;
  liveIds: Set<string>;
  /** Conversation on screen — `null` before a session exists. */
  activeId: string | null;
  /** Shown below `md`, where the rail is an overlay rather than a column. */
  open: boolean;
  onClose: () => void;
  /** Talk to a slug — `""` is Condor; `fresh` starts a second conversation. */
  onTalk: (slug: string, agent: AgentSummary | null, fresh?: boolean) => void;
  /** A new conversation with whoever is selected. */
  onNew: () => void;
  onOpenConversation: (meta: ConversationMeta) => void;
}) {
  const [expanded, setExpanded] = useState(readOpen);

  // A pane that opened borrows the column; the value it borrowed is what it
  // gives back, so a rail the reader had already collapsed stays collapsed.
  const paneOpen = useWorkspacePane()?.open ?? false;
  const lent = useRef<boolean | null>(null);
  useEffect(() => {
    if (paneOpen) {
      if (lent.current === null) {
        lent.current = expanded;
        setExpanded(false);
      }
    } else if (lent.current !== null) {
      setExpanded(lent.current);
      lent.current = null;
    }
    // `expanded` is read, not watched: re-running this on the reader's own
    // expand would take the column straight back off them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paneOpen]);

  // Only the reader's own toggle is written down. What the layout does on its
  // own is about this moment, and a passing squeeze must not become the answer
  // for every session after it.
  const toggle = (next: boolean) => {
    setExpanded(next);
    lent.current = null;
    localStorage.setItem(CHAT_RAIL_OPEN_KEY, String(next));
  };

  // Condor is in the registry like every other Agent (FEAT-033), but it is not
  // a specialist you bind: you get it by binding nothing, which is what the
  // empty slug means everywhere else here. So it is lifted out of the list and
  // rendered once, at the top, reading its name and description off its own
  // AGENT.md rather than repeating them here.
  const condor = agents.find((a) => a.slug === CHAT_SLUG);
  const specialists = agents.filter((a) => a.slug !== CHAT_SLUG);
  const liveAgents = specialists.filter(
    (a) => deriveAgentStatus(a) === "running",
  );

  // Collapsed is a desktop state only: below `md` the rail is already an
  // overlay you dismiss, and a strip of icons there would be a third mode
  // nobody asked for.
  if (!expanded) {
    return (
      <aside className="hidden w-10 shrink-0 flex-col items-center gap-3 border-r border-[var(--color-border)] bg-[var(--color-bg)] py-2 md:flex">
        <button
          onClick={() => toggle(true)}
          className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          title="Show agents and conversations"
        >
          <PanelLeftOpen className="h-4 w-4" />
        </button>
        <button
          onClick={onNew}
          className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          title="New chat"
        >
          <Plus className="h-4 w-4" />
        </button>
        {liveAgents.length > 0 && (
          <button
            onClick={() => toggle(true)}
            className="flex flex-col items-center gap-0.5 rounded p-1 text-emerald-400 transition-colors hover:bg-[var(--color-surface-hover)]"
            title={`${liveAgents.length} agent${
              liveAgents.length !== 1 ? "s" : ""
            } looping`}
          >
            <Zap className="h-4 w-4" />
            <span className="text-[9px] font-bold">{liveAgents.length}</span>
          </button>
        )}
      </aside>
    );
  }

  return (
    <aside
      className={`${
        open ? "flex" : "hidden"
      } absolute inset-y-0 left-0 z-30 w-[260px] shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg)] md:relative md:flex`}
    >
      {/* What is looping right now, the way into it, and the way out of the
          column — the mirror of the dock's own collapse. */}
      <div className={`${WORKSPACE_BAR} gap-1 pr-1`}>
        <LiveStrip agents={liveAgents} runningTasks={runningTasks} />
        <button
          onClick={() => toggle(false)}
          className="hidden rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] md:block"
          title="Collapse"
        >
          <PanelLeftClose className="h-3.5 w-3.5" />
        </button>
      </div>

      {/* Who you can talk to */}
      <div className="border-b border-[var(--color-border)] py-1">
        <div className="px-3 pb-1 pt-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Agents
        </div>
        <RailRow
          label={condor?.name || "Condor"}
          icon={<MessageSquare className="h-3 w-3 shrink-0" />}
          active={hasSession && !activeSlug}
          title={condor?.description || "General trading assistant"}
          onClick={() => onTalk("", null)}
          newTitle={`New chat with ${condor?.name || "Condor"}`}
          onNew={() => onTalk("", null, true)}
        />
        {specialists.map((agent) => (
          <RailRow
            key={agent.slug}
            label={agent.name}
            icon={
              <Bot className="h-3 w-3 shrink-0 text-[var(--color-accent)]" />
            }
            live={deriveAgentStatus(agent) === "running"}
            active={activeSlug === agent.slug}
            title={agent.description || agent.name}
            onClick={() => onTalk(agent.slug, agent)}
            newTitle={`New chat with ${agent.name}`}
            onNew={() => onTalk(agent.slug, agent, true)}
          />
        ))}
      </div>

      {/* What you already said — the rail's own list, in flow */}
      <ConversationList
        liveIds={liveIds}
        activeId={activeId}
        // "New chat" means a fresh one with whoever is selected — Condor only
        // when Condor is who you are pointing at.
        onNew={onNew}
        onOpen={(meta) => {
          onClose();
          onOpenConversation(meta);
        }}
      />
    </aside>
  );
});

// ── Live strip ──

/**
 * What is looping, and the door into it.
 *
 * Strategies live on an agent's own page, so that is where this points — one
 * live agent is a direct link, several open a short list.
 *
 * This line used to be the whole answer to "what is running": it replaced a
 * fleet grid whose only unique job it already did. Since FEAT-104 step 3 that
 * is no longer its job — the home *is* the overview, and it carries the money,
 * the last decision and the next tick this line never could. What is left is a
 * glance for somebody mid-conversation who does not want to leave it, and a way
 * back to the page that says the rest: the fleet is the last row of the list,
 * and the whole strip when nothing is looping at all.
 */
function LiveStrip({
  agents,
  runningTasks,
}: {
  agents: AgentSummary[];
  runningTasks: number;
}) {
  const [open, setOpen] = useState(false);
  // State, not a ref: the portalled panel only gets coordinates once a render
  // has handed it the resolved trigger element.
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const tasks =
    runningTasks > 0
      ? ` · ${runningTasks} task${runningTasks !== 1 ? "s" : ""}`
      : "";

  const icon = (
    <Zap
      className={`h-3 w-3 shrink-0 ${agents.length > 0 ? "text-emerald-400" : ""}`}
    />
  );
  // No vertical padding of its own: the bar around it owns the height now, so
  // what is looping cannot quietly make the rail's bar taller than the dock's.
  const rowClass =
    "flex min-w-0 flex-1 items-center gap-2 self-stretch pl-3 text-left text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]";

  return (
    <>
      {agents.length === 0 ? (
        <Link
          to={homePath("fleet")}
          className={rowClass}
          title="See every agent and what it last did"
        >
          {icon}
          <span className="min-w-0 flex-1 truncate">
            Nothing looping{tasks}
          </span>
          <ArrowUpRight className="h-3 w-3 shrink-0" />
        </Link>
      ) : agents.length === 1 ? (
        <Link
          to={`/agents/${agents[0].slug}`}
          className={rowClass}
          title={`Open ${agents[0].name}'s strategies`}
        >
          {icon}
          <span className="min-w-0 flex-1 truncate">
            {agents[0].name} live{tasks}
          </span>
          <ArrowUpRight className="h-3 w-3 shrink-0" />
        </Link>
      ) : (
        <button
          ref={setAnchor}
          onClick={() => setOpen((v) => !v)}
          className={rowClass}
          aria-expanded={open}
          aria-haspopup="listbox"
          title="Open a running agent's strategies"
        >
          {icon}
          <span className="min-w-0 flex-1 truncate">
            {agents.length} live{tasks}
          </span>
          <ChevronDown className="h-3 w-3 shrink-0" />
        </button>
      )}

      {/* Portalled, not `absolute`: the rail sits in the chat workspace, whose
          `main` is `overflow-hidden`, so an absolute panel was clipped at the
          strip's own border with no scroll to recover it. The old `fixed
          inset-0` backdrop also swallowed the click that followed. */}
      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={() => setOpen(false)}
        align="left"
        maxHeight={288}
        role="listbox"
        className="flex w-[236px] flex-col py-0.5"
      >
        {agents.map((agent) => (
          <Link
            key={agent.slug}
            to={`/agents/${agent.slug}`}
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
          >
            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400" />
            <span className="min-w-0 flex-1 truncate">{agent.name}</span>
            <ArrowUpRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
          </Link>
        ))}
        {/* The list is the running agents; this is everything else about them,
            including the ones that are not running. */}
        <Link
          to={homePath("fleet")}
          onClick={() => setOpen(false)}
          className="mt-0.5 flex items-center gap-2 border-t border-[var(--color-border)] px-2.5 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          <span className="min-w-0 flex-1 truncate">The whole fleet</span>
          <ArrowUpRight className="h-3 w-3 shrink-0" />
        </Link>
      </AnchoredMenu>
    </>
  );
}

// ── Rail row ──

function RailRow({
  label,
  icon,
  live,
  active,
  title,
  onClick,
  onNew,
  newTitle,
}: {
  label: string;
  icon: React.ReactNode;
  live?: boolean;
  active?: boolean;
  title?: string;
  onClick: () => void;
  /** Start a *second* conversation with this row, rather than focusing one. */
  onNew?: () => void;
  newTitle?: string;
}) {
  return (
    <button
      onClick={onClick}
      title={title}
      className={`group flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors ${
        active
          ? "bg-[var(--color-primary)]/10 text-[var(--color-primary)]"
          : "text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
      }`}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{label}</span>
      {live && (
        <span
          className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400"
          title="Loop running"
        />
      )}
      {onNew && (
        // A span, not a button: this sits inside the row's own button.
        //
        // The `+` used to appear only on hover, which made the second
        // conversation a thing you had to already know about. Now it is always
        // on the row and the *hover* is what explains it: the icon grows a
        // "New" label, so the affordance is discovered by pointing rather than
        // by guessing. Hovering the pill itself is a third step in the same
        // gesture — it takes the accent colour, so you can see the click will
        // land here and not on the row underneath.
        <span
          role="button"
          tabIndex={0}
          aria-label={newTitle}
          title={newTitle}
          onClick={(e) => {
            e.stopPropagation();
            onNew();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" || e.key === " ") {
              e.preventDefault();
              e.stopPropagation();
              onNew();
            }
          }}
          className="flex shrink-0 items-center gap-0.5 rounded-full px-1 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)] opacity-60 transition-all duration-200 group-hover:bg-[var(--color-surface-hover)] group-hover:opacity-100 group-focus-within:bg-[var(--color-surface-hover)] group-focus-within:opacity-100 hover:bg-[var(--color-primary)]/15! hover:text-[var(--color-primary)]! focus-visible:opacity-100"
        >
          <Plus className="h-3 w-3 shrink-0" />
          {/* Width, not display: a label that animates from nothing keeps the
              row's own text from jumping when the pill grows. */}
          <span className="max-w-0 overflow-hidden whitespace-nowrap opacity-0 transition-all duration-200 group-hover:max-w-[2.5rem] group-hover:pr-0.5 group-hover:opacity-100 group-focus-within:max-w-[2.5rem] group-focus-within:pr-0.5 group-focus-within:opacity-100">
            New
          </span>
        </span>
      )}
    </button>
  );
}
