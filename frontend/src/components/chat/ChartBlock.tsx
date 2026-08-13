// ── Interactive charts inside a chat bubble ──
//
// The agent draws by writing a ```chart fence with a small JSON spec, exactly
// the way it writes a table: the chart rides the ordinary text channel, so
// streaming, persistence and history replay never learn it exists. Anything
// that cannot render the fence (Telegram, transcript views) still shows a
// readable code block, which is why every failure path here degrades to <pre>
// rather than to nothing.

import { Component, type ReactNode } from "react";
import { Loader2 } from "lucide-react";
import {
  Area,
  Bar,
  CartesianGrid,
  ComposedChart,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

// ── Spec ──

const CHART_TYPES = ["line", "area", "bar"] as const;
type ChartType = (typeof CHART_TYPES)[number];

/** Tolerant caps: a spec over them is sliced, never rejected. */
const MAX_ROWS = 200;
const MAX_SERIES = 4;

interface ChartSeries {
  key: string;
  name: string;
  color: string;
}

interface ChartSpec {
  type: ChartType;
  title: string;
  x: string;
  series: ChartSeries[];
  data: Record<string, unknown>[];
}

// Named colors the agent may ask for by meaning rather than by hex. Everything
// else falls through to the fixed categorical order, which is the only way a
// series keeps its color when a chart gains or loses one.
const NAMED_COLORS: Record<string, string> = {
  up: "var(--chart-up)",
  down: "var(--chart-down)",
  green: "var(--color-green)",
  red: "var(--color-red)",
  yellow: "var(--color-yellow)",
  accent: "var(--color-accent)",
};

const SERIES_PALETTE = [
  "var(--chart-series-1)",
  "var(--chart-series-2)",
  "var(--chart-series-3)",
  "var(--chart-series-4)",
];

function seriesColor(raw: unknown, index: number): string {
  if (typeof raw === "string" && raw.trim()) {
    const named = NAMED_COLORS[raw.trim().toLowerCase()];
    if (named) return named;
    // A literal CSS color the model wrote itself. Kept, but only if it cannot
    // smuggle anything into the style attribute.
    if (/^(#[0-9a-f]{3,8}|[a-z]+)$/i.test(raw.trim())) return raw.trim();
  }
  return SERIES_PALETTE[index % SERIES_PALETTE.length];
}

/**
 * Read a spec out of a fence body, or null if it is not one (yet).
 *
 * Null is the honest answer for both a half-streamed fence and a malformed
 * one; the caller decides which of the two it is from `live`.
 */
function parseChartSpec(raw: string): ChartSpec | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;

  const spec = parsed as Record<string, unknown>;
  const type = String(spec.type ?? "line").toLowerCase() as ChartType;
  if (!CHART_TYPES.includes(type)) return null;

  const x = typeof spec.x === "string" ? spec.x : "";
  if (!x) return null;

  if (!Array.isArray(spec.series) || !Array.isArray(spec.data)) return null;

  const series: ChartSeries[] = [];
  for (const entry of spec.series) {
    if (!entry || typeof entry !== "object") continue;
    const s = entry as Record<string, unknown>;
    const key = typeof s.key === "string" ? s.key : "";
    if (!key) continue;
    series.push({
      key,
      name: typeof s.name === "string" && s.name ? s.name : key,
      color: seriesColor(s.color, series.length),
    });
    if (series.length === MAX_SERIES) break;
  }
  if (series.length === 0) return null;

  const data = spec.data
    .filter((row): row is Record<string, unknown> => !!row && typeof row === "object")
    .slice(0, MAX_ROWS);
  if (data.length === 0) return null;

  return {
    type,
    title: typeof spec.title === "string" ? spec.title : "",
    x,
    series,
    data,
  };
}

// ── Rendering ──

/** Axis ticks: enough digits to be read, few enough to fit a chat bubble. */
function formatTick(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value ?? "");
  const abs = Math.abs(value);
  if (abs >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (abs >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  if (abs >= 100) return value.toFixed(0);
  if (abs >= 1) return value.toFixed(2);
  return value.toPrecision(3);
}

function formatValue(value: unknown): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return String(value ?? "—");
  const abs = Math.abs(value);
  const decimals = abs >= 1000 ? 2 : abs >= 1 ? 4 : 6;
  return value.toLocaleString(undefined, { maximumFractionDigits: decimals });
}

interface TooltipEntry {
  dataKey?: string | number;
  name?: string | number;
  value?: unknown;
  color?: string;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: unknown;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)]/95 px-2.5 py-2 text-[11px] leading-relaxed shadow-lg backdrop-blur-sm">
      <div className="mb-1 text-[10px] text-[var(--color-text-muted)]">{String(label ?? "")}</div>
      {payload.map((entry, i) => (
        <div key={i} className="flex items-center justify-between gap-3">
          <span className="flex items-center gap-1.5 text-[var(--color-text-muted)]">
            <span
              className="h-1.5 w-1.5 shrink-0 rounded-full"
              style={{ background: entry.color }}
            />
            {String(entry.name ?? entry.dataKey ?? "")}
          </span>
          <span className="font-medium tabular-nums text-[var(--color-text)]">
            {formatValue(entry.value)}
          </span>
        </div>
      ))}
    </div>
  );
}

