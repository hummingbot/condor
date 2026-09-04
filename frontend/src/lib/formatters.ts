// ── Centralized Formatters ──

/** What a tool call is called when it turns out not to be called anything. */
const UNNAMED_TOOL = "tool";

/** Control characters — a title that carries one would break the single-line row.
 *  The control range is the whole point here, so `no-control-regex` is off by
 *  intent rather than by accident. */
// eslint-disable-next-line no-control-regex
const CONTROL_CHARS = /[\u0000-\u001f\u007f-\u009f]/g;

/** Strings that are the *absence* of a name wearing a name's clothes. Python's
 *  `None` and JS's `undefined`/`null` all reach the wire stringified by some
 *  adapter or other; `[object Object]` is what `String()` makes of a payload
 *  that was never a name at all. */
const NOT_A_NAME = new Set(["undefined", "null", "none", "nan", "[object object]"]);

/**
 * Humanize a tool-call name: strip the `mcp__<server>__` prefix and underscores.
 * e.g. "mcp__condor__manage_routines" → "manage routines", "ToolSearch" → "ToolSearch".
 *
 * **Total by contract**: every input returns a string and nothing throws. That
 * is not defensiveness for its own sake — the argument comes off an untyped
 * wire. The live `tool_call` frame used to build its `ToolCall` with an
 * unchecked `data.title as string` cast, so a frame missing the field handed
 * this function `undefined` and `title.includes` took down the bubble that was
 * *currently streaming*, while the same turn re-read from history (which
 * coerces) rendered fine. A renderer that only breaks live and heals on reload
 * is the worst shape that failure can have, so the guarantee lives here, at the
 * one place all four call sites share, rather than at each of them.
 *
 * The sentinel check is the second half: a title that is present but meaningless
 * — the literal string `"undefined"`, quotes included, of which a real
 * transcript holds five — renders as `tool` instead of shouting a word that
 * tells the reader nothing. CORR-327 normalises those at the ACP seam before
 * they are persisted; this keeps the transcripts already on disk readable.
 */
export function formatToolName(title?: unknown): string {
  // A name is a string. A number, a boolean, an object or a missing field is
  // not a mangled name, it is the absence of one — say so rather than render
  // "42" or "[object Object]" as if it were what ran.
  if (typeof title !== "string") return UNNAMED_TOOL;

  // Control characters become spaces (not nothing) so "read\nfile" degrades to
  // two words rather than one invented one, and the row stays on one line.
  let raw = title.replace(CONTROL_CHARS, " ").trim();

  // One layer of wrapping quotes comes off, so the sentinel check below sees a
  // double-stringified value (`"\"undefined\""`) for what it is.
  const quoted = raw.match(/^"(.*)"$/s) ?? raw.match(/^'(.*)'$/s);
  if (quoted) raw = quoted[1].trim();

  if (!raw || NOT_A_NAME.has(raw.toLowerCase())) return UNNAMED_TOOL;

  // `mcp__<server>__<tool>` → the tool. Empty segments are skipped so a
  // trailing or tripled separator cannot yield an empty name.
  const segments = raw.split("__").filter((s) => s.trim() !== "");
  const name = segments.length ? segments[segments.length - 1] : raw;

  return name.replace(/_/g, " ").replace(/\s+/g, " ").trim() || UNNAMED_TOOL;
}

/**
 * A call that never ran because something said no.
 *
 * The permission gate emits `blocked` and then `continue`s — no further update
 * for that id ever arrives (condor/acp/pydantic_ai_client.py). Read as
 * "in flight", that refusal is the worst possible lie: live, the settle pass
 * rewrote it to `completed` when the prompt ended, so a call the user *refused*
 * finished the turn wearing a green check; reloaded, it span forever on a turn
 * that was over. Both halves come from the same missing word.
 *
 * The other spellings are here because the refusal vocabulary is not one
 * bridge's: `condor/agents/actions.py` already reads back exactly this set
 * (`_REFUSED_STATUSES`), and the confirmation layer answers `cancelled` while
 * the admin surface says `rejected`. One list, so a new adapter's word is added
 * in one place rather than discovered as a spinner.
 */
const REFUSED_STATUSES = new Set([
  "blocked",
  "denied",
  "rejected",
  "cancelled",
  "canceled",
  "error",
]);

/** Classify an ACP tool-call status into the three states the UI renders.
 *
 *  The wire vocabulary is `pending | in_progress | completed | failed` — it
 *  comes straight off the ACP `tool_call`/`tool_call_update` stream (see the
 *  `ToolCallEvent` dataclass in condor/acp/client.py) and is what the journal
 *  writes as `### N. name (status)` for parse-agent.ts to read back. Anything
 *  that is neither terminally done nor terminally refused is still in flight —
 *  and "still in flight" is a claim the rest of the UI acts on, so a status
 *  that means the call is over must never fall through to it. */
