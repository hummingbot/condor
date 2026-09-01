import { Maximize2, Minimize2, X } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { createPortal } from "react-dom";

import { WORKSPACE_BAR } from "@/components/chat/workspaceBar";
import { useWorkspacePane } from "@/hooks/useWorkspacePane";
import { useEscapeKey } from "@/hooks/useEscapeKey";

/** Remembers the last zen choice, so a reader who wants the whole window keeps it. */
const ZEN_KEY = "condor_sheet_zen";

function readZen(fallback: boolean): boolean {
  const stored = localStorage.getItem(ZEN_KEY);
  return stored === null ? fallback : stored === "1";
}

/**
 * The dock's read view: whatever a dock row points at — a delegation's result,
 * a routine run's report.
 *
 * The dock is 300px wide and deliberately terse; anything worth reading in full
 * opens here instead of squeezing into the column.
 *
 * Three sizes. Beside a conversation (see {@link useWorkspacePane}) it opens as
 * the workspace's right-hand pane: the transcript keeps the left of the window
 * and stays live, so the report and the agent that produced it are on screen at
 * once and you can keep asking about what you are reading. Away from a
 * conversation — an agent's own page — there is nothing to sit beside, so it
 * opens windowed, floating over the page for a result you glance at and
 * dismiss. Zen, from either, takes the entire viewport: no backdrop, no
 * rounding, no inset, for when reading is the whole job.
 */
