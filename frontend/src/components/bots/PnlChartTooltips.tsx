// ── The PNL evolution chart's one hover card ──
//
// One card for both panes (READ-248). There used to be two: a Total/Realized/
// Unrealized card in the PnL pane and a Volume/Position card in the activity
// pane, and because the panes share a `syncId` — which propagates the active
// index and the `isTooltipActive` flag across charts — a single hover popped
// both at once, each with its own copy of the same timestamp. Reading one
// instant meant joining two boxes in two places, which is exactly the
// impression the pane captions and the shared legend exist to undo: that these
// are two unrelated charts.
//
// The card is mounted on *both* panes and drawn by the one under the cursor —
// see `visible` below — so it always appears beside the point being read
// rather than proportionally-projected into the other pane.
//
// Every row is named from PNL_SERIES_LABELS and every section from PANE_LABELS
// — the same vocabulary the header legend and the pane captions read (READ-244,
// READ-247) — so a series reads the same word wherever the user meets it.

import type { ReactNode } from "react";

import { formatCurrencyVolume, formatDateTime } from "@/lib/formatters";
import { PANE_LABELS, PNL_SERIES_COLORS, PNL_SERIES_LABELS, type PnlChartPoint } from "@/lib/pnl-chart";
import { getThemeColors } from "@/lib/theme-colors";

/** One entry of a recharts tooltip payload: the series, plus the row it came from. */
interface PayloadEntry {
  dataKey?: string | number;
  value?: number;
  /** The whole data row the point was drawn from — see `readPoint`. */
  payload?: Partial<PnlChartPoint>;
}

export interface PnlEvolutionTooltipProps {
  active?: boolean;
  payload?: PayloadEntry[];
  label?: number;
  /** Currency the values are already expressed in. */
  symbol: string;
  /**
   * How long one volume bar covers — `"5m"`, `"1h"`, `"1d"` … — as chosen by
   * the sampling ladder (PERF-238) and read back off the series.
   *
   * The Volume row used to report a running total, which is self-explanatory.
   * Since READ-245 it reports what was traded in one bucket, and that number is
   * unreadable without its bucket: the same $40k means a busy hour or a dead
   * day. Absent (a series too short to have a spacing) the row just says
   * "Volume" rather than inventing an interval.
   */
  bucket?: string;
  /**
   * Whether the activity pane is actually drawing a position series.
   *
   * The row follows the *series*, not this instant's value. It used to be
   * hidden whenever the number was exactly `0`, which dropped it at a genuine
   * long→short crossover — the one moment a reader is most likely to be
   * hovering (READ-246). A book with no position anywhere on the timeline draws
   * no area and gets no row; a book that is momentarily flat gets the row,
   * reading zero.
   */
  hasPosition?: boolean;
  /**
   * Whether the cursor is in *this* pane.
   *
   * Both panes mount this card so that whichever one is hovered can draw it in
   * its own coordinates. The other pane is active too — that is what `syncId`
   * does — and returning null there is what keeps one hover to one card, while
   * leaving the synced cursor line to span both panes as before.
   */
  visible?: boolean;
}

/**
 * The whole data row behind the hovered instant.
 *
 * This is what lets one card report five series while sitting inside a chart
 * that draws three of them: a recharts payload entry carries not only its own
 * `value` but the entire object it was read from, and both panes are handed the
 * *same* `PnlChartPoint[]`. So the card never has to reach across to the other
 * chart's tooltip state — the row under the cursor already has everything.
 *
 * The per-series `value`s are still folded in as a fallback, for a payload
 * assembled without its source row.
 */
function readPoint(payload: PayloadEntry[]): (key: keyof PnlChartPoint) => number {
  const row = payload[0]?.payload;
  const byKey: Record<string, number> = {};
  for (const entry of payload) {
    if (entry.dataKey != null && typeof entry.value === "number") byKey[String(entry.dataKey)] = entry.value;
  }
  return (key) => {
    const fromRow = row?.[key];
    if (typeof fromRow === "number") return fromRow;
    return byKey[key] ?? 0;
  };
}

