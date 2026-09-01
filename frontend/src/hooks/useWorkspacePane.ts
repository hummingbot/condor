import { createContext, useContext } from "react";

/**
 * What kind of thing is in the pane — see `PANE_PROFILES` in `WorkspacePane`.
 *
 * `read` is content you read (a report, a delegation's result); `tune` is a
 * surface you work in with one eye on the chat beside it (the agent panel).
 * The two open at different splits and remember their own.
 */
export type PaneProfile = "read" | "tune";

export type WorkspacePane = {
  /** Where a split sheet portals its body. */
  host: HTMLElement | null;
  setHost: (el: HTMLElement | null) => void;
  /**
   * Take the pane for `token`; the returned function gives it back.
   *
   * The first claim wins and later ones are refused, which is what keeps two
   * sheets from portalling into the one `aside` and stacking.
   *
   * `kind` is what the claimant is, which is what decides the split it opens
   * at — a report and a workbench want different halves of the row.
   */
  claim: (token: string, kind?: PaneProfile) => () => void;
  /** Who is in the pane right now, or `null` — see {@link WorkspaceSheet}. */
  holder: string | null;
  /** Something is in the pane right now. */
  open: boolean;
  /** There is room to split — otherwise sheets stay overlays. */
  canSplit: boolean;
  /** Share of the chat+pane row the pane takes, 0..1. */
  frac: number;
  setFrac: (f: number) => void;
  /** The current occupant's opening split, which the handle resets to. */
  defaultFrac: number;
};

/** Provided by `WorkspacePaneProvider`; absent on every other surface. */
export const WorkspacePaneContext = createContext<WorkspacePane | null>(null);

/**
 * The pane, if the surface around you offers one.
 *
 * `null` everywhere but the chat workspace, which is the point: a sheet opened
 * from an agent's own page has no conversation to sit beside, so it keeps the
 * overlay it has always been.
 */
export function useWorkspacePane(): WorkspacePane | null {
  return useContext(WorkspacePaneContext);
}
