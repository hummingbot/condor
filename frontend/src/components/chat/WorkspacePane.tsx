import { useCallback, useEffect, useMemo, useState } from "react";

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
 * instead.
 */
const WIDE = "(min-width: 1280px)";

/**
 * A conversation that can be read alongside its work.
 *
 * The dock's sheets used to cover the chat whole — you opened a routine's
 * report and the agent that produced it was gone until you closed it again,
 * which is exactly backwards for a report you want to ask about. Inside this
 * provider a sheet renders into {@link WorkspacePaneOutlet} instead: the
 * transcript keeps the left of the window and stays live, the report takes the
 * right. Full screen is still one click away, for reading rather than talking.
 */
export function WorkspacePaneProvider({
  children,
}: {
  children: React.ReactNode;
}) {
  const [host, setHost] = useState<HTMLElement | null>(null);
  const [claims, setClaims] = useState(0);
  const [wide, setWide] = useState(() => window.matchMedia(WIDE).matches);

  useEffect(() => {
    const mq = window.matchMedia(WIDE);
    const onChange = () => setWide(mq.matches);
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const claim = useCallback(() => {
    setClaims((c) => c + 1);
    return () => setClaims((c) => c - 1);
  }, []);

  const value = useMemo<WorkspacePane>(
    () => ({ host, setHost, claim, open: claims > 0, canSplit: wide }),
    [host, claim, claims, wide],
  );

  return (
    <WorkspacePaneContext.Provider value={value}>
      {children}
    </WorkspacePaneContext.Provider>
  );
}

/**
 * Where the pane lives in the layout — between the conversation and the dock.
 *
 * Always mounted, so a sheet has a target to portal into on its first render
 * rather than after an effect; it is only given width once something claims it.
 * Wider than the transcript beside it because the thing in it is a page — a
 * report laid out for a page's width — while the chat only needs its measure.
 */
export function WorkspacePaneOutlet() {
  const pane = useWorkspacePane();
  if (!pane) return null;
  // Destructured, not read through `pane` in the JSX: the ref lint rule reads
  // any member of an object whose property is passed as `ref` as a ref itself.
  const { setHost, open } = pane;
  return (
    <aside
      ref={setHost}
      aria-label="Workspace pane"
      className={
        open
          ? "flex min-w-0 flex-[1.4] flex-col border-l border-[var(--color-border)] bg-[var(--color-bg)]"
          : "hidden"
      }
    />
  );
}
