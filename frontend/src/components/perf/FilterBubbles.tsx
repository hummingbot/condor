import { Search, X } from "lucide-react";
import { useMemo, useState } from "react";

/**
 * One thing a bubble group can filter on, and how much of the population it
 * accounts for.
 *
 * The count is what makes a wall of bubbles readable: a fleet's terminated side
 * offers a hundred bot names, and the reader is looking for the four that
 * actually traded. It is measured over the population *before* any filter is
 * applied, so a bubble never renumbers itself as a consequence of being ticked.
 */
export interface BubbleOption {
  value: string;
  label: string;
  count: number;
}

/**
 * A row of togglable bubbles with `all` / `none` beside its title.
 *
 * This replaces a pair of multi-select dropdowns, and the reason is what a
 * dropdown costs on this particular screen: picking three of fourteen
 * controllers meant open, tick, tick, tick, close, and the picked set was then
 * invisible — the trigger said "3 selected". Bubbles put the whole set on the
 * page, so which are on and which are off is read rather than remembered, and
 * "these four bots" is four clicks with no menu in the way.
 *
 * **Empty means everything.** A group with nothing ticked filters nothing out,
 * which is what makes `none` the way back to the unfiltered population. `all`
 * lights every bubble instead — the same set, but as a starting point for
 * un-ticking the two you did not want, which is the actual way a reader narrows
 * a fleet down to a handful.
 */
export function BubbleGroup({
  title,
  hint,
  options,
  selected,
  onChange,
  /** How many bubbles are drawn before the rest hide behind a `+N` chip. */
  previewCount = 8,
  /** Above this many options the expanded group gets a search box of its own. */
  searchAbove = 12,
}: {
  title: string;
  hint?: string;
  options: BubbleOption[];
  selected: string[];
  onChange: (next: string[]) => void;
  previewCount?: number;
  searchAbove?: number;
}) {
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState("");

  const picked = useMemo(() => new Set(selected), [selected]);

  // A ticked bubble is always drawn, wherever it sorts and whatever the search
  // says: a filter you cannot see is a filter you cannot undo.
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    const matching = q
      ? options.filter((o) => picked.has(o.value) || o.label.toLowerCase().includes(q))
      : options;
    if (expanded || matching.length <= previewCount) return matching;
    const head = matching.slice(0, previewCount);
    const seen = new Set(head.map((o) => o.value));
    return [...head, ...matching.filter((o) => picked.has(o.value) && !seen.has(o.value))];
  }, [options, picked, query, expanded, previewCount]);

  const hiddenCount = options.length - shown.length;

  if (options.length === 0) return null;

  const toggle = (value: string) =>
    onChange(picked.has(value) ? selected.filter((v) => v !== value) : [...selected, value]);

  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center gap-1.5">
        <span
          className="text-[9px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]/70"
          title={hint}
        >
          {title}
        </span>
        {selected.length > 0 && (
          <span className="rounded-full bg-[var(--color-primary)]/15 px-1.5 text-[9px] font-bold tabular-nums text-[var(--color-primary)]">
            {selected.length}
          </span>
        )}
        <span className="ml-auto flex items-center gap-1 text-[9px] text-[var(--color-text-muted)]">
          <button
            type="button"
            onClick={() => onChange(options.map((o) => o.value))}
            className="rounded px-1 py-0.5 hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            title={`Select every ${title.toLowerCase()} — then un-tick the ones you do not want`}
          >
            all
          </button>
          <button
            type="button"
            onClick={() => onChange([])}
            disabled={selected.length === 0}
            className="rounded px-1 py-0.5 hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-30 disabled:hover:bg-transparent"
            title="Clear this filter — everything comes back"
          >
            none
          </button>
        </span>
      </div>

      {expanded && options.length > searchAbove && (
        <div className="relative">
          <Search className="pointer-events-none absolute left-1.5 top-1/2 h-2.5 w-2.5 -translate-y-1/2 text-[var(--color-text-muted)]" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={`Search ${title.toLowerCase()}…`}
            className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] py-0.5 pl-5 pr-5 text-[10px] focus:border-[var(--color-primary)] focus:outline-none"
          />
          {query && (
            <button
              type="button"
              onClick={() => setQuery("")}
              className="absolute right-1 top-1/2 -translate-y-1/2 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              title="Clear search"
            >
              <X className="h-2.5 w-2.5" />
            </button>
          )}
        </div>
      )}

      <div
        className={`flex flex-wrap gap-1 ${
          expanded ? "max-h-40 overflow-y-auto scrollbar-thin pr-0.5" : ""
        }`}
      >
        {shown.map((option) => {
          const on = picked.has(option.value);
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => toggle(option.value)}
              aria-pressed={on}
              title={`${option.value} — ${option.count} in scope`}
              className={`flex max-w-full items-center gap-1 rounded-full border px-2 py-0.5 text-[10px] transition-colors ${
                on
                  ? "border-[var(--color-primary)] bg-[var(--color-primary)]/15 font-semibold text-[var(--color-primary)]"
                  : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-text)]"
              }`}
            >
              <span className="truncate">{option.label}</span>
              <span className={`shrink-0 tabular-nums ${on ? "opacity-70" : "opacity-50"}`}>
                {option.count}
              </span>
            </button>
          );
        })}
        {(hiddenCount > 0 || expanded) && options.length > previewCount && (
          <button
            type="button"
            onClick={() => {
              setExpanded(!expanded);
              if (expanded) setQuery("");
            }}
            className="rounded-full border border-dashed border-[var(--color-border)] px-2 py-0.5 text-[10px] text-[var(--color-text-muted)] hover:border-[var(--color-primary)]/50 hover:text-[var(--color-text)]"
          >
            {expanded ? "less" : `+${hiddenCount}`}
          </button>
        )}
      </div>
    </div>
  );
}
