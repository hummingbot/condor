import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  ArrowUpRight,
  Bot,
  ChevronDown,
  ChevronRight,
  Globe,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Trash2,
  Zap,
} from "lucide-react";
import { memo, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { deriveAgentStatus } from "@/components/agent/agentStatus";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { ARCHIVED_GROUP, ConversationList } from "@/components/chat/ConversationList";
import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useWorkspacePane } from "@/hooks/useWorkspacePane";
import { EXECUTION_PATH } from "@/components/chat/accountPanels";
import {
  api,
  CHAT_SLUG,
  type AgentSummary,
  type ConversationMeta,
} from "@/lib/api";
import { agentsQuery } from "@/lib/queryClient";
import { CHAT_RAIL_OPEN_KEY } from "@/lib/sessionState";

/** The reader's own choice, if they ever made one: `null` when never set. */
function readStoredOpen(): boolean | null {
  try {
    const v = localStorage.getItem(CHAT_RAIL_OPEN_KEY);
    return v === null ? null : v === "true";
  } catch {
    return null;
  }
}

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
  hasSession,
  busyIds,
  waitingIds,
  liveDrafts,
  activeId,
  hubActive = false,
  open,
  onClose,
  onTalk,
  onNew,
  onOpenConversation,
  onDeletedConversation,
}: {
  agents: AgentSummary[];
  /** Delegations running anywhere, for the live strip's count. */
  runningTasks: number;
  /** Bound agent of the conversation on screen, `""` for Condor. */
  activeSlug?: string;
  /** Whether a conversation is on screen at all, so no row lights up before. */
  hasSession: boolean;
  /** No longer drawn: with one chat at a time, live is just the open one. */
  liveIds?: Set<string>;
  /** Conversations answering right now (or starting). */
  busyIds?: Set<string>;
  /** Conversations holding a tool call that waits on the user. */
  waitingIds?: Set<string>;
  /** Chats on screen the server does not list yet (first answer running). */
  liveDrafts?: ConversationMeta[];
  /** Conversation on screen — `null` before a session exists. */
  activeId: string | null;
  /** The Hub is in the workspace's column, so its row is the one lit. */
  hubActive?: boolean;
  /** Shown below `md`, where the rail is an overlay rather than a column. */
  open: boolean;
  onClose: () => void;
  /** Talk to a slug — `""` is Condor; `fresh` starts a second conversation. */
  onTalk: (slug: string, agent: AgentSummary | null, fresh?: boolean) => void;
  /** A new conversation with whoever is selected. */
  onNew: () => void;
  onOpenConversation: (meta: ConversationMeta) => void;
  /** A chat was deleted from the rail; whatever still holds it on screen lets go. */
  onDeletedConversation?: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(readOpen);

  // The agent a row's menu asked to delete, held here rather than in the row:
  // the row unmounts the moment the deletion lands, and a dialog owned by it
  // would go with it mid-confirm.
  const [pendingDelete, setPendingDelete] = useState<AgentSummary | null>(null);
  const queryClient = useQueryClient();
  const deleteAgent = useMutation({
    mutationFn: (slug: string) => api.deleteAgent(slug),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: agentsQuery().queryKey });
      // Its chats were deleted with it (server side), so the rail re-reads.
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
      setPendingDelete(null);
    },
  });

  // A pane that opened borrows the column; the value it borrowed is what it
  // gives back, so a rail the reader had already collapsed stays collapsed.
  const paneOpen = useWorkspacePane()?.open ?? false;
  const lent = useRef<boolean | null>(null);
  useEffect(() => {
    if (paneOpen) {
      // A rail the reader opened by hand is theirs: it is not lent away, so a
      // reload with a pane already open leaves it open, as they were using it.
      // Only a rail nobody has chosen for steps back on its own.
      if (lent.current === null && readStoredOpen() !== true) {
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
        {/* The nav's Hub tab is gone, so the collapsed strip carries the
            glyph again: the rail collapses on its own when a pane opens, and
            that must not cost the only way in. */}
        <Link
          to="/hub"
          title="Botcamp Hub — install published agents, or publish your own"
          aria-current={hubActive ? "page" : undefined}
          className={`rounded p-1 transition-colors hover:bg-[var(--color-surface-hover)] ${
            hubActive
              ? "text-[var(--color-primary)]"
              : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          }`}
        >
          <Globe className="h-4 w-4" />
        </Link>
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
      } absolute inset-y-0 left-0 z-30 w-[288px] shrink-0 flex-col border-r border-[var(--color-border)] bg-[var(--color-bg)] md:relative md:flex`}
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

      {/* One action above the list: a new chat with whoever is selected
          (Condor before anything is). A specific agent's is the `+` on its
          row. */}
      <div className="px-2 pb-1 pt-2">
        <button
          onClick={onNew}
          // Always raised — the same card the open chat is — so the one
          // action in the column reads as a button, not as a row of the list.
          className="flex w-full items-center gap-2.5 rounded-lg bg-[var(--color-surface)] px-2 py-2 text-left text-sm font-medium text-[var(--color-text)] shadow-sm ring-1 ring-[var(--color-border)] transition-shadow hover:shadow-md"
        >
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary-fill)] text-[var(--on-primary)]">
            <Plus className="h-3.5 w-3.5" />
          </span>
          New chat
        </button>
      </div>

      {/* Agents, each with its chats inside — one row per agent, so no agent
          is ever listed twice. */}
      <ConversationList
        agents={condor ? [condor, ...specialists] : specialists}
        busyIds={busyIds}
        waitingIds={waitingIds}
        liveDrafts={liveDrafts}
        onDeleted={onDeletedConversation}
        activeId={activeId}
        onOpen={(meta) => {
          onClose();
          onOpenConversation(meta);
        }}
        renderAgent={({
          agent,
          slug,
          name,
          count,
          expanded,
          holdsActive,
          attention,
          onToggle,
        }) => {
          const isCondor = slug === CHAT_SLUG;
          const talkSlug = isCondor ? "" : slug;
          const live = !!agent && !isCondor && deriveAgentStatus(agent) === "running";
          return (
            <RailRow
              key={slug}
              label={name}
              icon={
                slug === ARCHIVED_GROUP ? (
                  <Archive className="h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />

                ) : isCondor ? (
                  // The house assistant keeps the brand colour; every
                  // specialist shares one quiet line icon.
                  <MessageSquare className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />
                ) : (
                  <Bot className="h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />
                )
              }
              expanded={count > 0 ? expanded : undefined}
              count={count}
              attention={attention}
              live={live}
              isNew={agent?.is_new}
              // Lit when its chat is on screen but folded away, so the open
              // chat is always findable in the list.
              active={!hubActive && hasSession && holdsActive && !expanded}
              title={
                slug === ARCHIVED_GROUP
                  ? count > 0
                    ? "Chats you archived. Each shows which agent it was with."
                    : "Nothing archived yet. Point at a chat and use the box icon to archive it."
                  : agent?.description || name
              }
              // An agent with chats opens and closes them; one without starts
              // the first.
              onClick={() =>
                count > 0
                  ? onToggle()
                  : agent
                    ? onTalk(talkSlug, isCondor ? null : agent)
                    : undefined // an empty archive: nothing to open
              }
              newTitle={`New chat with ${name}`}
              onNew={agent ? () => onTalk(talkSlug, isCondor ? null : agent, true) : undefined}
              slug={!isCondor && agent ? slug : undefined}
              running={live}
              onDelete={!isCondor && agent ? () => setPendingDelete(agent) : undefined}
            />
          );
        }}
      />

      {/* The one door into Botcamp Hub: installing and publishing agents.
          It sat below the agents and had a nav tab beside Agents too; the tab
          is gone and the card is the foot of the rail, under the
          conversations and pinned there however short the list is: a place you
          go, not something you read past. Still outside the agent list, as a
          card and not a row — when the two shared a shape the Hub read as a
          seventh agent whose chat was broken. */}
      <div className="mt-auto border-t border-[var(--color-border)] p-2">
        <Link
          to="/hub"
          title="Botcamp Hub — install published agents, or publish your own"
          aria-current={hubActive ? "page" : undefined}
          onClick={onClose}
          className={`group flex w-full items-center gap-2.5 rounded-md border px-2.5 py-2 text-left transition-colors ${
            hubActive
              ? "border-[var(--color-primary)]/40 bg-[var(--color-primary-fill)]/10"
              : "border-[var(--color-border)] hover:border-[var(--color-primary)]/40 hover:bg-[var(--color-surface-hover)]"
          }`}
        >
          <span
            className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md transition-colors ${
              hubActive
                ? "bg-[var(--color-primary-fill)]/20 text-[var(--color-primary)]"
                : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)] group-hover:text-[var(--color-primary)]"
            }`}
          >
            <Globe className="h-3.5 w-3.5" />
          </span>
          <span className="min-w-0 flex-1">
            <span
              className={`block truncate text-xs font-medium ${
                hubActive
                  ? "text-[var(--color-primary)]"
                  : "text-[var(--color-text)]"
              }`}
            >
              Agent Hub
            </span>
            <span className="block truncate text-[10px] text-[var(--color-text-muted)]">
              Browse and publish agents
            </span>
          </span>
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)] transition-transform group-hover:translate-x-0.5" />
        </Link>
      </div>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="Delete Agent"
        isPending={deleteAgent.isPending}
        isError={deleteAgent.isError}
        errorText={
          (deleteAgent.error as Error | null)?.message ??
          "Failed to delete agent. It may have running loops."
        }
        onConfirm={() =>
          pendingDelete && deleteAgent.mutate(pendingDelete.slug)
        }
        onClose={() => {
          // A mutation shared across every row in the rail (BUG-9):
          // without this, the error from a row whose delete was refused
          // stayed on screen as the *next* row's dialog opened, blaming a
          // delete that was never attempted.
          deleteAgent.reset();
          setPendingDelete(null);
        }}
      >
        Delete{" "}
        <strong className="text-[var(--color-text)]">
          {pendingDelete?.name}
        </strong>
        , its loops and your chats with it? This cannot be undone.
      </ConfirmDialog>
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
 * fleet grid whose only unique job it already did. `/fleet` (FEAT-104) took
 * that job back, and carries the money, the last decision and the next tick
 * this line never could. That page is gone again (FEAT-114) and its rows are
 * the Execution panel one column to the right, so what is left here is a glance
 * for somebody mid-conversation who does not want to leave it — and a way into
 * the panel that says the rest, without leaving the conversation at all: the
 * fleet is the last row of the list, and the whole strip when nothing is
 * looping.
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
          to={EXECUTION_PATH}
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
          title={`Open ${agents[0].name}'s loops`}
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
          title="Open a running agent's loops"
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
          to={EXECUTION_PATH}
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
  expanded,
  count,
  attention,
  live,
  isNew,
  active,
  title,
  onClick,
  onNew,
  newTitle,
  slug,
  running,
  onDelete,
}: {
  label: string;
  icon: React.ReactNode;
  /** Has chats under it: open (`true`) or folded (`false`). Absent: none. */
  expanded?: boolean;
  /** How many chats it holds, shown folded. */
  count?: number;
  /** A folded chat waits on an approval, or is answering. */
  attention?: "waiting" | "busy";
  live?: boolean;
  /** Cloned from Botcamp and not opened once yet — see `ChatRail`'s `markSeen`. */
  isNew?: boolean;
  active?: boolean;
  title?: string;
  onClick: () => void;
  /** Start a *second* conversation with this row, rather than focusing one. */
  onNew?: () => void;
  newTitle?: string;
  /** Whose row this is. With `onDelete`, what gives the row its `⋯` menu. */
  slug?: string;
  /** Looping, so deleting is refused by the server and said so here. */
  running?: boolean;
  onDelete?: () => void;
}) {
  return (
    // The row and its hover actions are siblings, not one button with
    // buttons inside: the actions float over the row's end (count, chevron)
    // instead of squeezing into it, so pointing at a row never reflows it, and
    // they go when the pointer does — a click no longer leaves them stuck on
    // through focus.
    <div className="group/agent relative px-2">
      <button
        onClick={onClick}
        title={title}
        className={`flex w-full items-center gap-2.5 rounded-lg px-2 py-1.5 text-left text-sm transition-colors group-hover/agent:bg-[var(--color-surface-hover)] ${
          active ? "font-semibold text-[var(--color-text)]" : "font-medium text-[var(--color-text)]"
        }`}
      >
        {icon}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {/* States as labelled pills, not bare dots: "Live" reads itself. */}
        {live && (
          <span
            className="shrink-0 rounded-md border border-[var(--color-green)]/30 bg-[var(--color-green)]/10 px-1.5 py-px text-2xs font-medium text-[var(--color-green)]"
            title="Loop running"
          >
            Live
          </span>
        )}
        {isNew && (
          // Cloned from Botcamp and never opened; gone the instant the row is
          // clicked (`ChatRail`'s `markSeen`).
          <span
            className="shrink-0 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-1.5 py-px text-2xs font-medium text-[var(--color-text-muted)]"
            title="Cloned from Botcamp — not opened yet"
          >
            New
          </span>
        )}
        {attention === "waiting" ? (
          <AlertTriangle
            className="h-3.5 w-3.5 shrink-0 text-[var(--color-yellow)]"
            aria-label="A chat is waiting for your approval"
          >
            <title>A chat is waiting for your approval</title>
          </AlertTriangle>
        ) : attention === "busy" ? (
          <Loader2
            className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--color-primary)]"
            aria-label="Answering"
          />
        ) : null}
        {!!count && (
          <span
            title={`${count} chat${count === 1 ? "" : "s"}`}
            className="shrink-0 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-1.5 py-px text-2xs font-medium tabular-nums text-[var(--color-text-muted)]"
          >
            {count}
          </span>
        )}
        {expanded !== undefined && (
          <ChevronDown
            className={`h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)] transition-transform ${
              expanded ? "" : "-rotate-90"
            }`}
          />
        )}
      </button>

      {(onNew || (slug && onDelete)) && (
        <div
          className="pointer-events-none absolute inset-y-0 right-2 flex items-center gap-0.5 rounded-r-lg pl-8 pr-1.5 opacity-0 transition-opacity group-hover/agent:pointer-events-auto group-hover/agent:opacity-100 has-[:focus-visible]:pointer-events-auto has-[:focus-visible]:opacity-100"
          // Fades in from the row's own hover colour, so it covers the count
          // and chevron without a hard edge.
          style={{
            background:
              "linear-gradient(to right, transparent, var(--color-surface-hover) 40%)",
          }}
        >
          {onNew && (
            <button
              type="button"
              aria-label={newTitle}
              title={newTitle}
              onClick={onNew}
              className="flex rounded-md p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-primary-fill)]/15 hover:text-[var(--color-primary)]"
            >
              <Plus className="h-3.5 w-3.5" />
            </button>
          )}
          {slug && onDelete && (
            <RowMenu slug={slug} label={label} running={running} onDelete={onDelete} />
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Everything you can do to an agent that is not talking to it.
 *
 * A row click means "talk", and it always has — which left the agent's own
 * workspace with no door from the only list that names every agent, and
 * deleting one four clicks deep behind a chat you did not want. This is that
 * door, in the slot beside the `+` that already taught the row has actions on
 * it.
 *
 * `role="button"` on a span rather than a real `<button>`: the row itself is a
 * button and nesting one inside it is invalid HTML. Same reason the `+` beside
 * it is a span, and the same stopPropagation so the click does not also open a
 * chat underneath the menu.
 */
function RowMenu({
  slug,
  label,
  running,
  onDelete,
}: {
  slug: string;
  label: string;
  running?: boolean;
  onDelete: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  return (
    <>
      <span
        ref={setAnchor}
        role="button"
        tabIndex={0}
        aria-label={`More for ${label}`}
        aria-haspopup="menu"
        aria-expanded={open}
        title={`More for ${label}`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen((v) => !v);
        }}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") {
            e.preventDefault();
            e.stopPropagation();
            setOpen((v) => !v);
          }
        }}
        className={`flex shrink-0 items-center rounded-md p-1 text-[var(--color-text-muted)] transition-all duration-200 hover:bg-[var(--color-primary-fill)]/15! hover:text-[var(--color-primary)]! focus-visible:opacity-100 ${
          // Unlike the `+`, this is hidden until the row is pointed at: it is
          // the rare errand, and six always-on `⋯` would make the list busier
          // than the agents in it.
          // `hidden`, not transparent: an invisible `⋯` still took its width,
          // which pushed every count but Condor's (no menu) out of line.
          // Shown by the hover overlay it sits in (`RailRow`).
          "flex"
        }`}
      >
        <MoreHorizontal className="h-3.5 w-3.5 shrink-0" />
      </span>
      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={() => setOpen(false)}
        align="left"
        role="menu"
        className="flex w-[200px] flex-col py-0.5"
      >
        {/* The panel is portalled into `body`, so it is not nested in the row
            in the DOM — but it still is in React's tree, and that is what
            bubbling follows. Without this every click in the menu would also
            open a chat with the row behind it. */}
        <div
          className="flex w-full flex-col"
          onClick={(e) => e.stopPropagation()}
          onKeyDown={(e) => e.stopPropagation()}
        >
          <Link
            to={`/agents/${encodeURIComponent(slug)}`}
            role="menuitem"
            onClick={() => setOpen(false)}
            className="flex items-center gap-2 px-2.5 py-1.5 text-xs text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
          >
            <span className="min-w-0 flex-1 truncate">Open workspace</span>
            <ArrowUpRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
          </Link>
          <button
            role="menuitem"
            disabled={running}
            title={running ? "Stop its loops before deleting" : undefined}
            onClick={() => {
              setOpen(false);
              onDelete();
            }}
            className="mt-0.5 flex items-center gap-2 border-t border-[var(--color-border)] px-2.5 py-1.5 text-left text-xs text-[var(--color-red)] transition-colors hover:bg-red-500/10 disabled:cursor-not-allowed disabled:text-[var(--color-text-muted)] disabled:hover:bg-transparent"
          >
            <Trash2 className="h-3 w-3 shrink-0" />
            <span className="min-w-0 flex-1 truncate">
              {running ? "Delete — stop its loops first" : "Delete…"}
            </span>
          </button>
        </div>
      </AnchoredMenu>
    </>
  );
}
