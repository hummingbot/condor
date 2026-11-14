import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { DockSplitHandle } from "@/components/chat/DockSplitHandle";
import { useDockSplit } from "@/hooks/useDockSplit";
import { useResizeDrag } from "@/hooks/useResizeDrag";
import {
  useWorkspacePane,
  WorkspacePaneContext,
  type PaneProfile,
  type WorkspacePane,
} from "@/hooks/useWorkspacePane";
import {
  PANE_FRAC_KEY,
  PANE_FRAC_TUNE_KEY,
  SIDE_SPLIT_KEY,
  STACK_SPLIT_KEY,
} from "@/lib/sessionState";

/**
 * Below this the workspace has no room for another column, so a sheet keeps its
 * overlay.
 *
 * `xl`, the same width at which the dock stops overlaying the transcript —
 * splitting a conversation the dock is already parked on top of would leave
 * nothing readable. It fits at 1280 because the rail steps back to a strip
 * while the pane is open (see `ChatRail`), and the reader can collapse the dock
 * too: between them that is 520px the transcript and the report get to share
 * instead. The chat column's own floor is written as Tailwind's `xl:` in
 * `AgentChatTab`, which is this same breakpoint.
 */
const WIDE = "(min-width: 1280px)";

/**
 * Opening split, and what a double-click on the handle returns to — one per
 * kind of occupant, because the two want different halves of the row.
 *
 * `read` is a page: a report laid out for a page's width, inside a browser that
 * is itself two columns, while the transcript is capped at `max-w-3xl` anyway,
 * so width past its measure becomes margin rather than text. It gets the larger
 * share.
 *
 * `tune` is a workbench you keep one hand on — the agent panel, which you open
 * to change one thing and read the answer beside it. Two thirds of the row left
 * the conversation it is meant to be read against at its 360px floor, which is
 * a column of five-word lines. An even split is the honest default when both
 * sides are being used at once.
 *
 * Each remembers the reader's own drag under its own key: one stored number
 * would have a report's width become the panel's, and back again.
 */
const PANE_PROFILES: Record<PaneProfile, { key: string; frac: number }> = {
  read: { key: PANE_FRAC_KEY, frac: 0.62 },
  tune: { key: PANE_FRAC_TUNE_KEY, frac: 0.5 },
};

/**
 * The envelope a fraction may take, whatever the window does.
 *
 * These bound the stored value and the keyboard steps; a drag is bounded in
 * pixels instead (below), because at 1280 a floor in percent is the wrong unit —
 * what matters there is that neither column falls under its own minimum.
 */
const MIN_FRAC = 0.3;
const MAX_FRAC = 0.75;

/** Neither column is readable below these, and both fit at 1280. */
const MIN_PANE_PX = 400;
const MIN_CHAT_PX = 360;

function clampFrac(f: number, profile: PaneProfile) {
  if (!Number.isFinite(f)) return PANE_PROFILES[profile].frac;
  return Math.max(MIN_FRAC, Math.min(MAX_FRAC, f));
}

function readFrac(profile: PaneProfile): number {
  const { key, frac } = PANE_PROFILES[profile];
  try {
    const stored = localStorage.getItem(key);
    return stored === null ? frac : clampFrac(parseFloat(stored), profile);
  } catch {
    return frac;
  }
}

/**
 * A conversation that can be read alongside its work.
 *
 * The dock's sheets used to cover the chat whole — you opened a routine's
 * report and the agent that produced it was gone until you closed it again,
 * which is exactly backwards for a report you want to ask about. Inside this
 * provider a sheet renders into {@link WorkspacePaneOutlet} instead: the
 * transcript keeps the left of the window and stays live, the report takes the
 * right. Full screen is still one click away, for reading rather than talking.
 *
 * How much of the row each side gets is the reader's, not a constant: the split
 * is a stored fraction, dragged on the pane's own edge (ARCH-273).
 */
