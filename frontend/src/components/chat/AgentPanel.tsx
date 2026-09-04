import { ExternalLink } from "lucide-react";
import { useState } from "react";
import { Link } from "react-router-dom";

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
 * The model and server pickers are in the bar at the top of this panel, one
 * line above the sections they apply to, rather than beside the button that
 * opens it — the button is a verb and a door, and the panel is on screen for
 * exactly as long as anyone is thinking about what this agent is made of.
 *
 * It takes half the pane row and nothing more: no full screen, because this is
 * a panel you work in while glancing back at the conversation, and a
 * full-screen version of it is a second layout to maintain for a gesture whose
 * only outcome is losing the chat. Half rather than a report's two thirds for
 * the same reason — a report is read and the chat behind it merely stays alive,
 * while here both columns are being used in the same minute, and two thirds put
 * the transcript on its 360px floor. The rail folds to a strip as it does for a
 * report; the dock stays, and the door back out is the pressed Tune button in
 * the bar above.
 *
 * A routine row hands the pane to the routine library rather than growing a
 * second one, and a strategy row does the same with its workbench — the very
 * component the strategy page renders, so the pane is not a preview of the
 * page. That last one used to be the exception: a strategy navigated, "to the
 * page that owns starting it with real money". But the guard on starting a loop
 * is its own dialog, not the width of the window, and the exception cost you
 * the conversation every time the agent named a strategy you wanted to look at.
 *
 * **Two reversals, reversed back (FEAT-118), and both arguments are worth
 * keeping in one place.** This panel long refused a full-screen control ("a
 * second layout to maintain for a gesture whose only outcome is losing the
 * chat") and refused a door out to `/agents/:slug` ("a link out is the easy
 * answer that stops this panel from ever having to be good enough"). FEAT-117
 * overturned both on one premise: that the panel *was* the page, so Maximize
 * was a width and not an escape. The premise is what changed back — the panel
 * is the seven Being sections again and the page is what the agent *did* — so
 * the first argument returns whole (`fullscreen={false}`) and the second
 * returns qualified. A door is fine when the thing behind it is a different
 * screen with a different job, which is what the page becomes; so it is
 * labelled with its destination (**Workspace ↗**) rather than drawn as a
 * maximize glyph, and it is a link rather than a key binding, because `f`
 * quietly navigating out of the chat is precisely the gesture the paragraph
 * above argued against.
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
  tab,
  onTabChange,
  onSelectBrain,
  onSelectServer,
  onOpenRoutine,
  onOpenStrategy,
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
  /**
   * Which section is open, off the home's `?tab=` (FEAT-118).
   *
   * A prop and not `useState` any more — the one deliberate improvement on the
   * panel this restores. Held in state, the section could not be sent to
   * anyone, Back did not step through it and a reload landed on Brain whatever
   * you had been reading; one parameter buys all three.
   */
  tab?: KnowledgeTabId;
  onTabChange: (tab: KnowledgeTabId) => void;
  onSelectBrain: (selection: BrainSelection) => void;
  onSelectServer: (serverName: string) => void;
  /** Hand the pane to the routine library, focused on this routine. */
  onOpenRoutine: (routineName: string) => void;
  /** Hand the pane to this strategy's workbench, the same one its page hosts. */
  onOpenStrategy: (strategySlug: string) => void;
  /**
   * Put a request from a brain row to this agent (FEAT-092). Here that is a
   * message into a fresh conversation, which is the better move the chat has
   * and the detail page does not: the workspace stays where it is.
   */
  onAskAgent: (text: string) => void;
  onClose: () => void;
}) {
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
          <div className="flex shrink-0 items-center gap-1">
            {/* The door, to the left of the wiring: the wiring is about this
                conversation and belongs nearest the close glyph it shares an
                edge with, while this is about the agent, like the title it
                sits beside. Labelled rather than a glyph — the destination is
                a different screen, not a wider one, and a reader is owed the
                name of the place a click takes them out to. */}
            <Link
              to={`/agents/${encodeURIComponent(slug)}`}
              title={`Open ${name}'s workspace — its runs, what it deployed and what it made`}
              className="flex shrink-0 items-center gap-1 rounded border border-[var(--color-border)] px-2 py-1 text-[11px] text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
            >
              <ExternalLink className="h-3.5 w-3.5" /> Workspace
            </Link>
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
          </div>
        }
        onClose={() => (dirty ? setConfirmClose(true) : onClose())}
        // No full screen. The panel is a place you change one thing and look
        // back at what the agent just said — the whole reason it opens beside
        // the conversation instead of over it — so the one gesture the button
        // offered was losing the chat it is meant to be read against. The door
        // beside it goes somewhere else on purpose, and says so.
        fullscreen={false}
        // An even split, not a report's two thirds: both sides of this seam are
        // in use at once — you change something here and read what the agent
        // says about it there — so neither gets to be the margin of the other.
        paneProfile="tune"
        bleed
      >
        <AgentKnowledge
          slug={slug}
          // A column, not a page: the strategy cards read the viewport's
          // breakpoints, and on a wide window three of them would land side by
          // side in a 400px pane. Stated rather than inferred from the rail,
          // because they were always two different facts (FEAT-117).
          dense
          tab={tab}
          onTabChange={onTabChange}
          onOpenRoutine={onOpenRoutine}
          onOpenStrategy={onOpenStrategy}
          onAskAgent={onAskAgent}
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
