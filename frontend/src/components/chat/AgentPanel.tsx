import { useState } from "react";

import { AgentKnowledge } from "@/components/agent/AgentKnowledge";
import type { KnowledgeTabId } from "@/components/agent/knowledgeTabs";
import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { AgentWiring } from "@/components/chat/AgentWiring";
import type { BrainSelection } from "@/components/chat/BrainPicker";
import { WorkspaceSheet } from "@/components/chat/WorkspaceSheet";
import type { ChatSlot } from "@/hooks/useChatSocket";
import type {
  AgentBindingOption,
  ChatAgentOption,
  CustomProvider,
} from "@/lib/api";

/**
 * What the agent knows, and what it runs on, beside the conversation you are
 * having with it.
 *
 * Every section of {@link AgentKnowledge} — AGENT.md, playbooks, memories,
 * tools, strategies, routines and activity — reached through the rail down its
 * right edge, against the dock, so the sections are on the same side of the
 * window as everything else that opens something here. The same component the
 * agent's own page is built from, so anything editable there is editable here
 * (FEAT-081); reading what an agent knows no longer costs you the chat.
 *
 * The model and server pickers are in the bar at the top of this panel. They
 * were in the dock card that opens it, which forced the dock to stay put
 * whenever the panel was up — the one panel in the workspace that could not
 * fold its column away, because folding it took its own controls off screen.
 * Held here instead, the panel steers itself, and opening it borrows the dock
 * exactly the way opening a routine's report does.
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
  slot: ChatSlot | null;
  /** The model the next conversation starts on, before there is one. */
  pendingAgentKey: string;
  /** The page's ambient server selection, before there is a session. */
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

  return (
    <>
      <WorkspaceSheet
        title={name}
        // The wiring rides in the bar rather than in the body: it is about the
        // conversation, not about the agent, and it has to stay reachable
        // whichever of the panel's sections is scrolled to.
        actions={
          <AgentWiring
            slot={slot}
            pendingAgentKey={pendingAgentKey}
            ambientServer={ambientServer}
            agents={agents}
            customProviders={customProviders}
            agentBindings={agentBindings}
            isStreaming={isStreaming}
            onSelectBrain={onSelectBrain}
            onSelectServer={onSelectServer}
          />
        }
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