export function toolCallState(status: string): "ok" | "error" | "pending" {
  if (status === "completed") return "ok";
  if (status === "failed" || REFUSED_STATUSES.has(status)) return "error";
  return "pending";
}

/** Escape a string for safe interpolation into innerHTML. */
export function escapeHtml(val: string): string {
  return val
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

export function formatCurrency(val: number, symbol = "$") {
  if (Math.abs(val) >= 1_000_000) return symbol + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return symbol + (val / 1_000).toFixed(1) + "K";
  if (symbol === "$") {
    return val.toLocaleString("en-US", {
      style: "currency",
      currency: "USD",
      minimumFractionDigits: 2,
    });
  }
  // Adaptive precision for small values (e.g. BTC)
  if (Math.abs(val) < 0.01 && val !== 0) return symbol + val.toPrecision(4);
  return symbol + val.toFixed(2);
}

/**
 * Compact USD for chart tooltips / stat strips: `>=1M → "$N.NNM"`, `>=10K → "$N.NK"`,
 * else plain `"$" + toFixed(2)` (no locale grouping). Kept distinct from `formatCurrency`,
 * which uses `Intl` grouping/sign placement below 10K — switching would change rendered values.
 */
export function formatCompactUsd(val: number): string {
  if (Math.abs(val) >= 1_000_000) return "$" + (val / 1_000_000).toFixed(2) + "M";
  if (Math.abs(val) >= 10_000) return "$" + (val / 1_000).toFixed(1) + "K";
  return "$" + val.toFixed(2);
}

export function formatCurrencyVolume(val: number, symbol = "$") {
  if (Math.abs(val) >= 1_000_000) return symbol + (val / 1_000_000).toFixed(1) + "M";
  if (Math.abs(val) >= 1_000) return symbol + (val / 1_000).toFixed(1) + "K";
  return symbol + val.toFixed(Math.abs(val) < 100 ? 2 : 0);
}

export function formatCurrencyPnl(val: number, symbol = "$") {
  const prefix = val >= 0 ? "+" : "";
  return prefix + formatCurrency(val, symbol);
}

// Single source of truth for the PnL sign encoding (>= 0 green, < 0 red), in the
// two forms the codebase consumes. They are NOT interchangeable — both are
// `string`, so handing one to the other's consumer renders an unstyled or
// invisible number with no TS or build error. Same split as `MODE_STYLES` in
// components/agent/modeStyles.ts: one source of truth, one variant per sink.

/** CSS color value — for `style={{ color: pnlColor(x) }}` and chart/theme options. */
export function pnlColor(val: number) {
  return val >= 0 ? "var(--color-green)" : "var(--color-red)";
}

/** Tailwind text-color class — for `className={pnlTextClass(x)}`. */
export function pnlTextClass(val: number) {
  return val >= 0 ? "text-[var(--color-green)]" : "text-[var(--color-red)]";
}

// Backward-compatible aliases (default to $)
export function formatUsd(val: number) {
  return formatCurrency(val);
}

export function formatVolume(val: number) {
  return formatCurrencyVolume(val);
}

/**
 * Compact volume with a billions tier — 24h exchange volumes routinely exceed $1B,
 * where `formatCurrencyVolume` would render an unreadable "2400.0M".
 */
export function formatCompactVolume(val: number, symbol = "$"): string {
  const abs = Math.abs(val);
  if (abs >= 1_000_000_000) return symbol + (val / 1_000_000_000).toFixed(2) + "B";
  if (abs >= 1_000_000) return symbol + (val / 1_000_000).toFixed(1) + "M";
  if (abs >= 1_000) return symbol + (val / 1_000).toFixed(1) + "K";
  return symbol + val.toFixed(0);
}

/**
 * Y-axis tick labels for the PnL evolution charts.
 *
 * Same B/M/K ladder as `formatCompactVolume`, so a tick never renders the same
 * number differently from the header strip and the tooltip drawn beside it
 * (both `formatCurrencyVolume`): a $2.4M cumulative volume used to print
 * "$2400.0K" on the axis and "$2.4M" everywhere else.
 *
 * The axis needs its own rule only *below* $1K, because it is drawn at
 * fontSize 10 inside a 52px gutter and its ticks are chosen by recharts, not by
 * the data: `"pnl"` keeps 2 decimals under $10 (a young controller's PnL axis
 * spans cents), `"volume"` never shows decimals (volume ticks are whole
 * dollars, and a decimal there only costs width).
 */
export function formatAxisCurrency(val: number, symbol = "$", kind: "pnl" | "volume" = "volume"): string {
  const abs = Math.abs(val);
  if (abs >= 1_000_000_000) return symbol + (val / 1_000_000_000).toFixed(2) + "B";
  if (abs >= 1_000_000) return symbol + (val / 1_000_000).toFixed(1) + "M";
  if (abs >= 1_000) return symbol + (val / 1_000).toFixed(1) + "K";
  return symbol + val.toFixed(kind === "pnl" && abs < 10 ? 2 : 0);
}

export function formatPnl(val: number) {
  return formatCurrencyPnl(val);
}

/** Normalize a timestamp to seconds (ms timestamps > 1e12 are divided by 1000). */
export function tsToSeconds(ts: number): number {
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

/** Normalize a timestamp (seconds or ms, number or ISO string) to epoch ms. */
export function toMs(ts: string | number): number {
  if (typeof ts === "number") return ts > 1e12 ? ts : ts * 1000;
  const parsed = Date.parse(ts);
  return isNaN(parsed) ? 0 : parsed;
}

/** Format an epoch-ms timestamp as a 24h `HH:MM` time label. */
export function formatTime(ms: number): string {
  return new Date(ms).toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" });
}

/** Format an epoch-ms timestamp as a `Mon D HH:MM` (24h) date-time label. */
export function formatDateTime(ms: number): string {
  const d = new Date(ms);
  return `${d.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit" })}`;
}

/**
 * X-axis tick labels for the PnL evolution charts, chosen from the span the
 * axis is actually showing.
 *
 * The axis used to be hardcoded to `formatTime`, i.e. `HH:MM` and nothing else
 * (READ-250). That was only ever right by accident: the sampling interval is
 * derived from the bot's real runtime (PERF-238) and ranges `5m` to `1d`, so a
 * single chart's visible span runs from a couple of hours to well over a year.
 * Past a day, `08:00 / 12:00 / 16:00` simply repeats across every day in the
 * window with nothing to say which day a drawdown happened on.
 *
 * The ladder is picked on one rule: **the shortest label whose parts can still
 * differ across the span, and no more**. That cuts both ways, and the second
 * half is the reason this is not just "prepend the date":
 *
 *  - under a day, the date is the *same on every tick*, so printing it is pure
 *    noise in a gutter that is already tight — `HH:MM`;
 *  - a day to a week, both halves move and both are needed — `Mon D HH:MM`,
 *    exactly the tooltip's `formatDateTime`, so a tick and the tooltip that
 *    opens over it read alike;
 *  - a week to a year, recharts' ~5 ticks are more than a day apart, so the
 *    time is decoration on a label that is already unique — `Mon D`;
 *  - a year and beyond, the day is that decoration and the year is what is
 *    ambiguous (a month/day pair repeats only after 365 days, which is exactly
 *    where this tier begins) — `Mon 'YY`.
 *
 * `spanMs` is `last.time - first.time` of the series being drawn; a
 * zero/negative/NaN span (a single point, an empty chart) falls back to `HH:MM`.
 */
export function formatAxisTime(ms: number, spanMs: number): string {
  const DAY = 86_400_000;
  if (!(spanMs >= DAY)) return formatTime(ms);
  if (spanMs < 7 * DAY) return formatDateTime(ms);
  const d = new Date(ms);
  if (spanMs < 365 * DAY) return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
  return `${d.toLocaleDateString("en-US", { month: "short" })} '${String(d.getFullYear() % 100).padStart(2, "0")}`;
}

/**
 * A runtime measured in hours, shown in the unit it is legible in.
 *
 * Hours is the right unit for the tile — it is the divisor of every per-hour
 * pace beside it, so the reader can check a pace against the total without
 * converting anything — but only once there is an hour to speak of. A scope six
 * minutes old reported `0.1h`, which reads as "about nothing" and hides the very
 * thing it is being asked: a pace divided by a tenth of an hour is an
 * extrapolation from six minutes, and the reader has to know that to discount
 * it. Under the hour it is minutes, which is a figure with real digits in it.
 *
 * Below a minute it says `<1m` rather than rounding to `0m`, for the same
 * reason: a runtime that rounds to zero is indistinguishable from no runtime at
 * all, which is the one thing it is not.
 */
export function formatRuntimeHours(hours: number): string {
  if (!Number.isFinite(hours) || hours <= 0) return "\u2014";
  if (hours >= 1) return `${hours.toFixed(1)}h`;
  const mins = Math.round(hours * 60);
  return mins >= 1 ? `${mins}m` : "<1m";
}

export function formatAge(timestamp: number): string {
  if (!timestamp) return "\u2014";
  try {
    const now = Date.now();
    const diffMs = now - timestamp * 1000;
    if (diffMs < 0) return "\u2014";
    const days = Math.floor(diffMs / 86400000);
    const hours = Math.floor((diffMs % 86400000) / 3600000);
    if (days > 0) return `${days}d ${hours}h`;
    const mins = Math.floor((diffMs % 3600000) / 60000);
    if (hours > 0) return `${hours}h ${mins}m`;
    if (mins > 0) return `${mins}m`;
    return "<1m";
  } catch {
    return "\u2014";
  }
}

/**
 * Format a timestamp as a relative "Ns/m/h/d ago" label.
 * Accepts epoch-seconds (number), or a Date/ISO string. Numeric ms timestamps
 * (> 1e12) are normalized to seconds. When the value is null/undefined/empty,
 * returns `fallback` (default "" — pass "never" to match instance-style labels).
 */
export function formatRelativeTime(
  value: number | string | Date | null | undefined,
  fallback = "",
): string {
  if (value == null || value === "") return fallback;
  let seconds: number;
  if (typeof value === "number") {
    seconds = tsToSeconds(value);
  } else {
    const ms = value instanceof Date ? value.getTime() : new Date(value).getTime();
    if (Number.isNaN(ms)) return fallback;
    seconds = ms / 1000;
  }
  const diff = Date.now() / 1000 - seconds;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

/**
 * Significant-digits price formatter shared by the trade pane, the chart
 * tooltip, and the LP position bar — the same entry price must render as the
 * identical string in all three. Prices span memecoin to majors, hence
 * significant digits below 1 rather than fixed decimals. Em-dash for a
 * missing/zero price (an executor with no entry yet, an open LP bound).
 * Distinct from `formatPrice` below, which locale-groups and uses 4
 * significant digits — its call sites keep their rendering.
 */
export function formatPriceSig(val: number | null | undefined): string {
  if (val == null || !Number.isFinite(val) || val === 0) return "—";
  if (Math.abs(val) >= 1000) return val.toFixed(2);
  if (Math.abs(val) >= 1) return val.toFixed(4);
  return val.toPrecision(6);
}

/**
 * Round a price picked off the chart to something a config field can hold.
 *
 * A click reads its price from the pixel under the pointer, so it arrives with
 * every digit a float has (`105234.87313432834`) — the field would show all of
 * them and the payload would carry them. The venue's own tick precision is the
 * right rounding when the caller knows it; without it, six significant digits,
 * the same round the grid's Auto-fill already applies, which keeps sub-cent
 * memecoin prices intact where a fixed number of decimals would flatten them.
 */
export function roundToPricePrecision(price: number, pricePrecision?: number | null): number {
  if (!Number.isFinite(price)) return price;
  if (pricePrecision != null) {
    const factor = 10 ** pricePrecision;
    return Math.round(price * factor) / factor;
  }
  return parseFloat(price.toPrecision(6));
}

export function formatPrice(val: number): string {
  if (!val) return "\u2014";
  if (val >= 1000) return val.toLocaleString("en-US", { maximumFractionDigits: 2 });
  if (val >= 1) return val.toFixed(4);
  return val.toPrecision(4);
}

export function formatPct(val: number): string {
  if (!val) return "\u2014";
  return (val >= 0 ? "+" : "") + (val * 100).toFixed(2) + "%";
}

export function isExecutorActive(status: string) {
  return status === "active" || status === "running";
}

// Format connector name for display (e.g. "binance_perpetual" -> "Binance Perp")
export function formatConnectorName(name: string) {
  return name
    .replace(/_perpetual$/, " perp")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * A bot name with its doubled deploy stamp collapsed.
 *
 * A deployed bot is named `<config>-<YYYYMMDD>-<HHMMSS>`, and the deploy path
 * appends the stamp to a name that already ends in one — so a real fleet
 * reports `pmm-fleet-btcbrl-global-20260829-121810-20260829-121810`, which is
 * 54 characters of which 15 are a verbatim repeat. In a 288px rail that is the
 * difference between reading which bot a row belongs to and reading
 * `pmm-fleet-btcbrl-glo…`.
 *
 * Only an *immediately repeated* stamp is collapsed, so nothing that tells two
 * runs apart is thrown away: two deploys of one config differ in the stamp
 * itself, and both keep it.
 */
export function shortBotName(name: string): string {
  return name.replace(/(-\d{8}-\d{6})\1$/, "$1");
}
