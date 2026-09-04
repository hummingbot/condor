import { PanelRightClose, Radio, Zap } from "lucide-react";
import { useState } from "react";

import {
  DockRoutines,
  RoutineLibrarySheet,
  type LibraryFocus,
} from "@/components/chat/DockRoutines";
import { DockResizeHandle } from "@/components/chat/DockResizeHandle";
import { DockSection } from "@/components/chat/DockSection";
import { DockTasks } from "@/components/chat/DockTasks";
import type { ContextPanels } from "@/components/chat/contextPanels";
import type { RoutineRunContext } from "@/components/routines/ReportBrowser";
import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
import type { Delegation } from "@/lib/api";
import { inScope, resolveRoutine, type RoutineScope } from "@/lib/routineUtils";
import { DOCK_WIDTH_KEY } from "@/lib/sessionState";

const DEFAULT_WIDTH = 300;
/** Narrower than this and the rows are ellipses; the column stops being readable. */
const MIN_WIDTH = 220;
/**
 * The floor a squeeze may take it to, as opposed to a drag — see the same pair
 * in `AccountDock`. The reader may not choose a width the rows cannot be read
 * at; the layout may borrow a little of it when the row is over-full, rather
 * than pushing the workspace rail off the window as it used to.
 */
const SQUEEZE_WIDTH = 200;
/** The transcript keeps at least this much, whatever the dock is dragged to. */
const MIN_CHAT_PX = 420;

function readWidth(): number {
  const stored = Number(localStorage.getItem(DOCK_WIDTH_KEY));
  return Number.isFinite(stored) && stored >= MIN_WIDTH
    ? stored
    : DEFAULT_WIDTH;
}

/**
 * What this conversation is doing.
 *
 * The workspace's third column: the rail is who you talk to, the middle is the
 * conversation, and this is the work it set in motion — the delegations it
 * started and the routines the agent on the other end owns. Both were only
 * reachable by leaving the conversation before.
 *
 * Below `xl` it stops being a column and overlays the transcript instead, the
 * mirror of what the rail does below `md`.
 *
 * Unlike the rail, it stays where it is when something opens in the workspace
 * pane. It used to fold itself away and hand the width over — which read well
 * for a report you only wanted to read, and badly for everything else: the runs
 * that are the doors into the library went off screen at the exact moment the
 * library came up, so opening a second routine meant first putting the column
 * back. The column is the reader's to close; nothing else touches it.
 *
 * The two sections are panes, not a stack: each scrolls inside itself, so an
 * expanded Tasks can never push Routines off the bottom. Both headers stay on
 * screen and one click away — which is the whole point of a dock you are meant
 * to watch while you type. The column keeps a scrollbar of its own only as the
 * escape hatch for a window too short to honour both panes' floors; clipping
 * a header there would be the very failure the panes exist to prevent.
 *
 * ## Which panes are open is not this component's to hold
 *
 * It lives in `useContextPanels`, because the tiles that open them sit on the
 * workspace rail with the agent's and the desk's — the same split
 * `useAccountPanels` makes for the same reason. This used to draw a second
 * 40 px strip of its own whenever it was collapsed, immediately beside that
 * rail: two strips of the same button, and a border between them that told the
 * reader nothing about why one word was on the left of it and another on the
 * right. Closed, this now renders no column and no strip — only the library
 * sheet, which is the pane's, not the column's.
 */
