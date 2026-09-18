import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import {
  paneHandoffDropsPanel,
  type PaneView,
} from "@/components/chat/paneUrl";

/**
 * The agent panel's unsaved-edit guard, held by the pane's host (CORR-395).
 *
 * It used to live inside `AgentPanel` and gated only the sheet's own close —
 * the X and Escape. But the pane is URL state and the panel has many doors
 * out: the rail's Agent tile, a desk tile, the routine library, a strategy
 * card, an Execution row naming another agent. Every one of them writes
 * `?panel=` and unmounts the panel, dropping an editor's draft without a word.
 * The only place all of them pass through is the host's `openPane`, so that is
 * where the question is asked.
 *
 * `apply` is the host's unguarded writer. The returned `openPane` is what every
 * caller should use; `onPanelDirtyChange` goes to the panel; `dialog` is
 * rendered once by the host. Browser Back is not covered: the router offers no
 * blocker in this app, and `?panel=` leaving the URL is not a call anyone makes.
 */
export function usePaneGuard({
  pane,
  panelSlug,
  apply,
}: {
  /** What is in the pane now. */
  pane: PaneView;
  /** Whose panel a bare `{kind: "agent"}` means — the conversation's. */
  panelSlug: string;
  apply: (next: PaneView) => void;
}): {
  openPane: (next: PaneView) => void;
  onPanelDirtyChange: (dirty: boolean) => void;
  dialog: ReactNode;
} {
  const [panelDirty, setPanelDirty] = useState(false);
  // Boxed, so a pending `null` (close the pane) is told apart from no request.
  const [pending, setPending] = useState<{ next: PaneView } | null>(null);

  const guarded = (next: PaneView) => {
    if (panelDirty && paneHandoffDropsPanel(pane, next, panelSlug)) {
      setPending({ next });
      return;
    }
    apply(next);
  };
  // Handed out under one identity for the life of the host (PERF-393). `pane`
  // is a fresh object on every host render and `apply` a fresh closure, so an
  // `openPane` built over them changed on every 50 ms stream flush, and so did
  // every panel handler derived from it — which defeated the `memo` that keeps
  // AgentKnowledge from re-parsing AGENT.md while an answer streams. Doors call
  // it from event handlers, after the commit that refreshed the ref.
  const latest = useRef(guarded);
  useEffect(() => {
    latest.current = guarded;
  });
  const openPane = useCallback((next: PaneView) => latest.current(next), []);

  const dialog = (
    // A pane is handed off in one click, where a page has to be navigated away
    // from — so the text an editor is holding gets a question first.
    <ConfirmDialog
      open={pending !== null}
      title="Discard changes?"
      confirmLabel="Discard"
      pendingLabel="Discarding..."
      onConfirm={() => {
        const request = pending;
        setPending(null);
        setPanelDirty(false);
        if (request) apply(request.next);
      }}
      onClose={() => setPending(null)}
    >
      This panel has an editor with unsaved text. Leaving it drops what you
      wrote.
    </ConfirmDialog>
  );

  return { openPane, onPanelDirtyChange: setPanelDirty, dialog };
}
