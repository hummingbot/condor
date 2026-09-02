import { Cpu, PanelRightClose, Wallet } from "lucide-react";
import { useEffect, useState } from "react";

import { DockExecution, TABLE_MIN_PX } from "@/components/chat/DockExecution";
import { DockPortfolio } from "@/components/chat/DockPortfolio";
import { DockResizeHandle } from "@/components/chat/DockResizeHandle";
import { DockSection } from "@/components/chat/DockSection";
import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
import { ACCOUNT_DOCK_KEY, ACCOUNT_DOCK_WIDTH_KEY } from "@/lib/sessionState";

/** The two questions a person holds in their head while typing at a trader. */
type PanelId = "portfolio" | "execution";

/**
 * Wide enough for the execution table, plus room for a real controller name.
 *
 * Derived from the table's own floor rather than guessed at: a column whose
 * default width is narrower than the thing in it opens on a horizontal
 * scrollbar, and a reader who has to drag a seam before the panel is legible
 * will conclude the panel is broken rather than that it is resizable. The
 * margin on top is the label column's, because at exactly the floor every
 * config id is an ellipsis.
 */
const DEFAULT_WIDTH = TABLE_MIN_PX + 80;
/** Below the table's own floor it stops being a table and starts scrolling. */
const MIN_WIDTH = TABLE_MIN_PX;
/**
 * The ceiling, as a share of the window rather than a subtraction from it.
 *
 * `calc(100vw - <floors>)` is the spelling the context dock uses and it cannot
 * be the one here: this column is outboard of *that* one, so its subtraction
 * would have to carry the dock's width too, and on a narrow window the result
 * goes negative — a negative `max-width` is invalid, the declaration is dropped
 * and the clamp silently stops existing at exactly the width it is for. A
 * fraction is always positive.
 */
const MAX_WIDTH_VW = 0.45;

const PANELS: {
  id: PanelId;
  label: string;
  Icon: typeof Wallet;
  /** What the section is, for the reader telling it from the one below. */
  hint: string;
  /** Why the tab is dead when there is no server to ask about. */
  disabledHint: string;
}[] = [
  {
    id: "portfolio",
    label: "Portfolio",
    Icon: Wallet,
    hint: "What you hold, by asset and by venue",
    disabledHint: "Select a server to see your portfolio",
  },
  {
    id: "execution",
    label: "Execution",
    Icon: Cpu,
    hint: "The controllers trading right now, and what each has done",
    disabledHint: "Select a server to see what is running",
  },
];

