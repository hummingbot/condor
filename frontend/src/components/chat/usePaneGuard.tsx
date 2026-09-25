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
  readPane,
  type PaneView,
} from "@/components/chat/paneUrl";

/** The router's position of a history entry, off the state it writes there. */
function entryIndex(state: unknown): number | null {
  const idx = (state as { idx?: unknown } | null)?.idx;
  return typeof idx === "number" ? idx : null;
}

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
 * rendered once by the host.
 *
 * ## Browser Back and Forward (CORR-432)
 *
 * They are doors too, and they call nobody: the browser moves the URL and the
 * router follows. `BrowserRouter` offers no blocker, so this guards the
 * `popstate` itself, and only while the panel is dirty — a clean panel's Back
 * is the browser's, untouched. The listener is on `window` in the *capture*
 * phase, which the DOM runs before the router's own bubble-phase listener on
 * the same target, so a traversal that would drop the panel (another path, or
 * a `?panel=` that `paneHandoffDropsPanel` says leaves it) is stopped before
 * the router hears of it: the panel never unmounts and the draft stays.
 *
 * The browser has already moved by then, so the move is undone with
 * `history.go(-delta)`, the delta read off the `idx` the router stamps on every
 * entry it writes — an undo rather than a push, so no entry is added and
 * Forward still leads where it did. On Discard the same delta is replayed and
 * let through. An entry with no `idx` falls back to pushing the URL the panel
 * was on back, and Discard is then a plain Back.
 *
 * This is option 2 of the item, over keeping the draft in `sessionStorage`:
 * the panel's editors are four different forms mounted by three states of
 * `AgentKnowledge`, and restoring a draft would mean restoring which editor
 * was open as well as its text. Guarding the traversal is one listener here.
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
  // `go` is a browser traversal that was undone: the delta to replay.
  const [pending, setPending] = useState<
    { next: PaneView } | { go: number } | null
  >(null);

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

  // What the popstate listener compares against: the pane on screen and the
  // entry it was committed on. Refreshed after every commit, so at the moment
  // the browser moves they still describe where it moved *from*.
  const shown = useRef({
    pane,
    panelSlug,
    path: "",
    href: "",
    state: null as unknown,
  });
  useEffect(() => {
    shown.current = {
      pane,
      panelSlug,
      path: window.location.pathname,
      href: window.location.href,
      state: window.history.state,
    };
  });
  // Set by Discard so the replayed traversal is not asked about again.
  const letThrough = useRef(false);

  useEffect(() => {
    if (!panelDirty) return;
    letThrough.current = false;
    const onPop = (e: PopStateEvent) => {
      if (letThrough.current) {
        letThrough.current = false;
        return;
      }
      const from = shown.current;
      const drops =
        window.location.pathname !== from.path ||
        paneHandoffDropsPanel(
          from.pane,
          readPane(new URLSearchParams(window.location.search), {}),
          from.panelSlug,
        );
      if (!drops) return;
      e.stopImmediatePropagation();
      const to = entryIndex(e.state);
      const at = entryIndex(from.state);
      const delta = to !== null && at !== null ? to - at : 0;
      if (delta) {
        window.history.go(-delta);
      } else {
        window.history.pushState(from.state, "", from.href);
      }
      setPending({ go: delta || -1 });
    };
    window.addEventListener("popstate", onPop, true);
    return () => window.removeEventListener("popstate", onPop, true);
  }, [panelDirty]);

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
        if (!request) return;
        if ("go" in request) {
          letThrough.current = true;
          window.history.go(request.go);
        } else {
          apply(request.next);
        }
      }}
      onClose={() => setPending(null)}
    >
      This panel has an editor with unsaved text. Leaving it drops what you
      wrote.
    </ConfirmDialog>
  );

  return { openPane, onPanelDirtyChange: setPanelDirty, dialog };
}
