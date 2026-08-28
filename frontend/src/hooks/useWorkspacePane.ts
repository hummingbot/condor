import { createContext, useContext } from "react";

export type WorkspacePane = {
  /** Where a split sheet portals its body. */
  host: HTMLElement | null;
  setHost: (el: HTMLElement | null) => void;
  /** Take the pane; the returned function gives it back. */
  claim: () => () => void;
  /** Something is in the pane right now. */
  open: boolean;
  /** There is room to split — otherwise sheets stay overlays. */
  canSplit: boolean;
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