function readOpen(): PanelId[] {
  try {
    const raw = JSON.parse(localStorage.getItem(ACCOUNT_DOCK_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return PANELS.map((p) => p.id).filter((id) => raw.includes(id));
  } catch {
    // Unreadable storage is a browser that has never opened a panel, not an
    // error to surface: the rail is how they come back either way.
    return [];
  }
}

function readWidth(): number {
  const stored = Number(localStorage.getItem(ACCOUNT_DOCK_WIDTH_KEY));
  return Number.isFinite(stored) && stored >= MIN_WIDTH ? stored : DEFAULT_WIDTH;
}

/**
 * The desk you trade, beside the conversation you are having about it.
 *
 * `ContextDock`'s subject is *this conversation* — the tasks it started, the
 * routines the agent owns. These two panels are about the **server**, which is
 * a different subject and does not belong under that header; this dock names
 * the server in its own bar, so the two can never be read for each other's
 * numbers.
 *
 * ## Where it sits (FEAT-094, revised)
 *
 * A rail and a column, both **inboard of the context dock**: the row reads
 * transcript, panels, this rail, then "this conversation". It shipped the
 * other way round — a rail on the far right and a panel floating at `z-40`
 * over the dock — and the float was the mistake. An open panel covered Tasks
 * and Routines exactly, so the two docks were mutually exclusive in practice:
 * you could watch a delegation or watch your balance, never both, and the
 * "glance model" that justified it was really a reader closing one thing to
 * see another. Two questions asked at the same time need two columns on screen
 * at the same time.
 *
 * So the panels take their width from the transcript, in flow, and the reader
 * decides how much: the seam is draggable and the width is remembered, like the
 * dock's beside it. What the float bought — a transcript that never moves — was
 * never worth what it cost, and it was only ever free because nothing was
 * visible underneath.
 *
 * The rail sits between the panels and the dock rather than at the far edge, so
 * every column keeps its own controls on its own side: the panels open leftward
 * away from their rail, the dock collapses rightward to its own.
 *
 * **Closed still costs nothing.** With no panel open there is no column at all,
 * so neither panel's queries nor its socket channels are ever mounted: the
 * Agents page is exactly as expensive as it was for anyone who never opens a
 * tab. That is why a closed `DockSection` unmounts its body rather than hiding
 * it, and it is the one part of the original design that the move does not
 * touch.
 */
export function AccountDock({ server }: { server: string | null }) {
  const [open, setOpen] = useState<PanelId[]>(readOpen);
  const [width, setWidth] = useState(readWidth);

  const toggle = (id: PanelId) => {
    setOpen((prev) => {
      const next = prev.includes(id)
        ? prev.filter((p) => p !== id)
        : [...prev, id];
      localStorage.setItem(ACCOUNT_DOCK_KEY, JSON.stringify(next));
      return next;
    });
  };

  const closeAll = () => {
    setOpen([]);
    localStorage.setItem(ACCOUNT_DOCK_KEY, "[]");
  };

  useEffect(() => {
    localStorage.setItem(ACCOUNT_DOCK_WIDTH_KEY, String(Math.round(width)));
  }, [width]);

  // A panel is only reachable with a server to ask about: a disclosure that
  // opens onto a column of zeroes is worse than a control that says why it is
  // dead. Anything left open from a previous server stays recorded and comes
  // back when one is selected again.
  const shown = server ? open : [];

  return (
    <>
      {shown.length > 0 && (
        <aside
          data-testid="account-dock"
          style={{ width, minWidth: MIN_WIDTH }}
          className="flex max-w-[45vw] shrink-0 border-l border-[var(--color-border)] bg-[var(--color-bg)]"
        >
          <DockResizeHandle
            width={width}
            onWidth={setWidth}
            min={MIN_WIDTH}
            max={() =>
              Math.max(MIN_WIDTH, window.innerWidth * MAX_WIDTH_VW)
            }
            reset={DEFAULT_WIDTH}
            label="Resize account panels"
          />
          <div className="flex min-w-0 flex-1 flex-col">
            {/* The server, once, at the top — a total with no desk attached to
                it is a number nobody can act on, and saying it on every
                section header spent the width the tables now use. */}
            <div className={`${WORKSPACE_BAR} gap-2 px-3`}>
              <span
                className="min-w-0 flex-1 truncate text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]"
                title={`Portfolio and execution on ${server}`}
              >
                {server}
              </span>
              <button
                onClick={closeAll}
                className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                title="Close"
              >
                <PanelRightClose className="h-3.5 w-3.5" />
              </button>
            </div>

            {PANELS.map(({ id, label, Icon, hint }) => (
              <DockSection
                key={id}
                icon={<Icon className="h-3 w-3 shrink-0" />}
                label={label}
                hint={hint}
                open={shown.includes(id)}
                onToggle={() => toggle(id)}
              >
                {id === "portfolio" ? (
                  <DockPortfolio server={server!} />
                ) : (
                  <DockExecution server={server!} />
                )}
              </DockSection>
            ))}
          </div>
        </aside>
      )}

      <aside className="flex w-10 shrink-0 flex-col items-center gap-3 border-l border-[var(--color-border)] bg-[var(--color-bg)] py-2">
        {PANELS.map(({ id, label, Icon, hint, disabledHint }) => {
          const isOpen = shown.includes(id);
          return (
            <button
              key={id}
              type="button"
              onClick={() => toggle(id)}
              disabled={!server}
              aria-pressed={isOpen}
              aria-label={label}
              title={server ? `${hint} · ${server}` : disabledHint}
              className={`rounded p-1 transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
                isOpen
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:hover:bg-transparent disabled:hover:text-[var(--color-text-muted)]"
              }`}
            >
              <Icon className="h-4 w-4" />
            </button>
          );
        })}
      </aside>
    </>
  );
}
