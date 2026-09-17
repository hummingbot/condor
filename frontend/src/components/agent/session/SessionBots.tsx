import { Bot } from "lucide-react";
import { useMemo } from "react";

import { prettyCloseType } from "@/components/agent/session/closeTypes";
import type { AgentPerformance } from "@/lib/api";
import { formatCompactUsd, formatCurrencyPnl, pnlTextClass } from "@/lib/formatters";

// ── Bots & Controllers ──
//
// The answer to "what did this session actually run". The aggregator has always
// known it; the HTTP model used to drop it, so the UI could not have shown it.

export function SessionBots({ perf }: { perf?: AgentPerformance | null }) {
  const instances = perf?.bot_instances ?? [];
  const controllers = perf?.controllers ?? [];
  const liveNames = useMemo(() => new Set(perf?.bot_names ?? []), [perf?.bot_names]);

  // Group controllers under the instance they ran on, keeping deploy order.
  const groups = useMemo(() => {
    const byBot = new Map<string, typeof controllers>();
    for (const c of controllers) {
      const key = c.bot_name || "";
      byBot.set(key, [...(byBot.get(key) ?? []), c]);
    }
    const names = instances.length > 0 ? instances : Array.from(byBot.keys());
    return names.map((name) => ({ name, controllers: byBot.get(name) ?? [] }));
  }, [controllers, instances]);

  if (groups.length === 0) return null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
      <h3 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
        <Bot className="h-3.5 w-3.5" /> Bots &amp; Controllers ({groups.length} deploy{groups.length !== 1 ? "s" : ""})
      </h3>
      <div className="space-y-3">
        {groups.map(({ name, controllers: ctrls }) => {
          const live = liveNames.has(name);
          const realized = ctrls.reduce((s, c) => s + (c.realized_pnl_quote ?? 0), 0);
          const volume = ctrls.reduce((s, c) => s + (c.volume_traded ?? 0), 0);
          return (
            <div key={name} className="rounded-md border border-[var(--color-border)]/60 bg-[var(--color-bg)]/40">
              <div className="flex flex-wrap items-center gap-2 border-b border-[var(--color-border)]/40 px-3 py-2">
                <span className="font-mono text-xs text-[var(--color-text)]">{name}</span>
                {/* Derived from the live snapshot's membership, NOT from the
                    controller `status` field — that reports "running" even for
                    instances this session stopped hours ago. */}
                <span
                  className={`rounded-full border px-2 py-0.5 text-[9px] font-bold uppercase ${
                    live
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                      : "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
                  }`}
                >
                  {live ? "running" : "stopped"}
                </span>
                <span className={`ml-auto font-mono text-xs ${pnlTextClass(realized)}`}>
                  {formatCurrencyPnl(realized)}
                </span>
                <span className="font-mono text-[10px] text-[var(--color-text-muted)]">{formatCompactUsd(volume)} vol</span>
              </div>
              {ctrls.length === 0 ? (
                <p className="px-3 py-2 text-[11px] text-[var(--color-text-muted)]">
                  No performance snapshot retained for this deploy.
                </p>
              ) : (
                <table className="w-full text-left text-xs">
                  <thead>
                    <tr className="text-[9px] uppercase tracking-widest text-[var(--color-text-muted)]">
                      <th className="px-3 py-1.5 font-bold">Controller</th>
                      <th className="px-3 py-1.5 text-right font-bold">Realized</th>
                      <th className="px-3 py-1.5 text-right font-bold">Unrealized</th>
                      <th className="px-3 py-1.5 text-right font-bold">Volume</th>
                      <th className="px-3 py-1.5 text-right font-bold">Closes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {ctrls.map((c, i) => {
                      const cCloses = Object.entries(c.close_type_counts ?? {}).filter(([, n]) => n > 0);
                      const cTotal = cCloses.reduce((s, [, n]) => s + n, 0);
                      return (
                        <tr key={`${c.controller_id}-${i}`} className="border-t border-[var(--color-border)]/25">
                          <td className="px-3 py-1.5 font-mono text-[var(--color-text)]">
                            {c.controller_id || "—"}
                            {c.trading_pair && (
                              <span className="ml-1.5 text-[10px] text-[var(--color-text-muted)]">{c.trading_pair}</span>
                            )}
                          </td>
                          <td className={`px-3 py-1.5 text-right font-mono ${(c.realized_pnl_quote ?? 0) >= 0 ? "text-[var(--color-text-muted)]" : "text-[var(--color-red)]"}`}>
                            {formatCurrencyPnl(c.realized_pnl_quote ?? 0)}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono text-[var(--color-text-muted)]">
                            {formatCurrencyPnl(c.unrealized_pnl_quote ?? 0)}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono text-[var(--color-text-muted)]">
                            {formatCompactUsd(c.volume_traded ?? 0)}
                          </td>
                          <td className="px-3 py-1.5 text-right font-mono text-[var(--color-text-muted)]">
                            {cTotal === 0 ? "—" : cTotal}
                            {cTotal > 0 && (
                              <span className="ml-1 text-[9px] text-[var(--color-text-muted)]/70">
                                {cCloses.map(([k]) => prettyCloseType(k)).join(", ")}
                              </span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