export function WorkspacePaneProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const [belowHost, setBelowHost] = useState<HTMLElement | null>(null);
  const [holder, setHolder] = useState<string | null>(null);
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  /** Which kind of pane the split is currently about — see `PANE_PROFILES`. */
  const [profile, setProfile] = useState<PaneProfile>("read");
  const [fracs, setFracs] = useState(() => ({
    read: readFrac("read"),
    tune: readFrac("tune"),
  }));
  const frac = fracs[profile];

  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const onChange = () => setWide(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(PANE_PROFILES[profile].key, String(frac));
    } catch {
      /* private mode; the split just lasts the session */
    }
  }, [profile, frac]);

  const setFrac = useCallback(
    (f: number) =>
      setFracs((prev) => ({ ...prev, [profile]: clampFrac(f, profile) })),
    [profile],
  );

  /**
   * Who has the pane.
   *
   * A count would say "something is in there" but not *what*, and the pane is
   * one node: the second sheet to portal into it draws over the first. Naming
   * the holder lets a sheet read, in its own render, whether the pane is
   * already someone else's and stay the overlay it is below `xl` if so.
   */
  const claim = useCallback((token: string, kind: PaneProfile = "read") => {
    setHolder((h) => h ?? token);
    // The occupant's kind travels with the claim, so the split it opens at is
    // the one that kind was measured for. Only the sheet that is actually
    // splitting ever claims — a refused sheet is an overlay and never gets
    // here — so this cannot be some other pane's profile.
    setProfile(kind);
    return () => setHolder((h) => (h === token ? null : h));
  }, []);

  const value = useMemo<WorkspacePane>(
    () => ({
      host,
      setHost,
      belowHost,
      setBelowHost,
      claim,
      holder,
      open: holder !== null,
      canSplit: wide,
      frac,
      setFrac,
      defaultFrac: PANE_PROFILES[profile].frac,
    }),
    [host, belowHost, claim, holder, wide, frac, setFrac, profile],
  );

  return (
    <WorkspacePaneContext.Provider value={value}>
      {children}
    </WorkspacePaneContext.Provider>
  );
}

/**
 * The seam between the conversation and the pane, and the way to move it.
 *
 * A sibling rather than a child of the pane, because the pane is also a portal
 * host: everything React renders inside it would be sharing the node with DOM
 * the portal owns.
 *
 * The reference frame is measured once, in `onMouseDown`, from the two elements
 * on either side — a reflow mid-drag must not move the frame the pointer is
 * being read against. `useResizeDrag` clamps in pixels, so the floors hold at
 * any window width; the result is stored back as a fraction, which is what keeps
 * the split responsive when the window changes afterwards.
 */
function PaneResizeHandle({
  frac,
  setFrac,
  defaultFrac,
}: {
  frac: number;
  setFrac: (f: number) => void;
  /** What a double-click returns to — the opening split of whoever is in. */
  defaultFrac: number;
}) {
  const geom = useRef({ rowRight: 0, avail: 1 });

  const { onMouseDown: startDrag, isDragging } = useResizeDrag({
    axis: "x",
    value: 0, // `compute` is absolute; the drag has no starting size to grow.
    onChange: (px) => setFrac(px / geom.current.avail),
    min: MIN_PANE_PX,
    max: () => geom.current.avail - MIN_CHAT_PX,
    compute: (coord) => geom.current.rowRight - coord,
    cursor: "col-resize",
    lockUserSelect: true,
  });

  const onMouseDown = (e: React.MouseEvent) => {
    const handle = e.currentTarget;
    const chat = handle.previousElementSibling as HTMLElement | null;
    const pane = handle.nextElementSibling as HTMLElement | null;
    if (chat && pane) {
      geom.current = {
        rowRight: pane.getBoundingClientRect().right,
        avail: chat.offsetWidth + pane.offsetWidth,
      };
    }
    startDrag(e);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    // Left grows the pane, right gives the room back to the transcript — the
    // handle moves the way the arrow points.
    if (e.key === "ArrowLeft") setFrac(frac + 0.02);
    else if (e.key === "ArrowRight") setFrac(frac - 0.02);
    else return;
    e.preventDefault();
  };

  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize workspace pane"
      aria-valuenow={Math.round(frac * 100)}
      aria-valuemin={Math.round(MIN_FRAC * 100)}
      aria-valuemax={Math.round(MAX_FRAC * 100)}
      tabIndex={0}
      onMouseDown={onMouseDown}
      onKeyDown={onKeyDown}
      onDoubleClick={() => setFrac(defaultFrac)}
      title="Drag to resize — double-click to reset"
      className={`w-1.5 shrink-0 cursor-col-resize transition-colors hover:bg-[var(--color-primary-fill)]/30 focus:outline-none focus-visible:bg-[var(--color-primary-fill)]/30 ${
        isDragging ? "bg-[var(--color-primary-fill)]/30" : ""
      }`}
    />
  );
}

