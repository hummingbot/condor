import { ChevronDown, ChevronRight } from "lucide-react";

/**
 * One pane of a dock.
 *
 * Open, it takes a fixed share of the column — `flex-1 basis-0`, so two open
 * panes are half and half no matter what is in them. Sizing from content
 * instead (`flex-auto`) looks tidier on a quiet conversation and is unusable on
 * a busy one: every task that starts or routine that finishes moves the divider,
 * so the row you were reading slides out from under the cursor. A boundary that
 * never moves is worth more than one that is always optimally placed.
 *
 * The body owns the scrollbar, so the header never leaves the viewport whatever
 * the list does.
 *
 * Closed, it is just the header bar — the deliberate way to give the other pane
 * the whole column. Closed also means the body is *unmounted*, which is what
 * lets a pane's queries and socket channels be gated on the disclosure alone
 * (see `AccountDock`): a section nobody opened costs nothing to have.
 *
 * Shared by both docks (FEAT-094): the context dock, whose subject is this
 * conversation, and the account dock, whose subject is the server it trades on.
 */
export function DockSection({
  icon,
  label,
  hint,
  count,
  open,
  onToggle,
  children,
}: {
  icon: React.ReactNode;
  label: string;
  /** What this section is, for the reader who has to tell it from the other. */
  hint: string;
  count?: number;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div
      className={`flex flex-col border-b border-[var(--color-border)] ${
        open ? "min-h-[72px] flex-1 basis-0 overflow-hidden" : "shrink-0"
      }`}
    >
      <button
        onClick={onToggle}
        title={hint}
        className="flex w-full shrink-0 items-center gap-1.5 px-3 py-1.5 text-left text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        {open ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        {icon}
        <span className="min-w-0 flex-1 truncate">{label}</span>
        {count !== undefined && (
          <span className="shrink-0 text-emerald-400">{count}</span>
        )}
      </button>
      {open && (
        <div className="min-h-0 flex-1 overflow-y-auto pb-1">{children}</div>
      )}
    </div>
  );
}
