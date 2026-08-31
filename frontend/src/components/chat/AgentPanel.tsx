import { Bot, ChevronRight, ExternalLink, Lock, Server } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

import { AgentKnowledge } from "@/components/agent/AgentKnowledge";
import type { KnowledgeTabId } from "@/components/agent/knowledgeTabs";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import {
  BrainMenuBody,
  type BrainSelection,
} from "@/components/chat/BrainPicker";
import { ServerMenuBody } from "@/components/chat/ServerMenu";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import { useBrainLabel } from "@/hooks/useBrainLabel";
import { useChat } from "@/hooks/useChat";
import type { ChatSlot } from "@/hooks/useChatSocket";
import type {
  AgentBindingOption,
  ChatAgentOption,
  CustomProvider,
} from "@/lib/api";

/** Why a control is dead: both switches respawn the session underneath. */
const BUSY_MODEL = "Finish this turn before switching model";
const BUSY_SERVER = "Finish this turn before switching server";

/** What a conversation is wired to, whether or not it has started yet. */
function wiring(slot: ChatSlot | null, pendingAgentKey: string, ambientServer: string) {
  return {
    agentKey: slot ? slot.info.agent_key : pendingAgentKey,
    serverName: slot ? slot.info.server_name || "" : ambientServer,
    pinned: !!slot?.info.server_pinned,
    pinnedBy: slot?.info.label || slot?.info.agent_slug || "",
  };
}

/**
 * The one control beside the session tabs: who is answering, on what, where.
 *
 * It replaces three — a model picker, a server chip and a link that *left* the
 * workspace for the agent's page — because they were three answers to one
 * question, and the third cost you the conversation to read the answer.
 * Clicking this opens {@link AgentPanel} in the pane instead, with the
 * transcript still live beside it.
 *
 * The label truncates right to left: the server goes below `lg`, the model
 * below `sm`, the name never. All three are always in the `title`.
 */
export function AgentPanelButton({
  name,
  slot,
  pendingAgentKey,
  ambientServer,
  agents,
  agentBindings,
  open,
  onToggle,
}: {
  /** Who is on the other end — the bound Agent, or Condor. */
  name: string;
  slot: ChatSlot | null;
  /** The model the next conversation starts on, before there is one. */
  pendingAgentKey: string;
  /** The page's ambient server selection, before there is a session. */
  ambientServer: string;
  agents: ChatAgentOption[];
  agentBindings: AgentBindingOption[];
  open: boolean;
  onToggle: () => void;
}) {
  const chat = useChat();
  const { agentKey, serverName, pinned } = wiring(
    slot,
    pendingAgentKey,
    ambientServer,
  );
  const { short } = useBrainLabel({
    agents,
    agentBindings,
    selectedAgentKey: agentKey,
    selectedAgentSlug: slot?.info.agent_slug || "",
  });

  const title = [name, short, serverName].filter(Boolean).join(" · ");

  return (
    <button
      onClick={onToggle}
      aria-pressed={open}
      title={`${title} — read and change what this agent is`}
      className={`flex min-w-0 items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors ${
        open
          ? "border-[var(--color-primary)]/50 bg-[var(--color-surface-hover)] text-[var(--color-text)]"
          : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text)] hover:border-[var(--color-primary)]/40"
      }`}
    >
      {/* The socket, not the session: green means the workspace can hear the
          server at all, which is the first thing to know when nothing answers. */}
      {chat.isConnected && (
        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500" />
      )}
      <Bot className="h-3 w-3 shrink-0 text-[var(--color-accent)]" />
      <span className="max-w-[14ch] truncate font-medium">{name}</span>
      {short && (
        <span className="hidden max-w-[14ch] truncate text-[var(--color-text-muted)] sm:inline">
          · {short}
        </span>
      )}
      {serverName && (
        <span className="hidden max-w-[14ch] items-center gap-1 truncate text-[var(--color-text-muted)] lg:inline-flex">
          <span aria-hidden>·</span>
          {pinned && <Lock className="h-2.5 w-2.5 shrink-0" />}
          {serverName}
        </span>
      )}
      <ChevronRight
        className={`h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${
          open ? "rotate-90" : ""
        }`}
      />
    </button>
  );
}

