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
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useWorkspacePane } from "@/hooks/useWorkspacePane";
import { CHAT_SLUG, type AgentSummary, type ConversationMeta } from "@/lib/api";

/** Whether the rail is a column or a strip, remembered across sessions. */
const OPEN_KEY = "condor.chat.rail.open";

function readOpen(): boolean {
  return localStorage.getItem(OPEN_KEY) !== "false";
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
    localStorage.setItem(OPEN_KEY, String(next));
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
      <div className="flex items-center gap-1 border-b border-[var(--color-border)] pr-1">
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
 * live agent is a direct link, several open a short list. This replaced the
 * fleet grid: the grid's only unique job was showing which agents are running,
 * and a line at the top of the rail does that without a second page.
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
  const rowClass =
    "flex min-w-0 flex-1 items-center gap-2 py-2 pl-3 text-left text-[11px] text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]";

  return (
    <>
      {agents.length === 0 ? (
        <span className={rowClass}>
          {icon}
          <span className="min-w-0 flex-1 truncate">
            Nothing looping{tasks}
          </span>
        </span>
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
        // A span, not a button: this sits inside the row's own button. Below
        // `md` the rail is a touch overlay where hover does not exist, so the
        // `+` stays visible there and only hides behind hover on the desktop
        // rail. The same shape the panel's session tabs use to close.
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
          className="shrink-0 rounded p-0.5 text-[var(--color-text-muted)] transition-opacity hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] focus-visible:opacity-100 group-focus-within:opacity-100 md:opacity-0 md:group-hover:opacity-100"
        >
          <Plus className="h-3 w-3" />
        </span>
      )}
    </button>
  );
}
