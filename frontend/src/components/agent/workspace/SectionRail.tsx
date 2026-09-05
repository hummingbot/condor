import { Activity, ChevronRight } from "lucide-react";

import { SECTION_META } from "@/components/agent/workspace/sectionMeta";
import { SECTIONS, type SectionId } from "@/components/agent/workspace/sections";

/**
 * Everything on this screen, listed down the side of it (FEAT-120).
 *
 * The screen underneath is unchanged: one run read top to bottom, with five
 * disclosures under it that mount nothing until they are opened. What it did
 * not have was an **index** — the five bands sit below the answer stack, which
 * is a chart and a decision and a ledger tall, so the reader had to scroll past
 * the answer to find out that a Playbook or a Money band existed at all, and a
 * `?open=playbook` link landed on a screen whose opened band was off the
 * bottom edge.
 *
 * So the rail says what is on the screen and where the reader is in it, and
 * every entry carries whatever this screen *already knows* about that section —
 * a run count, what the run deployed — so the index is also a summary.
 *
 * Two things it deliberately does not do:
 *
 * **It does not swap the body.** That was the spine FEAT-119 spent, and the
 * reason it went is that the money and the deployments could not then be read
 * at the same time. A rail entry is the same button as the band's own header:
 * it opens the band in place, and opening scrolls it under the reader's eye.
 * One action, two places to reach it, and `?open=` still names a *set*.
 *
 * **It does not fetch.** Every fact on it is a by-product of a query the screen
 * had already made, which is why Money and Fleet carry no number: the fold is
 * the whole fleet, and buying it on every page load to print one figure in a
 * rail is exactly the cost the disclosures exist to avoid. A number here that
 * came from somewhere cheaper would also be a *different* quantity from the one
 * the band headlines, which is the confusion FEAT-109 was spent settling.
 */
export function SectionRail({
  open,
  facts,
  nowFact,
  nowAlert = false,
  onSelect,
  onTop,
}: {
  /** Which sections are open — the same set the bands read. */
  open: readonly SectionId[];
  /** A short, already-known fact per section. Absent is normal, not missing. */
  facts?: Partial<Record<SectionId, string | null>>;
  /** The answer stack's own line: what wants attention, else the last tick. */
  nowFact?: string | null;
  /** Colour `nowFact` as a warning — something on the run needs a person. */
  nowAlert?: boolean;
  onSelect: (id: SectionId) => void;
  /** Back to the top of the screen, which is what "Now" means here. */
  onTop: () => void;
}) {
  return (
    <nav
      aria-label="Sections"
      data-section-rail
      /* A media query and not a `dense` prop, unlike `AgentKnowledge`: this
         screen's only host is `/agents/:slug`, a full page, so the viewport's
         width really is this component's width. Below `lg` the rail would eat a
         third of the column, and the bands are still the whole navigation. */
      className="hidden w-48 shrink-0 flex-col gap-0.5 overflow-y-auto border-r border-[var(--color-border)] p-2 lg:flex"
    >
      <button
        type="button"
        data-rail="now"
        onClick={onTop}
        title="The run itself — its vitals, last decision and what it deployed"
        className="flex flex-col items-stretch gap-0.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <span className="flex items-center gap-2">
          <Activity className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
          <span className="flex-1 truncate text-[11px] font-bold uppercase tracking-widest">
            Now
          </span>
        </span>
        {nowFact && (
          <span
            className={`pl-[22px] font-mono text-[10px] tabular-nums ${
              nowAlert ? "text-amber-400" : "text-[var(--color-text-muted)]"
            }`}
          >
            {nowFact}
          </span>
        )}
      </button>

      <div className="my-1 border-t border-[var(--color-border)]" />

      {SECTIONS.map((id) => {
        const { label, hint, Icon } = SECTION_META[id];
        const isOpen = open.includes(id);
        const fact = facts?.[id];
        return (
          <button
            key={id}
            type="button"
            data-rail={id}
            aria-expanded={isOpen}
            aria-controls={`section-${id}`}
            onClick={() => onSelect(id)}
            title={hint}
            className={`flex flex-col items-stretch gap-0.5 rounded-md px-2 py-1.5 text-left transition-colors hover:bg-[var(--color-surface-hover)] ${
              isOpen ? "bg-[var(--color-surface)]" : ""
            }`}
          >
            {/* The fact goes under the label rather than beside it: a rail this
                narrow made "Playbook · 16 settings" one line that truncated the
                *name*, which is the half the reader is scanning for. */}
            <span className="flex items-center gap-2">
              <Icon
                className={`h-3.5 w-3.5 shrink-0 ${
                  isOpen
                    ? "text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)]"
                }`}
              />
              <span
                className={`flex-1 truncate text-[11px] font-bold uppercase tracking-widest ${
                  isOpen ? "" : "text-[var(--color-text-muted)]"
                }`}
              >
                {label}
              </span>
              <ChevronRight
                className={`h-3 w-3 shrink-0 text-[var(--color-text-muted)] transition-transform ${
                  isOpen ? "rotate-90" : ""
                }`}
              />
            </span>
            {fact && (
              <span className="truncate pl-[22px] font-mono text-[10px] tabular-nums text-[var(--color-text-muted)]">
                {fact}
              </span>
            )}
          </button>
        );
      })}
    </nav>
  );
}