export function ContextDock({
  panels,
  delegations,
  conversationId,
  agentSlug,
  agentName,
  runContext,
  library,
  onLibraryChange,
}: {
  /** Which panes are open, and the lists behind them ({@link ContextPanels}). */
  panels: ContextPanels;
  /** The shared `["delegations"]` result — the dock adds no poll of its own. */
  delegations: Delegation[];
  conversationId: string;
  agentSlug: string;
  /** Who is answering, for the library bar's accessible name. */
  agentName?: string;
  /** The conversation a run launched from the library belongs to. */
  runContext?: RoutineRunContext;
  /**
   * Which routine the pane is showing — `null` when the library is not in it.
   *
   * Held by the workspace rather than here, because the pane is one column and
   * the library is no longer the only thing that wants it: the agent panel
   * (FEAT-081) does too. One owner and one union means opening either closes
   * the other by construction, instead of by a rule two components have to
   * remember. A row fills in what it points at; `{}` is the whole library,
   * which only an emptied scope leaves the pane on.
   */
  library: LibraryFocus | null;
  onLibraryChange: (focus: LibraryFocus | null) => void;
}) {
  const { shown, toggle, closeAll, instances, routines } = panels;
  const setLibrary = onLibraryChange;
  // Whose routines the picker lists — the agent on the other end of this
  // conversation, since those are the ones it can run.
  const [scope, setScope] = useState<RoutineScope>(agentSlug || "all");
  const [scopeAgent, setScopeAgent] = useState(agentSlug);
  const [width, setWidth] = useState(readWidth);

  /**
   * Whose routines, re-asked for whoever is now answering.
   *
   * Not remembered across conversations, deliberately. Opening a routine from
   * an agent means that agent's routines, so switching to another one — or to
   * the unbound Condor conversation, which is "all" — re-derives the scope
   * rather than leaving the last agent's filter over a list it does not own.
   * A pick *within* a conversation is the reader's own and stands: "All
   * routines" keeps showing all of them, and another agent's slug is a reader
   * saying they want to see that agent's, however long they stay.
   *
   * Adjusted during the render that brings the new slug rather than in an
   * effect, so the picker is never painted once under the old agent's filter.
   */
  if (scopeAgent !== agentSlug) {
    setScopeAgent(agentSlug);
    setScope(agentSlug || "all");
  }

  /**
   * The scope actually listed.
   *
   * A conversation bound to an agent that owns no routines of its own would
   * otherwise open on an empty list — a filter that hides everything is worse
   * than no filter, so it widens rather than showing nothing.
   */
  const effectiveScope =
    routines.length && !inScope(routines, scope).length ? "all" : scope;

  /**
   * Read a routine, from a dock row or from the library's own picker.
   *
   * A row may point at a routine the current scope excludes — a shared library
   * routine this agent ran, say — and a picker that does not contain what the
   * pane is showing is worse than a wider one, so the scope widens rather than
   * hiding what the reader just clicked.
   */
  const openFocus = (focus: LibraryFocus) => {
    const picked = resolveRoutine(routines, focus.source);
    if (
      picked &&
      !inScope(routines, scope).some((r) => r.name === picked.name)
    ) {
      setScope("all");
    }
    setLibrary(focus);
  };

  /** Narrowing the list moves the reader into it, rather than stranding them. */
  const changeScope = (next: RoutineScope) => {
    setScope(next);
    if (!library) return;
    const scoped = inScope(routines, next);
    const current = resolveRoutine(routines, library.source);
    if (!current || !scoped.some((r) => r.name === current.name)) {
      setLibrary(scoped[0] ? { source: scoped[0].name } : {});
    }
  };

  const librarySheet = library && (
    <RoutineLibrarySheet
      library={library}
      instances={instances}
      routines={routines}
      scope={effectiveScope}
      onScopeChange={changeScope}
      onSelectRoutine={(name) => openFocus({ source: name })}
      agentName={agentName}
      runContext={runContext}
      onClose={() => setLibrary(null)}
    />
  );

  /**
   * The sheet is a sibling of the column, not a child of it.
   *
   * It portals its body into the pane either way, but where it sits in *this*
   * tree decides whether it survives: nested in the `aside`, closing the column
   * unmounted it — which released the pane, which gave the column back, which
   * mounted it again. Held beside the `aside` at a fixed index — which is why
   * the column is a `&&` inside the fragment rather than an early return, since
   * an early return would move the sheet to index 0 and remount it for the same
   * reason — it keeps its identity, and the report inside it keeps its scroll,
   * through a close of the column beside it.
   */
  return (
    <>
      {shown.length > 0 && (
        <aside
          data-testid="context-dock"
          style={{
            width,
            minWidth: SQUEEZE_WIDTH,
            // The floor the transcript keeps, held in CSS as well as in the drag:
            // a window that shrinks under the dock must not squeeze the chat away,
            // and clamping here leaves the width the reader chose on record for
            // when the room comes back.
            maxWidth: `calc(100vw - ${MIN_CHAT_PX}px)`,
          }}
          className="absolute inset-y-0 right-0 z-30 flex border-l border-[var(--color-border)] bg-[var(--color-bg)] xl:relative xl:z-auto"
        >
          <DockResizeHandle
            width={width}
            onWidth={setWidth}
            min={MIN_WIDTH}
            max={() => Math.max(MIN_WIDTH, window.innerWidth - MIN_CHAT_PX)}
            reset={DEFAULT_WIDTH}
            label="Resize dock"
          />
          <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
            <div className={`${WORKSPACE_BAR} gap-2 px-3`}>
              <span className="min-w-0 flex-1 truncate text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                This conversation
              </span>
              <button
                onClick={closeAll}
                className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                title="Close"
              >
                <PanelRightClose className="h-3.5 w-3.5" />
              </button>
            </div>

            <DockSection
              icon={<Radio className="h-3 w-3 shrink-0" />}
              label="Tasks"
              hint="Work handed to other agents from this conversation"
              open={shown.includes("tasks")}
              onToggle={() => toggle("tasks")}
            >
              <DockTasks
                delegations={delegations}
                conversationId={conversationId}
                agentSlug={agentSlug}
              />
            </DockSection>

            <DockSection
              icon={<Zap className="h-3 w-3 shrink-0" />}
              label="Routines"
              hint="Scripts this agent runs, on demand or on a schedule"
              open={shown.includes("routines")}
              onToggle={() => toggle("routines")}
            >
              <DockRoutines
                instances={instances}
                agentSlug={agentSlug}
                conversationId={conversationId}
                onLibraryChange={openFocus}
              />
            </DockSection>
          </div>
        </aside>
      )}
      {librarySheet}
    </>
  );
}
