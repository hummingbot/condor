import { Bot, ChevronRight, Lock } from "lucide-react";
import { useState } from "react";

import { AgentKnowledge } from "@/components/agent/AgentKnowledge";
import type { KnowledgeTabId } from "@/components/agent/knowledgeTabs";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import { wiring } from "@/components/chat/sessionWiring";
import { useBrainLabel } from "@/hooks/useBrainLabel";
import { useChat } from "@/hooks/useChat";
import type { ChatSlot } from "@/hooks/useChatSocket";
import type { AgentBindingOption, ChatAgentOption } from "@/lib/api";

/**
 * The one control beside the session tabs: who is answering, on what, where.
 *
 * It replaces three — a model picker, a server chip and a link that *left* the
 * workspace for the agent's page — because they were three answers to one
 * question, and the third cost you the conversation to read the answer.
 * Clicking this opens {@link AgentPanel} in the pane instead, with the
 * transcript still live beside it.
 *
 * It survives the move of the pickers into the dock's card (`DockAgentCard`)
 * because the dock can be collapsed and this cannot: the reader who folded the
 * column away still needs to be told what is answering, and still needs a way
 * back to it.
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
 * What the agent knows, beside the conversation you are having with it.
 *
 * Every section of {@link AgentKnowledge} — AGENT.md, playbooks, memories,
 * tools, strategies, routines and activity — reached through the rail down its
 * right edge, against the dock, so the sections are on the same side of the
 * window as everything else that opens something here. The same component the
 * agent's own page is built from, so anything editable there is editable here
 * (FEAT-081); reading what an agent knows no longer costs you the chat.
 *
 * What the conversation *runs on* is not here: the model and server pickers
 * live in the dock's `DockAgentCard`, which stays on screen while this is open.
 * The panel is the agent; the card is the wiring.
 *
 * There is deliberately no door out to the agent's full page. Anything worth
 * doing to an agent should be worth doing here, next to the conversation that
 * made you want to do it — a link out is the easy answer that stops this panel
 * from ever having to be good enough.
 *
 * A routine row hands the pane to the routine library rather than growing a
 * second one, and a strategy still navigates to the page that owns starting it
 * with real money.
 */
export function AgentPanel({
  slug,
  name,
  onOpenRoutine,
  onClose,
}: {
  /** Whose panel this is: the session's agent, the rail's pick, else Condor. */
  slug: string;
  name: string;
  /** Hand the pane to the routine library, focused on this routine. */
  onOpenRoutine: (routineName: string) => void;
  onClose: () => void;
}) {
  const [tab, setTab] = useState<KnowledgeTabId>("brain");
  const [dirty, setDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);

  return (
    <>
      <WorkspaceSheet
        title={name}
        onClose={() => (dirty ? setConfirmClose(true) : onClose())}
        bleed
      >
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