/**
 * The agent, whole, beside the conversation you are having with it.
 *
 * A session strip pinned at the top — the model this chat runs on, which for a
 * bound Agent *is* its model everywhere, and the server it trades against —
 * and under it every section of {@link AgentKnowledge}: AGENT.md, playbooks,
 * memories, tools, strategies, routines and activity. The same component the
 * agent's own page is built from, so anything editable there is editable here
 * (FEAT-081); reading what an agent knows no longer costs you the chat.
 *
 * A routine row hands the pane to the routine library rather than growing a
 * second one, and a strategy still navigates to the page that owns starting it
 * with real money.
 */
export function AgentPanel({
  slug,
  name,
  description,
  slot,
  pendingAgentKey,
  ambientServer,
  agents,
  customProviders,
  agentBindings,
  isStreaming,
  onSelectBrain,
  onSelectServer,
  onOpenRoutine,
  onClose,
}: {
  /** Whose panel this is: the session's agent, the rail's pick, else Condor. */
  slug: string;
  name: string;
  description?: string;
  slot: ChatSlot | null;
  pendingAgentKey: string;
  ambientServer: string;
  agents: ChatAgentOption[];
  customProviders: CustomProvider[];
  agentBindings: AgentBindingOption[];
  isStreaming: boolean;
  onSelectBrain: (selection: BrainSelection) => void;
  onSelectServer: (serverName: string) => void;
  /** Hand the pane to the routine library, focused on this routine. */
  onOpenRoutine: (routineName: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<KnowledgeTabId>("brain");
  const [dirty, setDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);

  // Streaming or still spawning: both switches respawn the session
  // underneath, which would drop the turn in flight.
  const busy = !!slot && (slot.pending || isStreaming);
  const { agentKey, serverName, pinned, pinnedBy } = wiring(
    slot,
    pendingAgentKey,
    ambientServer,
  );

  return (
    <>
      <WorkspaceSheet
        title={name}
        subtitle={description}
        actions={
          <Link
            to={`/agents/${encodeURIComponent(slug)}${tab === "brain" ? "" : `?tab=${tab}`}`}
            className="flex items-center gap-1 rounded px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title="Open this agent on its own page, on the section you are reading"
          >
            <ExternalLink className="h-3 w-3" /> Full page
          </Link>
        }
        onClose={() => (dirty ? setConfirmClose(true) : onClose())}
        bleed
      >
        {/* Never a tab: "what is this chat wired to" is the question the button
            was pressed to answer, so it is answered before anything is clicked. */}
        <div className="shrink-0 border-b border-[var(--color-border)]">
          <SessionRow
            label="Model"
            value={
              <BrainValue
                agents={agents}
                agentBindings={agentBindings}
                agentKey={agentKey}
                agentSlug={slot?.info.agent_slug || ""}
              />
            }
            hint={
              slot
                ? undefined
                : "The model your next conversation starts on"
            }
            disabled={busy}
            disabledReason={BUSY_MODEL}
            menu={(close) => (
              <BrainMenuBody
                agents={agents}
                customProviders={customProviders}
                agentBindings={agentBindings}
                selectedAgentKey={agentKey}
                selectedAgentSlug={slot?.info.agent_slug || ""}
                onSelect={onSelectBrain}
                onClose={close}
              />
            )}
          />
          {/* A pinned server is the Agent's decision, not the session's: there
              is nothing to pick, only somewhere to go and change it. */}
          {pinned ? (
            <SessionRow
              label="Server"
              value={
                <span className="flex items-center gap-1 text-emerald-400">
                  <Lock className="h-3 w-3 shrink-0" />
                  {serverName}
                </span>
              }
              hint={
                pinnedBy
                  ? `Pinned by ${pinnedBy} — change it on the agent's page`
                  : "Pinned by this agent's front matter"
              }
            />
          ) : (
            <SessionRow
              label="Server"
              value={
                <span className="flex items-center gap-1">
                  <Server className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
                  {serverName || "None selected"}
                </span>
              }
              hint={
                slot
                  ? undefined
                  : "The server your next conversation starts on"
              }
              disabled={busy}
              disabledReason={BUSY_SERVER}
              // Nothing to respawn before a session exists, and the top-right
              // selector already owns the ambient choice — so it is read here,
              // not set. No dead control, and no second door to one field.
              menu={
                slot
                  ? (close) => (
                      <ServerMenuBody
                        serverName={serverName}
                        onSelect={onSelectServer}
                        onClose={close}
                      />
                    )
                  : undefined
              }
            />
          )}
        </div>

        <AgentKnowledge
          slug={slug}
          layout="rail"
          tab={tab}
          onTabChange={setTab}
          onOpenRoutine={onOpenRoutine}
          onDirtyChange={setDirty}
        />
      </WorkspaceSheet>

      {/* A pane closes in one click, where a page has to be navigated away
          from — so the text an editor is holding gets a question first. */}
      <ConfirmDialog
        open={confirmClose}
        title="Discard changes?"
        confirmLabel="Discard"
        pendingLabel="Discarding..."
        onConfirm={() => {
          setConfirmClose(false);
          onClose();
        }}
        onClose={() => setConfirmClose(false)}
      >
        This panel has an editor with unsaved text. Closing it drops what you
        wrote.
      </ConfirmDialog>
    </>
  );
}

/** The model, resolved the way every other trigger resolves it. */
function BrainValue({
  agents,
  agentBindings,
  agentKey,
  agentSlug,
}: {
  agents: ChatAgentOption[];
  agentBindings: AgentBindingOption[];
  agentKey: string;
  agentSlug: string;
}) {
  const { model } = useBrainLabel({
    agents,
    agentBindings,
    selectedAgentKey: agentKey,
    selectedAgentSlug: agentSlug,
  });
  return <span className="truncate">{model}</span>;
}

/**
 * One line of the session strip: a field name, its value, and — when there is
 * something to pick — the list, hung off the row itself.
 *
 * A row with no `menu` is a statement rather than a dead button: a pinned
 * server and a chat that has not started yet both have an honest answer and
 * nothing here to change it with.
 */
function SessionRow({
  label,
  value,
  hint,
  disabled = false,
  disabledReason,
  menu,
}: {
  label: string;
  value: React.ReactNode;
  /** Why this row cannot be changed, or what it will apply to. */
  hint?: string;
  disabled?: boolean;
  disabledReason?: string;
  menu?: (close: () => void) => React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);

  const row = (
    <>
      <span className="w-14 shrink-0 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </span>
      <span className="min-w-0 flex-1 truncate text-left text-xs text-[var(--color-text)]">
        {value}
      </span>
    </>
  );

  if (!menu) {
    return (
      <div
        className="flex items-center gap-2 px-3 py-1.5"
        title={hint}
        data-session-row={label.toLowerCase()}
      >
        {row}
      </div>
    );
  }

  return (
    <>
      <button
        ref={setAnchor}
        onClick={() => setOpen((v) => !v)}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={disabled ? disabledReason : hint}
        data-session-row={label.toLowerCase()}
        className="flex w-full items-center gap-2 px-3 py-1.5 transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {row}
        <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
      </button>
      {/* Portalled, like every menu in this workspace: the pane and `main`
          above it both clip, and a list that opens into a scroll container
          with no overflow region is a list nobody can reach. */}
      <AnchoredMenu
        anchor={anchor}
        open={open}
        onClose={() => setOpen(false)}
        align="left"
        matchAnchorWidth="min"
        maxHeight={288}
        role="listbox"
        className="flex flex-col py-0.5"
      >
        {menu(() => setOpen(false))}
      </AnchoredMenu>
    </>
  );
}