function Row({ name, qualifier, color, children }: {
  name: string;
  qualifier?: string;
  color: string;
  children: ReactNode;
}) {
  return (
    <div className="flex justify-between gap-3" data-tooltip-row={name.toLowerCase()}>
      <span style={{ color }}>
        {name}
        {qualifier ? <span className="text-[var(--color-text-muted)]"> / {qualifier}</span> : null}
      </span>
      <span style={{ color }}>{children}</span>
    </div>
  );
}

function Section({ label }: { label: string }) {
  return (
    <div
      data-tooltip-section={label.toLowerCase()}
      className="text-[9px] font-bold uppercase tracking-widest text-[var(--color-text-muted)] mt-1.5 mb-0.5"
    >
      {label}
    </div>
  );
}

/** The single hover card: both panes' series, at one instant, under one timestamp. */
export function PnlEvolutionTooltip({
  active,
  payload,
  label,
  symbol,
  bucket,
  hasPosition = false,
  visible = true,
}: PnlEvolutionTooltipProps) {
  if (!visible || !active || !payload?.length || !label) return null;

  const read = readPoint(payload);
  const realized = read("realized");
  const unrealized = read("unrealized");
  const total = read("total") || realized + unrealized;
  const sign = (v: number) => (v >= 0 ? "+" : "");
  // Every PnL row is coloured from the theme's own up/down pair, the pair the
  // lines and the legend swatches beside them are already drawn with.
  //
  // The two rows here used to read from somewhere else: Realized hard-coded
  // `--color-green` and Total went through `pnlColor`, which returns
  // `--color-green` / `--color-red`. Those are the same values as `--chart-up`
  // / `--chart-down` in every theme shipped today — including the colourblind
  // one — so nothing looks wrong, and nothing would look wrong right up until
  // a theme parted the semantic pair from the chart pair, at which point one
  // card would disagree with the two lines it is describing (READ-244).
  const tc = getThemeColors();

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/95 backdrop-blur-sm px-2.5 py-2 text-[11px] leading-relaxed shadow-lg min-w-[170px]">
      <div className="text-[var(--color-text-muted)] text-[10px]">{formatDateTime(label)}</div>

      <Section label={PANE_LABELS.pnl} />
      <div className="flex justify-between gap-3" data-tooltip-row="total">
        <span className="text-[var(--color-text-muted)]">{PNL_SERIES_LABELS.total}</span>
        <span className="font-semibold" style={{ color: total >= 0 ? tc.up : tc.down }}>
          {sign(total)}{formatCurrencyVolume(total, symbol)}
        </span>
      </div>
      <Row name={PNL_SERIES_LABELS.realized} color={tc.up}>
        {sign(realized)}{formatCurrencyVolume(realized, symbol)}
      </Row>
      <Row name={PNL_SERIES_LABELS.unrealized} color={PNL_SERIES_COLORS.unrealized}>
        {sign(unrealized)}{formatCurrencyVolume(unrealized, symbol)}
      </Row>

      <Section label={PANE_LABELS.activity} />
      {/* The bar's own value — this bucket's trading, not the running total.
          The running total is the header legend's "Traded lifetime" entry, the
          one entry there with no swatch precisely because nothing draws it;
          repeating it here would be the number the pane deliberately stopped
          drawing. */}
      <Row name={PNL_SERIES_LABELS.volumeDelta} qualifier={bucket} color={PNL_SERIES_COLORS.volume}>
        {formatCurrencyVolume(read("volumeDelta"), symbol)}
      </Row>
      {hasPosition && (
        <Row name={PNL_SERIES_LABELS.position} color={PNL_SERIES_COLORS.position}>
          {formatCurrencyVolume(read("position"), symbol)}
        </Row>
      )}
    </div>
  );
}