export function WorkspaceSheet({
  title,
  subtitle,
  header,
  actions,
  onClose,
  bleed = false,
  defaultZen = false,
  fullscreen = true,
  children,
}: {
  title: string;
  subtitle?: string;
  /**
   * The left of the bar, in place of the title and subtitle — for content whose
   * name is better said by a control than by a label. The routine library says
   * which routine is open with the picker that changes it, rather than with
   * text beside a picker that repeats it.
   *
   * A function when it depends on the size the sheet is at, as `actions` is.
   */
  header?: React.ReactNode | ((state: { zen: boolean }) => React.ReactNode);
  /**
   * Sheet-level controls, left of full-screen and Close — the door out to the
   * full page for content that has one.
   *
   * A function when the controls depend on the size the sheet is at: full
   * screen the sheet covers the dock, so anything the reader was steering the
   * content with from there has to be offered here instead.
   */
  actions?: React.ReactNode | ((state: { zen: boolean }) => React.ReactNode);
  onClose: () => void;
  /**
   * Give the body the whole sheet — no padding, no scroll of its own. For
   * content that brings its own page, i.e. a report's iframe.
   */
  bleed?: boolean;
  /**
   * Open at full viewport, for content that wants the room (reports). Ignored
   * beside a conversation, where the pane is already the room — a report that
   * blanked the chat is the thing the pane exists to stop.
   */
  defaultZen?: boolean;
  /**
   * Whether the whole window is on offer.
   *
   * Content that is read — a report, a transcript — earns it. Content that is
   * *steered beside a conversation* does not: the agent panel is a place you
   * change one thing and look back at what the agent just said, and a full
   * screen version of it is a second layout to maintain for a gesture whose
   * only outcome is losing the chat. The pane is the room it needs.
   */
  fullscreen?: boolean;
  children: React.ReactNode;
}) {
  const pane = useWorkspacePane();
  const canSplit = !!pane?.canSplit;
  const [zen, setZen] = useState(() =>
    !fullscreen || canSplit ? false : readZen(defaultZen),
  );
  /**
   * Whether somebody else is already in the pane.
   *
   * There is only one `aside`, so two sheets portalled into it stack — a
   * report drawn over a delegation, with no way to tell which scrollbar
   * belongs to what. The first to arrive finds the pane free and claims it;
   * anyone opened after that finds it taken and renders as the windowed
   * overlay it already is everywhere below `xl`. Nobody has to remember the
   * rule, because it is the shape of the state: the pane names its holder, and
   * this sheet asks whether that is itself.
   */
  const token = useId();
  const taken = !!pane?.holder && pane.holder !== token;
  const split = canSplit && !zen && !taken;

  // Hold the pane open for as long as this sheet is in it, so the outlet takes
  // width exactly while something occupies it.
  const claim = pane?.claim;
  useEffect(() => {
    if (!split || !claim) return;
    return claim(token);
  }, [split, claim, token]);

  // Esc closes, as it does for every overlay here. Not in the pane, though: the
  // chat beside it is live and Esc belongs to whatever has focus there.
  useEscapeKey(!split, onClose);

  // `f` toggles, matching the report browser's own fullscreen key. Ignored while
  // a field has focus — the chat composer is still mounted beside the sheet.
  // The choice only persists for the overlay: in the pane, "make this one full
  // screen" is about the page you are reading now, and remembering it would
  // quietly cover the chat again on the next report.
  useEffect(() => {
    if (!fullscreen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key !== "f" || e.metaKey || e.ctrlKey || e.altKey) return;
      if (
        e.target instanceof HTMLInputElement ||
        e.target instanceof HTMLTextAreaElement ||
        (e.target instanceof HTMLElement && e.target.isContentEditable)
      )
        return;
      setZen((z) => {
        if (!canSplit) localStorage.setItem(ZEN_KEY, z ? "0" : "1");
        return !z;
      });
      e.preventDefault();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [canSplit, fullscreen]);

  const toggleZen = () =>
    setZen((z) => {
      if (!canSplit) localStorage.setItem(ZEN_KEY, z ? "0" : "1");
      return !z;
    });

  const chrome = (
    <>
      {/* One height for every bar in this workspace — the rail's, the
          conversation's, the dock's and this one — so four columns side by side
          read as one strip rather than as four borders at four heights. Only
          the inset varies: a windowed sheet floats and can afford `px-6`. */}
      <div
        className={`${WORKSPACE_BAR} justify-between gap-3 ${
          zen || split ? "px-4" : "px-6"
        }`}
      >
        {header ? (
          <div className="flex min-w-0 flex-1 items-center" aria-label={title}>
            {typeof header === "function" ? header({ zen }) : header}
          </div>
        ) : (
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-[var(--color-text)]">
              {title}
            </h2>
            {subtitle && (
              <p className="truncate text-[11px] text-[var(--color-text-muted)]">
                {subtitle}
              </p>
            )}
          </div>
        )}
        <div className="flex shrink-0 items-center gap-1">
          {typeof actions === "function" ? actions({ zen }) : actions}
          {fullscreen && (
            <button
              onClick={toggleZen}
              className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title={
                zen
                  ? canSplit
                    ? "Back beside the chat (f)"
                    : "Exit full screen (f)"
                  : "Full screen (f)"
              }
            >
              {zen ? (
                <Minimize2 className="h-4 w-4" />
              ) : (
                <Maximize2 className="h-4 w-4" />
              )}
            </button>
          )}
          <button
            onClick={onClose}
            className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title={split ? "Close" : "Close (Esc)"}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>
      {/* Zen and the pane both drop the sheet's max width, which is right for a
          report and wrong for prose — so text keeps its measure by centering
          instead. */}
      <div
        className={
          bleed
            ? "flex min-h-0 flex-1 flex-col overflow-hidden"
            : `min-h-0 flex-1 overflow-auto px-6 py-4 ${
                zen || split ? "mx-auto w-full max-w-5xl" : ""
              }`
        }
      >
        {children}
      </div>
    </>
  );

  if (split) {
    // No host on the very first render of a provider still mounting; the outlet
    // is always mounted, so the next one has one.
    if (!pane?.host) return null;
    return createPortal(
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {chrome}
      </div>,
      pane.host,
    );
  }

  return (
    <div
      className={`fixed inset-0 z-50 flex ${
        zen ? "" : "items-center justify-center p-4"
      }`}
    >
      {!zen && (
        <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      )}
      {/* Prose reads badly past a measure, so text stops at `5xl`. A report was
          laid out for a page of its own and gets the whole window. */}
      <div
        className={
          zen
            ? "relative z-10 flex h-full w-full flex-col bg-[var(--color-bg)]"
            : `relative z-10 flex h-[90vh] w-[95vw] flex-col rounded-xl border border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl ${
                bleed ? "" : "max-w-5xl"
              }`
        }
      >
        {chrome}
      </div>
    </div>
  );
}