/** The unparseable fence, rendered as what it textually is. */
function RawFence({ raw }: { raw: string }) {
  return (
    <pre>
      <code className="language-chart">{raw}</code>
    </pre>
  );
}

function Chart({ spec }: { spec: ChartSpec }) {
  const { type, series } = spec;

  return (
    <div className="my-2 overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      {spec.title && (
        <div className="border-b border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5">
          <p className="text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            {spec.title}
          </p>
        </div>
      )}
      <div className="px-1 py-1">
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart
            data={spec.data}
            margin={{ top: 12, right: 12, left: 0, bottom: 0 }}
            barCategoryGap="20%"
          >
            <defs>
              {series.map((s, i) => (
                <linearGradient key={s.key} id={`chartGrad-${i}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={s.color} stopOpacity={0.25} />
                  <stop offset="95%" stopColor={s.color} stopOpacity={0.02} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" strokeOpacity={0.5} />
            <XAxis
              dataKey={spec.x}
              tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
              stroke="var(--color-border)"
              tickLine={false}
              minTickGap={24}
            />
            <YAxis
              // A bar is read against zero, so its axis has to start there. A
              // price or PnL line is read for its shape, and anchoring that at
              // zero is what flattens a day of trading into a straight line.
              domain={type === "bar" ? [0, "auto"] : ["auto", "auto"]}
              tickFormatter={formatTick}
              tick={{ fontSize: 10, fill: "var(--color-text-muted)" }}
              stroke="var(--color-border)"
              tickLine={false}
              axisLine={false}
              width={52}
            />
            <Tooltip content={<ChartTooltip />} cursor={{ stroke: "var(--color-border)" }} />
            {series.map((s, i) =>
              type === "bar" ? (
                <Bar
                  key={s.key}
                  dataKey={s.key}
                  name={s.name}
                  fill={s.color}
                  radius={[4, 4, 0, 0]}
                  maxBarSize={48}
                />
              ) : type === "area" ? (
                <Area
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  name={s.name}
                  stroke={s.color}
                  strokeWidth={2}
                  fill={`url(#chartGrad-${i})`}
                  dot={false}
                />
              ) : (
                <Line
                  key={s.key}
                  type="monotone"
                  dataKey={s.key}
                  name={s.name}
                  stroke={s.color}
                  strokeWidth={2}
                  dot={false}
                />
              )
            )}
            {/* One series is named by the title and the axis; a legend box
                would only repeat it. Two or more must never be told apart by
                color alone. */}
            {series.length > 1 && (
              <Legend
                verticalAlign="top"
                align="right"
                iconType={type === "bar" ? "square" : "plainline"}
                wrapperStyle={{ fontSize: 10, paddingBottom: 4 }}
                formatter={(value: string) => (
                  <span className="text-[10px] text-[var(--color-text-muted)]">{value}</span>
                )}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

/**
 * A recharts crash is a rendering detail of one bubble, never a reason for the
 * message list to disappear. The fallback is the same code block a consumer
 * without this component would have shown.
 */
class ChartErrorBoundary extends Component<{ raw: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error) {
    console.error("[ChartBlock]", error);
  }

  render() {
    if (this.state.failed) return <RawFence raw={this.props.raw} />;
    return this.props.children;
  }
}

/**
 * The ```chart fence, rendered.
 *
 * While the answer is still streaming, an unparseable body is simply a fence
 * whose JSON has not finished arriving — showing raw braces that flash and
 * rearrange for a second is worse than a quiet placeholder. Once the turn is
 * over the same failure is a real malformed spec, and then the honest render
 * is the text the model actually wrote.
 */
export function ChartBlock({ raw, live }: { raw: string; live: boolean }) {
  const spec = parseChartSpec(raw);

  if (!spec) {
    if (live) {
      return (
        <div className="my-2 flex items-center gap-1.5 rounded-lg border border-dashed border-[var(--color-border)] px-3 py-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 className="h-3 w-3 animate-spin" />
          Rendering chart...
        </div>
      );
    }
    return <RawFence raw={raw} />;
  }

  return (
    <ChartErrorBoundary raw={raw}>
      <Chart spec={spec} />
    </ChartErrorBoundary>
  );
}
