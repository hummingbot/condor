import { useMemo, useState } from "react";

import type { ControllerInfo, ControllerPerformanceSnapshot } from "@/lib/api";
import { controllerKey } from "@/lib/controller-identity";
import { aggregatePnlSeries } from "@/lib/pnl-chart";
import type { ConvertFn } from "@/lib/rates";
import { PnlEvolutionChart } from "./PnlEvolutionChart";

// ── Controller color palette ──

const CTRL_COLORS = ["#22c55e", "#3b82f6", "#f59e0b", "#ef4444", "#a78bfa", "#ec4899", "#14b8a6", "#f97316"];

/** Do two selections hold exactly the same ids? */
function sameIds(a: Set<string>, b: Set<string>): boolean {
  if (a.size !== b.size) return false;
  for (const id of a) if (!b.has(id)) return false;
  return true;
}

// ── Main component ──

interface Props {
  snapshots: ControllerPerformanceSnapshot[];
  controllers: ControllerInfo[];
  currencySymbol?: string;
  convert?: ConvertFn;
  /**
   * The snapshots are only part of the history — the cursor walk that fetched
   * them stopped at a cap or on a failed page, so the line starts later than the
   * fleet did. Surfaced rather than absorbed: a short series that looks complete
   * is the bug (CORR-237).
   */
  truncated?: boolean;
}

/**
 * The bots page's portfolio-wide PNL chart: which controllers are folded in is
 * the only thing this component decides. The drawing is PnlEvolutionChart's
 * (ARCH-242) and the fold is aggregatePnlSeries' (ARCH-243).
 */
export function AggregatedPnlChart({ snapshots, controllers, currencySymbol = "$", convert, truncated = false }: Props) {
  /**
   * One entry per controller in the fleet: `key` is what the fold and the
   * selection are keyed on, `label` is what the chip says.
   *
   * The two are not the same string. A controller is identified by its bot
   * *and* its config id, because deploying one config to two bots gives two
   * live controllers sharing an id — keyed on the id alone they collapsed into
   * a single chip and a single series (CORR-241). The label stays the plain
   * controller id, which is what a user recognises, and only picks up its bot
   * when some other bot is running that very id and the bare label would name
   * two different lines.
   */
  const controllerIds = useMemo(() => {
    const entries: { key: string; label: string; botName: string }[] = [];
    const seen = new Set<string>();
    const labelCounts = new Map<string, number>();
    for (const c of controllers) {
      const key = controllerKey(c);
      if (!key || seen.has(key)) continue;
      seen.add(key);
      const label = c.controller_id || c.controller_name;
      labelCounts.set(label, (labelCounts.get(label) ?? 0) + 1);
      entries.push({ key, label, botName: c.bot_name });
    }
    return entries.map((e) =>
      (labelCounts.get(e.label) ?? 0) > 1 && e.botName
        ? { ...e, label: `${e.label} · ${e.botName}` }
        : e,
    );
  }, [controllers]);

  /**
   * The fleet's ids as one string. The prune below keys on this and not on
   * `controllerIds`, because every `bots` WS frame rebuilds the `controllers`
   * array — and with it `controllerIds` — out of the same ids: keying on the
   * array identity re-synced the selection on every frame, and since the
   * updater always built a fresh Set, `enabled` got a new identity each time,
   * invalidating the `data` memo below and forcing a second render pass for a
   * selection that had not changed (PERF-240).
   */
  const idSignature = controllerIds.map((c) => c.key).join("\u0000");

  const [enabled, setEnabled] = useState<Set<string>>(() => new Set(controllerIds.map((c) => c.key)));
  const [syncedIds, setSyncedIds] = useState(idSignature);

  // Drop ids that left the fleet, while rendering rather than in an effect, so
  // the chips and the fold never paint a controller that is already gone.
  if (syncedIds !== idSignature) {
    setSyncedIds(idSignature);
    setEnabled((prev) => {
      const allIds = new Set(controllerIds.map((c) => c.key));
      const kept = new Set<string>();
      for (const id of prev) if (allIds.has(id)) kept.add(id);
      // Nothing survived the prune -> fall back to the whole fleet.
      const resolved = kept.size === 0 ? allIds : kept;
      // Same selection, expressed differently: keep the identity so the
      // aggregation memo holds.
      return sameIds(resolved, prev) ? prev : resolved;
    });
  }

  const toggleController = (id: string) => {
    setEnabled((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
        if (next.size === 0) return prev;
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const allEnabled = enabled.size === controllerIds.length;
  const toggleAll = () => {
    if (allEnabled) return;
    setEnabled(new Set(controllerIds.map((c) => c.key)));
  };

  const data = useMemo(
    () => aggregatePnlSeries(snapshots, enabled, controllers, convert),
    [snapshots, enabled, controllers, convert],
  );

  if (!snapshots || snapshots.length === 0 || data.length < 2) return null;

  const chips = controllerIds.length > 1 && (
    <div className="flex items-center gap-1.5 px-3 py-1.5 border-b border-[var(--color-border)] bg-[var(--color-bg)] overflow-x-auto">
      <button
        onClick={toggleAll}
        className={`rounded-full px-2.5 py-0.5 text-[10px] font-medium transition-colors whitespace-nowrap ${
          allEnabled
            ? "bg-[var(--color-text-muted)]/20 text-[var(--color-text)]"
            : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        }`}
      >
        All
      </button>
      {controllerIds.map((c, i) => {
        const color = CTRL_COLORS[i % CTRL_COLORS.length];
        const active = enabled.has(c.key);
        return (
          <button
            key={c.key}
            onClick={() => toggleController(c.key)}
            className={`rounded-full px-2.5 py-0.5 text-[10px] font-medium transition-all whitespace-nowrap ${
              active ? "text-white" : "opacity-40 hover:opacity-70"
            }`}
            style={{
              backgroundColor: active ? color : "transparent",
              border: `1px solid ${color}`,
              color: active ? "white" : color,
            }}
          >
            {c.label}
          </button>
        );
      })}
    </div>
  );

  return (
    <PnlEvolutionChart
      data={data}
      title="Portfolio PnL"
      pnlHeight={220}
      volumeHeight={120}
      currencySymbol={currencySymbol}
      filters={chips}
      notice={
        truncated
          ? {
              label: "partial history",
              detail:
                "This fleet has more stored history than one chart may load at once, so the series starts later than the earliest deploy.",
            }
          : undefined
      }
    />
  );
}
