import { useQuery } from "@tanstack/react-query";
import {
  Bot,
  ChevronDown,
  ChevronRight,
  PanelRightClose,
  Radio,
  Zap,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import {
  DockRoutines,
  RoutineLibrarySheet,
  conversationInstances,
  type LibraryFocus,
} from "@/components/chat/DockRoutines";
import { DockTasks } from "@/components/chat/DockTasks";
import type { RoutineRunContext } from "@/components/routines/ReportBrowser";
import { RoutinePicker } from "@/components/routines/RoutinePicker";
import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
import { useResizeDrag } from "@/hooks/useResizeDrag";
import { useWorkspacePane } from "@/hooks/useWorkspacePane";
import { api, type Delegation } from "@/lib/api";
import { inScope, resolveRoutine, type RoutineScope } from "@/lib/routineUtils";

const OPEN_KEY = "condor.dock.open";
const WIDTH_KEY = "condor.dock.width";
/** Where the dock stops being a column and starts overlaying (Tailwind `xl`). */
const WIDE = "(min-width: 1280px)";

const DEFAULT_WIDTH = 300;
/** Narrower than this and the rows are ellipses; the column stops being readable. */
const MIN_WIDTH = 220;
/** The transcript keeps at least this much, whatever the dock is dragged to. */
const MIN_CHAT_PX = 420;

function readWidth(): number {
  const stored = Number(localStorage.getItem(WIDTH_KEY));
  return Number.isFinite(stored) && stored >= MIN_WIDTH
    ? stored
    : DEFAULT_WIDTH;
}

/** The dock's default: a column when there is room for one, an icon rail when not. */
function defaultOpen(): boolean {
  return (
    window.matchMedia(WIDE).matches &&
    localStorage.getItem(OPEN_KEY) !== "false"
  );
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
 * And like the rail, it steps back to its icon strip while something is in the
 * workspace pane: a routine's report opens on a conversation with both flanks
 * folded away, so what you asked for and what you asked it of are the only two
 * things on screen. It is a loan — the column comes back the moment the pane
 * closes, and only a collapse of the reader's own is written down as how the
 * workspace opens next time.
 *
 * Every occupant of the pane borrows it, the agent panel included. The panel
 * used to be the exception, because the model and server pickers that steered
 * it lived in the card up here; they were moved into the panel's own bar so
 * that the exception could go. One rule for the whole pane is worth more than
 * a column that folds for a report and stays put for an agent, which is a
 * difference the reader can only learn by being surprised by it.
 *
 * The card comes first, then the sections: who is answering, then what that
 * has set in motion.
 *
 * The two sections are panes, not a stack: each scrolls inside itself, so an
 * expanded Tasks can never push Routines off the bottom. Both headers stay on
 * screen and one click away — which is the whole point of a dock you are meant
 * to watch while you type. The column keeps a scrollbar of its own only as the
 * escape hatch for a window too short to honour both panes' floors; clipping
 * a header there would be the very failure the panes exist to prevent.
 */
export function ContextDock({
  delegations,
  conversationId,
  agentSlug,
  agentName,
  agentCard,
  runContext,
  library,
  onLibraryChange,
}: {
  /** The shared `["delegations"]` result — the dock adds no poll of its own. */
  delegations: Delegation[];
  conversationId: string;
  agentSlug: string;
  /** Who is answering, for the library bar's accessible name. */
  agentName?: string;
  /**
   * Who is answering, as a card — the identity, the model and the server.
   *
   * Passed in rather than built here: it is wired to the session, and this
   * column knows about work, not about sessions. What it does know is that the
   * card belongs above Tasks, because "who" comes before "what they are doing".
   */
  agentCard?: React.ReactNode;
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
  const [open, setOpen] = useState(defaultOpen);
  const [tasksOpen, setTasksOpen] = useState(true);
  // Expanded like Tasks: a collapsed section is a section nobody finds, and
  // "where do I watch this?" was the question both of them exist to answer.
  // The extra poll it costs is one endpoint on a page that is already open.
  const [routinesOpen, setRoutinesOpen] = useState(true);
  const setLibrary = onLibraryChange;
  // Whose routines the picker lists — the agent on the other end of this
  // conversation, since those are the ones it can run.
  const [scope, setScope] = useState<RoutineScope>(agentSlug || "all");
  const [scopeAgent, setScopeAgent] = useState(agentSlug);
  const [width, setWidth] = useState(readWidth);

  /**
   * The pane borrows this column, exactly as it borrows the rail's.
   *
   * Four columns — rail, transcript, pane, dock — is more chrome than any
   * window has, and of the four the two flanks are the ones you are least
   * likely to be reading while a report is open beside the conversation. The
   * value borrowed is the value given back, so a dock the reader had already
   * tidied away stays tidied.
   */
  const paneOpen = useWorkspacePane()?.open ?? false;
  const lent = useRef<boolean | null>(null);
  useEffect(() => {
    if (paneOpen) {
      if (lent.current === null) {
        lent.current = open;
        setOpen(false);
      }
    } else if (lent.current !== null) {
      setOpen(lent.current);
      lent.current = null;
    }
    // `open` is read, not watched: re-running this on the reader's own expand
    // would take the column straight back off them.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [paneOpen]);

  /**
   * Only the reader's own toggle is written down. What the layout does on its
   * own — a loan to the pane, a window too narrow — is about this moment, and a
   * passing squeeze must not become the answer for every session after it.
   */
  const toggle = (next: boolean) => {
    setOpen(next);
    lent.current = null;
    localStorage.setItem(OPEN_KEY, String(next));
  };

  useEffect(() => {
    localStorage.setItem(WIDTH_KEY, String(Math.round(width)));
  }, [width]);

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

  // Crossing the breakpoint re-derives the default: a narrow window must not
  // wake up with an overlay parked on top of the transcript.
  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const onChange = () => {
      // The default is being re-derived from the window, so there is no loan
      // left to repay — repaying one here is how a narrow window would wake up
      // with the dock parked back on top of the transcript.
      lent.current = null;
      setOpen(defaultOpen());
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const { data: instances = [] } = useQuery({
    queryKey: ["routine-instances"],
    queryFn: api.getRoutineInstances,
    // Polled whenever the dock is open, not only while the section is: a
    // collapsed Routines still has to be able to say "one is running". The
    // library pane reads the same list, and it can be up while the column is
    // not, so it keeps the poll alive on its own.
    enabled: open || !!library,
    refetchInterval: 5000,
  });

  // The library itself, for the picker. Shares react-query's cache with the
  // report browser, so the pane it opens costs no second fetch.
  const { data: routines = [] } = useQuery({
    queryKey: ["routines"],
    queryFn: api.getRoutines,
    enabled: open || !!library,
  });

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

  const mineRunning = delegations.filter(
    (d) => d.conversation_id === conversationId && d.status === "running",
  ).length;
  // Counted the same way the list is built, so the badge and the rows agree.
  const routinesRunning = conversationInstances(
    instances,
    agentSlug,
    conversationId,
  ).filter((i) => i.status === "running").length;

  const librarySheet = library && (
    <RoutineLibrarySheet
      library={library}
      instances={instances}
      routines={routines}
      scope={effectiveScope}
      onScopeChange={changeScope}
      onSelectRoutine={(name) => openFocus({ source: name })}
      // A collapsed column, or a collapsed section, is a picker off screen.
      dockOpen={open && routinesOpen}
      agentName={agentName}
      runContext={runContext}
      onClose={() => setLibrary(null)}
    />
  );

  /**
   * Whose routines this conversation is looking at.
   *
   * Only half of the pair: which routine is open is asked in the library's own
   * nav bar, over the report it names, so the two controls are never both on
   * screen saying the same thing.
   */
  const picker = (
    <RoutinePicker
      parts="scope"
      routines={routines}
      instances={instances}
      scope={effectiveScope}
      onScopeChange={changeScope}
      source={library?.source}
      onSelect={(name) => openFocus({ source: name })}
    />
  );

  /**
   * The sheet is a sibling of the column, not a child of it.
   *
   * It portals its body into the pane either way, but where it sits in *this*
   * tree decides whether it survives: nested in the `aside`, the two branches
   * below put it at different indices, so collapsing the column unmounted it —
   * which released the pane, which gave the column back, which mounted it
   * again. Held here it keeps its identity, and the report inside it keeps its
   * scroll, through a collapse the pane itself asked for.
   */
  if (!open) {
    return (
      <>
        <aside className="flex w-10 shrink-0 flex-col items-center gap-3 border-l border-[var(--color-border)] bg-[var(--color-bg)] py-2">
          {agentCard && (
            <button
              onClick={() => toggle(true)}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Show who is answering"
            >
              <Bot className="h-4 w-4" />
            </button>
          )}
          <button
            onClick={() => toggle(true)}
            className="flex flex-col items-center gap-0.5 rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title="Show what this conversation is doing"
          >
            <Radio
              className={`h-4 w-4 ${mineRunning > 0 ? "text-emerald-400" : ""}`}
            />
            {mineRunning > 0 && (
              <span className="text-[9px] font-bold text-emerald-400">
                {mineRunning}
              </span>
            )}
          </button>
          <button
            onClick={() => {
              toggle(true);
              setRoutinesOpen(true);
            }}
            className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title="Show routines"
          >
            <Zap className="h-4 w-4" />
          </button>
        </aside>
        {librarySheet}
      </>
    );
  }

  return (
    <>
      <aside
        style={{
          width,
          minWidth: MIN_WIDTH,
          // The floor the transcript keeps, held in CSS as well as in the drag:
          // a window that shrinks under the dock must not squeeze the chat away,
          // and clamping here leaves the width the reader chose on record for
          // when the room comes back.
          maxWidth: `calc(100vw - ${MIN_CHAT_PX}px)`,
        }}
        className="absolute inset-y-0 right-0 z-30 flex shrink-0 border-l border-[var(--color-border)] bg-[var(--color-bg)] xl:relative xl:z-auto"
      >
        <DockResizeHandle width={width} onWidth={setWidth} />
        <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
          <div className={`${WORKSPACE_BAR} gap-2 px-3`}>
            <span className="min-w-0 flex-1 truncate text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              This conversation
            </span>
            <button
              onClick={() => toggle(false)}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Collapse"
            >
              <PanelRightClose className="h-3.5 w-3.5" />
            </button>
          </div>

          {agentCard}

          <DockSection
            icon={
              <Radio
                className={`h-3 w-3 shrink-0 ${mineRunning > 0 ? "text-emerald-400" : ""}`}
              />
            }
            label="Tasks"
            hint="Work handed to other agents from this conversation"
            count={mineRunning || undefined}
            open={tasksOpen}
            onToggle={() => setTasksOpen((v) => !v)}
          >
            <DockTasks
              delegations={delegations}
              conversationId={conversationId}
              agentSlug={agentSlug}
            />
          </DockSection>

          <DockSection
            icon={
              <Zap
                className={`h-3 w-3 shrink-0 ${routinesRunning > 0 ? "text-emerald-400" : ""}`}
              />
            }
            label="Routines"
            hint="Scripts this agent runs, on demand or on a schedule"
            count={routinesRunning || undefined}
            open={routinesOpen}
            onToggle={() => setRoutinesOpen((v) => !v)}
            toolbar={picker}
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
      {librarySheet}
    </>
  );
}

/**
 * The seam between the conversation and the dock, and the way to move it.
 *
 * Three columns of chrome share one window, and how much of it this one is
 * worth depends on what the reader is doing: watching a routine's rows wants
 * room, typing wants the transcript. So it is theirs to set, and remembered —
 * the width is per-browser, like the collapse beside it.
 *
 * Dragging left grows the dock, which is the direction a right-anchored panel's
 * edge moves; the arrows do the same, so the handle is reachable without a
 * pointer. Double-click puts it back.
 */
function DockResizeHandle({
  width,
  onWidth,
}: {
  width: number;
  onWidth: (next: number) => void;
}) {
  const { onMouseDown, isDragging } = useResizeDrag({
    axis: "x",
    value: width,
    onChange: onWidth,
    min: MIN_WIDTH,
    max: () => Math.max(MIN_WIDTH, window.innerWidth - MIN_CHAT_PX),
    direction: "inverted",
    cursor: "col-resize",
    lockUserSelect: true,
  });

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowLeft") onWidth(width + 16);
    else if (e.key === "ArrowRight") onWidth(Math.max(MIN_WIDTH, width - 16));
    else return;
    e.preventDefault();
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize dock"
      aria-valuenow={Math.round(width)}
      aria-valuemin={MIN_WIDTH}
      tabIndex={0}
      onMouseDown={onMouseDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => onWidth(DEFAULT_WIDTH)}
      title="Drag to resize — double-click to reset"
      className={`-ml-1 w-1.5 shrink-0 cursor-col-resize transition-colors hover:bg-[var(--color-primary)]/30 focus:outline-none focus-visible:bg-[var(--color-primary)]/30 ${
        isDragging ? "bg-[var(--color-primary)]/30" : ""
      }`}
    />
  );
}

/**
 * One pane of the dock.
 *
 * Open, it takes a fixed share of the column — `flex-1 basis-0`, so two open
 * panes are half and half no matter what is in them. Sizing from content
 * instead (`flex-auto`) looks tidier on a quiet conversation and is unusable on
 * a busy one: every task that starts or routine that finishes moves the divider,
 * so the row you were reading slides out from under the cursor. A boundary that
 * never moves is worth more than one that is always optimally placed.
 *
 * The body owns the scrollbar, so the header never leaves the viewport whatever
 * the list does.
 *
 * Closed, it is just the header bar — the deliberate way to give the other pane
 * the whole column.
 */
function DockSection({
  icon,
  label,
  hint,
  count,
  open,
  onToggle,
  toolbar,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  /** What this section is, for the reader who has to tell it from the other. */
  hint: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  /**
   * Controls that steer what the body shows, between the header and the list.
   * Outside the scroller on purpose: a filter that scrolls away with the rows
   * it filters is one you have to go looking for.
   */
  toolbar?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`flex flex-col border-b border-[var(--color-border)] ${
        open ? "min-h-[72px] flex-1 basis-0 overflow-hidden" : "shrink-0"
      }`}
    >
      <button
        onClick={onToggle}
        title={hint}
        className="flex w-full shrink-0 items-center gap-1.5 px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        {icon}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {count !== undefined && (
          <span className="shrink-0 text-emerald-400">{count}</span>
        )}
      </button>
      {open && toolbar}
      {open && (
        <div className="min-h-0 flex-1 overflow-y-auto pb-1">{children}</div>
      )}
    </div>
  );
}