/**
 * Where the pane lives in the layout — between the conversation and the dock.
 *
 * Always mounted, so a sheet has a target to portal into on its first render
 * rather than after an effect; it is only given width once something claims it.
 * The width is a ratio rather than a measurement, so the two columns keep their
 * proportions when the window changes and only the drag has to measure at all.
 */
export function WorkspacePaneOutlet({
  split: dir = null,
}: {
  /**
   * The layout picker's free "Chat + 2": the pane and the slot beside it both
   * hold a panel. `col` puts the second under the first (stacked), `row`
   * beside it (side by side); each draws its seam and divides by its own
   * remembered share. `null`: just the pane.
   */
  split?: "col" | "row" | null;
} = {}) {
  const pane = useWorkspacePane();
  // Before the early return: hooks run on every render. Each arrangement keeps
  // its own seam — a stack split 50/50 says nothing about how wide a side
  // column should be.
  const stackSplit = useDockSplit(STACK_SPLIT_KEY);
  const sideSplit = useDockSplit(SIDE_SPLIT_KEY, 0.6);
  if (!pane) return null;
  // Destructured, not read through `pane` in the JSX: the ref lint rule reads
  // any member of an object whose property is passed as `ref` as a ref itself.
  const { setHost, setBelowHost, open, frac, setFrac, defaultFrac, canSplit } = pane;
  // Too narrow to sit beside the chat: the split becomes what every sheet is
  // at this width, an overlay over the transcript, stacked — rather than a
  // 640 px column that squeezes the chat to nothing.
  const overlay = dir !== null && !canSplit;
  const row = dir === "row" && !overlay;
  const split = row ? sideSplit : stackSplit;
  const shown = open || dir !== null;
  return (
    <>
      {shown && !overlay && (
        <PaneResizeHandle
          frac={frac}
          setFrac={setFrac}
          defaultFrac={defaultFrac}
        />
      )}
      {/* One column (or row): the pane, and the slot a free "Chat + 2" fills
          under or beside it. `flex-grow` against the chat's `flex-1`: the two
          grow in the ratio the reader left them in, whatever width the row
          turns out to have. Inline, so it overrides the `flex-1` shorthand. */}
      <div
        style={shown && !overlay ? { flexGrow: frac / (1 - frac) } : undefined}
        className={
          // Split, the column is up even when no sheet holds the pane: the
          // first panel may be Tasks/Routines, which draw here without a claim.
          !shown
            ? "hidden"
            : overlay
              ? "absolute inset-y-0 right-0 z-30 flex w-[min(480px,100%)] flex-col border-l border-[var(--color-border)] bg-[var(--color-bg)] shadow-xl"
              : `flex min-h-0 flex-1 border-l border-[var(--color-border)] bg-[var(--color-bg)] ${
                  row ? "min-w-[640px] flex-row" : "min-w-[400px] flex-col"
                }`
        }
      >
        <aside
          ref={setHost}
          aria-label="Workspace pane"
          // `basis-0` so the two halves divide by their grow alone — the shares
          // the seam sets — rather than by how big their content happens to be.
          style={dir ? { flexGrow: split.frac } : undefined}
          className="flex min-h-0 min-w-0 flex-1 basis-0 flex-col"
        />
        {dir && (
          <DockSplitHandle
            axis={row ? "x" : "y"}
            frac={split.frac}
            setFrac={split.setFrac}
            defaultFrac={split.defaultFrac}
            label={row ? "Resize the side-by-side panels" : "Resize the stacked panels"}
          />
        )}
        <div
          ref={setBelowHost}
          style={dir ? { flexGrow: 1 - split.frac } : undefined}
          className={`flex min-h-0 min-w-0 flex-1 basis-0 flex-col border-[var(--color-border)] empty:hidden ${
            row ? "border-l" : "border-t"
          }`}
        />
      </div>
    </>
  );
}
