import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useResizeDrag } from "@/hooks/useResizeDrag";
import {
  useWorkspacePane,
  WorkspacePaneContext,
  type WorkspacePane,
} from "@/hooks/useWorkspacePane";

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

/** Where the reader's split is remembered. */
const FRAC_KEY = "condor_pane_frac";

/**
 * Opening split, and what a double-click on the handle returns to.
 *
 * Wider than the chat beside it, because what lands in the pane is a page — a
 * report laid out for a page's width, inside a browser that is itself two
 * columns — while the transcript is capped at `max-w-3xl` anyway, so width past
 * its measure becomes margin rather than text.
 */
const DEFAULT_FRAC = 0.62;

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

function clampFrac(f: number) {
  if (!Number.isFinite(f)) return DEFAULT_FRAC;
  return Math.max(MIN_FRAC, Math.min(MAX_FRAC, f));
}

function readFrac(): number {
  try {
    const stored = localStorage.getItem(FRAC_KEY);
    return stored === null ? DEFAULT_FRAC : clampFrac(parseFloat(stored));
  } catch {
    return DEFAULT_FRAC;
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
  const [holder, setHolder] = useState<string | null>(null);
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);
  const [frac, setFracState] = useState(readFrac);

  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const onChange = () => setWide(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(FRAC_KEY, String(frac));
    } catch {
      /* private mode; the split just lasts the session */
    }
  }, [frac]);

  const setFrac = useCallback((f: number) => setFracState(clampFrac(f)), []);

  /**
   * Who has the pane.
   *
   * A count would say "something is in there" but not *what*, and the pane is
   * one node: the second sheet to portal into it draws over the first. Naming
   * the holder lets a sheet read, in its own render, whether the pane is
   * already someone else's and stay the overlay it is below `xl` if so.
   */
  const claim = useCallback((token: string) => {
    setHolder((h) => h ?? token);
    return () => setHolder((h) => (h === token ? null : h));
  }, []);

  const value = useMemo<WorkspacePane>(
    () => ({
      host,
      setHost,
      claim,
      holder,
      open: holder !== null,
      canSplit: wide,
      frac,
      setFrac,
    }),
    [host, claim, holder, wide, frac, setFrac],
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
}: {
  frac: number;
  setFrac: (f: number) => void;
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
      onDoubleClick={() => setFrac(DEFAULT_FRAC)}
      title="Drag to resize — double-click to reset"
      className={`w-1.5 shrink-0 cursor-col-resize transition-colors hover:bg-[var(--color-primary)]/30 focus:outline-none focus-visible:bg-[var(--color-primary)]/30 ${
        isDragging ? "bg-[var(--color-primary)]/30" : ""
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
export function WorkspacePaneOutlet() {
  const pane = useWorkspacePane();
  if (!pane) return null;
  // Destructured, not read through `pane` in the JSX: the ref lint rule reads
  // any member of an object whose property is passed as `ref` as a ref itself.
  const { setHost, open, frac, setFrac } = pane;
  return (
    <>
      {open && <PaneResizeHandle frac={frac} setFrac={setFrac} />}
      <aside
        ref={setHost}
        aria-label="Workspace pane"
        // `flex-grow` against the chat's `flex-1`: the two grow in the ratio the
        // reader left them in, whatever width the row turns out to have. Inline,
        // so it overrides the `flex-1` shorthand's own grow.
        style={open ? { flexGrow: frac / (1 - frac) } : undefined}
        className={
          open
            ? "flex min-w-[400px] flex-1 flex-col border-l border-[var(--color-border)] bg-[var(--color-bg)]"
            : "hidden"
        }
      />
    </>
  );
}
