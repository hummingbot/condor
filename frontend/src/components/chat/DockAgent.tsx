import { ChevronLeft, SlidersHorizontal } from "lucide-react";

/**
 * The way into what the agent answering this conversation is made of.
 *
 * One line, at the top of the column that already says what the conversation
 * is doing: what the click does — tune the agent — and, muted beside it, who
 * that is. The name alone was here first and read as a label rather than as a
 * door; a reader looking at a card that says "Brigado" under a tab that says
 * "Brigado" has been told nothing they did not already know, and nothing about
 * what clicking it would do.
 *
 * Everything the panel holds — what the agent is for, what it runs on, and
 * everything it knows — stays one click away in {@link AgentPanel} rather than
 * being restated here. A dock card that repeats what is already on screen is
 * not a summary, it is noise with a border around it.
 *
 * It sits with Tasks and Routines rather than inside the panel it opens, so
 * every door in this workspace is on the same side of the window: you click a
 * routine here and its report opens in the pane to the left, and you click the
 * agent here and the agent opens the same way. The chevron points that way —
 * left, at the pane — and turns down once the panel is up, because an arrow
 * that points away from where the thing appears is a promise the layout does
 * not keep.
 */
export function DockAgentCard({
  name,
  open,
  onOpen,
}: {
  /** Who is on the other end — the bound Agent, or Condor. */
  name: string;
  /** Whether the agent's panel is the one in the pane. */
  open: boolean;
  onOpen: () => void;
}) {
  return (
    <div className="shrink-0 border-b border-[var(--color-border)] px-2.5 py-1.5">
      <button
        onClick={onOpen}
        aria-pressed={open}
        title={`Tune ${name} — read and change what this agent is`}
        className={`group flex w-full items-center gap-1.5 rounded px-1 py-1 text-left transition-colors hover:bg-[var(--color-surface-hover)] ${
          open ? "bg-[var(--color-surface-hover)]" : ""
        }`}
      >
        <SlidersHorizontal className="h-3.5 w-3.5 shrink-0 text-[var(--color-accent)]" />
        <span className="shrink-0 text-xs font-semibold text-[var(--color-text)]">
          Tune agent
        </span>
        {/* Who that is, muted and second: the session tab above already names
            them, so this is here to disambiguate a glance, not to announce. */}
        <span className="min-w-0 flex-1 truncate text-[11px] text-[var(--color-text-muted)]">
          {name}
        </span>
        <ChevronLeft
          className={`h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${
            open ? "-rotate-90" : ""
          }`}
        />
      </button>
    </div>
  );
}
