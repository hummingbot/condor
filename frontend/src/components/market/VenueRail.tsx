import { useMemo } from "react";
import { Eye, KeyRound } from "lucide-react";

import { formatConnectorName } from "@/lib/formatters";

/**
 * The venue axis of the market browser.
 *
 * Every venue listed here has an order book — Gateway networks live on the DEX
 * page, where the pool rather than the pair is the unit of navigation.
 *
 * This is the market browser's *scope* control, not a commit: moving down the
 * rail re-lists the table and leaves the chart, the executors and the config
 * panel exactly where they are. Only picking a row moves the trade surface, and
 * it carries this venue with it. That is the whole reason the rail lives in
 * here rather than in the top bar: a venue selector that commits on its own
 * drops you onto a default pair on the new venue before you have chosen one.
 *
 * The one split worth heading is credentials (ARCH-272): most of this list is
 * chartable but not tradable, and that difference decides whether the Execute
 * panel opens or goes view-only. It is carried by the rows themselves, not only
 * by a word above them — the credentialed venues read at full strength and the
 * rest are dimmed behind a hard rule — because the header scrolls away long
 * before the list does. A group with no members is not headed at all: on a
 * server where every venue is credentialed the rail looks like one plain list.
 */
interface VenueRailProps {
  connectors: string[];
  /**
   * The subset of `connectors` the account holds keys on; the rest are listed
   * under `View only`. Omitted means "no idea yet" — before the venues query
   * resolves nothing is known to be view-only, and a flat list is the honest
   * rendering as well as the one that does not flicker.
   */
  credentialed?: ReadonlySet<string>;
  /** The venue the table is scoped to. */
  value: string;
  /** The venue the trade surface is on, marked so the rail says where you came from. */
  current: string;
  onChange: (v: string) => void;
}

export function VenueRail({
  connectors,
  credentialed,
  value,
  current,
  onChange,
}: VenueRailProps) {
  // `connectors` already arrives credentialed-first (see `orderBookVenues`), so
  // this is a partition that preserves the caller's order, never a re-sort.
  const [mine, viewOnly] = useMemo(() => {
    if (!credentialed) return [connectors, [] as string[]];
    return [
      connectors.filter((c) => credentialed.has(c)),
      connectors.filter((c) => !credentialed.has(c)),
    ];
  }, [connectors, credentialed]);

  // Headers only earn their room when there is something to tell apart.
  const grouped = mine.length > 0 && viewOnly.length > 0;

  return (
    <div className="flex w-44 shrink-0 flex-col border-r border-[var(--color-border)]">
      {/* An empty band, not a label: the rail names itself by what is in it.
          It is here to hold the search field opposite on the same line as the
          rail's first venue. */}
      <div
        aria-hidden
        className="h-10 shrink-0 border-b border-[var(--color-border)] bg-[var(--color-surface)]"
      />
      <div
        role="listbox"
        aria-label="Exchange"
        className="min-h-0 flex-1 overflow-y-auto pb-2"
      >
        <VenueGroup
          label="Your accounts"
          heading={grouped}
          icon={KeyRound}
          tone="mine"
          names={mine}
          value={value}
          current={current}
          onSelect={onChange}
        />
        <VenueGroup
          label="View only"
          heading={grouped}
          icon={Eye}
          tone="viewOnly"
          names={viewOnly}
          value={value}
          current={current}
          onSelect={onChange}
        />
      </div>
    </div>
  );
}

/**
 * One headed run of venues. `role="group"` inside the listbox is what keeps the
 * header out of the option sequence for a screen reader while still naming the
 * run it labels; an empty group renders nothing at all rather than a bare header.
 *
 * The header stays the group's only direct child besides its rows, so the two
 * tones below are carried by colour on the rows and by a rule on the group —
 * nothing that would read as a second heading.
 */
function VenueGroup({
  label,
  heading,
  icon: Icon,
  tone,
  names,
  value,
  current,
  onSelect,
}: {
  label: string;
  heading: boolean;
  icon: typeof KeyRound;
  tone: "mine" | "viewOnly";
  names: string[];
  value: string;
  current: string;
  onSelect: (v: string) => void;
}) {
  if (!names.length) return null;
  const mine = tone === "mine";
  return (
    <div
      role="group"
      aria-label={label}
      // The rule is the split: above it you can trade, below it you can only
      // look. Only drawn when there is a group above to be ruled off from.
      className={
        !mine && heading ? "mt-1 border-t border-[var(--color-border)] pt-1" : undefined
      }
    >
      {heading && (
        <div
          className={`flex items-center gap-1.5 px-3 pb-1 pt-2 text-[10px] font-semibold uppercase tracking-wide ${
            mine ? "text-[var(--color-primary)]" : "text-[var(--color-text-muted)]"
          }`}
        >
          <Icon className="h-3 w-3 shrink-0" aria-hidden />
          {label}
        </div>
      )}
      {names.map((c) => (
        <button
          key={c}
          role="option"
          aria-selected={c === value}
          // Two different truths: `selected` is what the table is showing,
          // `current` is what the chart behind the overlay is on. They start
          // equal and diverge the moment you browse elsewhere.
          aria-current={c === current ? "true" : undefined}
          onClick={() => onSelect(c)}
          // Gold is reserved for a venue you can actually execute on, so a
          // selected view-only venue is marked by its band alone. Selection is
          // still unmistakable; it just never borrows the tradable colour.
          className={`flex w-full items-center gap-1.5 px-3 py-1.5 text-left text-xs transition-colors ${
            c === value
              ? mine
                ? "bg-[var(--color-primary)]/10 font-medium text-[var(--color-primary)]"
                : "bg-[var(--color-surface-hover)] font-medium text-[var(--color-text)]"
              : mine
                ? "font-medium text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          }`}
        >
          <span className="truncate">{formatConnectorName(c)}</span>
          {c === current && (
            <span
              title="The venue on the chart"
              aria-hidden
              className="ml-auto h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--color-text-muted)]"
            />
          )}
        </button>
      ))}
    </div>
  );
}
