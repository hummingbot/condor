import { GROUPING_PRESETS, formatGrouping, type GroupAxis } from "@/lib/perf-grouping";

/**
 * Which question the scope tree is answering (FEAT-107).
 *
 * The tree used to have exactly one nesting — owner, then bot — and *"how is
 * SOL-USDC doing across the whole fleet"* was a question the page could narrow
 * to but never *read*. Narrowing to SOL-USDC and reading eleven owner rows is
 * not the same answer as reading one SOL-USDC row, and the totals a reader
 * wants are the ones a grouping produces.
 *
 * A strip and not a bubble row, for the reason the granularity strip beside it
 * is one: the four are a single answer, not four independent ticks. It sits
 * with the population toggle rather than among the filters because it is the
 * same kind of question those two ask — *what is being compared* — as against
 * *which of it to keep*.
 *
 * The buttons compare against the grouping the reader **asked for**, not the
 * one the tree was built with: a level that collapsed because everything in
 * scope agreed, or an owner level a rooted host forced back on, must not move
 * the highlight off the button they pressed.
 */
export function GroupByPicker({
  grouping,
  onChange,
}: {
  grouping: readonly GroupAxis[];
  onChange: (axes: readonly GroupAxis[]) => void;
}) {
  const current = formatGrouping(grouping);
  return (
    <div
      role="group"
      aria-label="Group by"
      className="flex gap-0.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] p-0.5"
    >
      {GROUPING_PRESETS.map((preset) => {
        const active = formatGrouping(preset.axes) === current;
        return (
          <button
            key={preset.key}
            type="button"
            onClick={() => onChange(preset.axes)}
            aria-pressed={active}
            title={preset.hint}
            className={`flex-1 rounded px-1 py-1 text-[10px] font-medium transition-colors ${
              active
                ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            }`}
          >
            {preset.label}
          </button>
        );
      })}
    </div>
  );
}
