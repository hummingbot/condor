import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { AgentWorkspaceBody } from "@/components/agent/workspace/AgentWorkspaceBody";
import { DOING_VIEWS } from "@/components/agent/workspace/views";
import {
  useWorkspaceUrl,
  workspaceSearch,
} from "@/components/agent/workspace/workspaceUrl";
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
 * The whole agent, beside the conversation you are having with it.
 *
 * This panel used to be half an agent: {@link AgentKnowledge}'s seven **Being**
 * sections — AGENT.md, playbooks, memories, tools, strategies, routines,
 * activity — and nothing of what the agent was actually *doing*. Now, Runs,
 * Playbook, Money and Fleet existed only at `/agents/:slug`, so the question a
 * conversation most often provokes ("it says it deployed six controllers — did
 * it?") was the one question this pane could not answer. It is
 * {@link AgentWorkspaceBody} now: the same loop bar, the same spine, the same
 * bodies the page renders, from the same four parameters (FEAT-117).
 *
 * The model and server pickers stay in the bar at the top, one line above the
 * sections they apply to, rather than beside the button that opens it — the
 * button is a verb and a door, and the panel is on screen for exactly as long
 * as anyone is thinking about what this agent is made of.
 *
 * **Two of this panel's decisions are deliberately reversed here, and the
 * argument for them is worth keeping.** It used to refuse a full-screen control
 * ("a second layout to maintain for a gesture whose only outcome is losing the
 * chat") and refuse a door out to the agent's page ("a link out is the easy
 * answer that stops this panel from ever having to be good enough"). Both were
 * right *while the panel was a subset of the page*. They stop being right once
 * the panel **is** the page: there is no second layout, because Maximize
 * navigates to `/agents/:slug` carrying the view, the strategy, the run and the
 * tick — the same component, from the same state, at a different width. The
 * door is not an escape hatch any more, and the panel did have to be good
 * enough, which is what the extraction made true rather than promised.
 *
 * A routine row still hands the pane to the routine library rather than growing
 * a second one. A strategy row no longer hands it to a sheet: the workbench is
 * one of this spine's own views, so a strategy is a scope change in place —
 * which is the same thing the page does, and one fewer surface than before.
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
  onAskAgent,
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
  /**
   * Put a request from a brain row to this agent (FEAT-092). Here that is a
   * message into a fresh conversation, which is the better move the chat has
   * and the detail page does not: the workspace stays where it is.
   */
  onAskAgent: (text: string) => void;
  onClose: () => void;
}) {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [dirty, setDirty] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);

  // The workspace's grammar, spent on the *home's* query string. Same four
  // keys, same cascades, same module the page binds — the pane is not a second
  // vocabulary, which is the whole reason a reader who learns one knows the
  // other (FEAT-117).
  const adapter = useWorkspaceUrl(searchParams, setSearchParams);

  return (
    <>
      <WorkspaceSheet
        title={name}
        // The wiring rides in the bar rather than in the body: it is about the
        // conversation, not about the agent, and it has to stay reachable
        // whichever of the workspace's views is scrolled to.
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
        // Maximize and `f` go to the page rather than growing the sheet,
        // because the page renders this exact component — so it is a change of
        // width, and the state travels with it: whichever view, scope, run and
        // tick the pane is on is what `/agents/:slug` opens on.
        onFullscreen={() => {
          const query = workspaceSearch(searchParams).toString();
          navigate(
            `/agents/${encodeURIComponent(slug)}${query ? `?${query}` : ""}`,
          );
        }}
        // Which half of the taxonomy is open decides how much room to ask for,
        // because the two halves are read differently and the profiles already
        // exist for exactly this distinction. A **Doing** view is a screen you
        // read — a run, a fleet, a month of money — and takes the report's
        // width; a **Being** section is a workbench you keep one hand on while
        // the agent answers beside it, and takes the even split this panel has
        // always opened at.
        paneProfile={
          (DOING_VIEWS as readonly string[]).includes(adapter.url.view)
            ? "read"
            : "tune"
        }
        bleed
      >
        <AgentWorkspaceBody
          slug={slug}
          adapter={adapter}
          // A column, not a page: the strategy cards read the viewport's
          // breakpoints, and on a wide window three of them would land side by
          // side in a 400px pane.
          dense
          // No header: the sheet's own bar already carries this agent's name
          // and the wiring, and a second identity strip under it is the same
          // fact twice in a 400px column.
          onAskAgent={onAskAgent}
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
