import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  Layers,
  ListOrdered,
  RefreshCw,
  Wallet,
} from "lucide-react";

import { FallbackSpinner } from "@/components/ui/FallbackSpinner";
import {
  api,
  type SnapshotExecutor,
  type SnapshotTerminalExecutor,
} from "@/lib/api";

// Kind badge colors keyed on executor type prefix (grid_, order_, position_, …).
const KIND_STYLES: Record<string, string> = {
  grid: "border-blue-500/30 bg-blue-500/10 text-blue-400",
  order: "border-emerald-500/30 bg-emerald-500/10 text-emerald-400",
  position: "border-purple-500/30 bg-purple-500/10 text-purple-400",
  dca: "border-amber-500/30 bg-amber-500/10 text-amber-400",
};

function KindBadge({ type }: { type: string }) {
  const kind = type.split("_")[0];
  const style = KIND_STYLES[kind] ?? "border-[var(--color-border)] bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]";
  return (
    <span className={`inline-block rounded border px-1.5 py-0.5 font-mono text-[10px] font-semibold uppercase ${style}`}>
      {type}
    </span>
  );
}

const STATUS_COLORS: Record<string, string> = {
  PENDING: "text-amber-400",
  ACTIVE: "text-emerald-400",
  CLOSING: "text-amber-400",
  CLOSED: "text-[var(--color-text-muted)]",
  FAILED: "text-[var(--color-red)]",
};

function StatusCell({ status }: { status: string }) {
  return (
    <span className={`font-medium ${STATUS_COLORS[status] ?? "text-[var(--color-text-muted)]"}`}>
      {status}
    </span>
  );
}

function StatTile({
  icon: Icon,
  label,
  value,
}: {
  icon: typeof Activity;
  label: string;
  value: string;
}) {
  return (
    <div className="flex-1 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3">
      <div className="mb-1 flex items-center gap-2">
        <Icon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        <span className="text-xs uppercase tracking-wider text-[var(--color-text-muted)]">{label}</span>
      </div>
      <p className="text-xl font-bold tabular-nums text-[var(--color-text)]">{value}</p>
    </div>
  );
}

const TH = "px-3 py-2 text-left text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-muted)]";
const TD = "px-3 py-2 align-top";

function OpenExecutorsTable({ executors }: { executors: SnapshotExecutor[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[var(--color-border)]">
            <th className={TH}>Kind</th>
            <th className={TH}>Status</th>
            <th className={TH}>Phase</th>
            <th className={TH}>Instrument</th>
            <th className={TH}>Venue</th>
            <th className={TH}>Agent</th>
            <th className={`${TH} text-right`}>Exposure</th>
            <th className={TH}>Live Orders</th>
            <th className={TH}>ID</th>
          </tr>
        </thead>
        <tbody>
          {executors.map((ex) => (
            <tr key={ex.id} className="border-t border-[var(--color-border)]/40">
              <td className={TD}><KindBadge type={ex.type} /></td>
              <td className={TD}><StatusCell status={ex.status} /></td>
              <td className={`${TD} font-mono text-[var(--color-text-muted)]`}>{ex.phase || "—"}</td>
              <td className={`${TD} font-medium text-[var(--color-text)]`}>{ex.instrument}</td>
              <td className={`${TD} text-[var(--color-text-muted)]`}>{ex.venue}</td>
              <td className={`${TD} text-[var(--color-text-muted)]`}>
                {ex.agent_slug || "—"}
                {ex.run_id && (
                  <span className="ml-1 font-mono text-[10px] text-[var(--color-text-muted)]/60">{ex.run_id}</span>
                )}
              </td>
              <td className={`${TD} text-right tabular-nums text-[var(--color-text)]`}>
                {ex.exposure_quote ? ex.exposure_quote.toFixed(2) : "—"}
              </td>
              <td className={TD}>
                {ex.live_orders.length === 0 ? (
                  <span className="text-[var(--color-text-muted)]">—</span>
                ) : (
                  <div className="flex flex-wrap gap-1">
                    {ex.live_orders.map((o) => (
                      <span
                        key={o.venue_order_id}
                        title={`${o.role} · ${o.status} · ${o.venue_order_id}`}
                        className={`rounded border px-1 py-0.5 font-mono text-[9px] uppercase ${
                          o.side.toLowerCase() === "buy"
                            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-400"
                            : "border-red-500/30 bg-red-500/10 text-red-400"
                        }`}
                      >
                        {o.side} {o.role}
                      </span>
                    ))}
                  </div>
                )}
              </td>
              <td className={`${TD} font-mono text-[10px] text-[var(--color-text-muted)]`}>{ex.id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function InventoryTable({ inventory }: { inventory: Record<string, string> }) {
  const rows = Object.entries(inventory);
  if (rows.length === 0) return null;
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[var(--color-border)]">
            <th className={TH}>Instrument</th>
            <th className={`${TH} text-right`}>Net Owned (base)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([instrument, amount]) => (
            <tr key={instrument} className="border-t border-[var(--color-border)]/40">
              <td className={`${TD} font-medium text-[var(--color-text)]`}>{instrument}</td>
              <td className={`${TD} text-right font-mono tabular-nums ${Number(amount) >= 0 ? "text-emerald-400" : "text-red-400"}`}>
                {amount}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TerminalTable({ executors }: { executors: SnapshotTerminalExecutor[] }) {
  return (
    <div className="overflow-x-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-[var(--color-border)]">
            <th className={TH}>Kind</th>
            <th className={TH}>Status</th>
            <th className={TH}>Close Reason</th>
            <th className={TH}>Run</th>
            <th className={TH}>ID</th>
          </tr>
        </thead>
        <tbody>
          {executors.map((ex) => (
            <tr key={ex.id} className="border-t border-[var(--color-border)]/40">
              <td className={TD}><KindBadge type={ex.type} /></td>
              <td className={TD}><StatusCell status={ex.status} /></td>
              <td className={`${TD} text-[var(--color-text-muted)]`}>{ex.close_reason || "—"}</td>
              <td className={`${TD} font-mono text-[10px] text-[var(--color-text-muted)]`}>{ex.run_id || "—"}</td>
              <td className={`${TD} font-mono text-[10px] text-[var(--color-text-muted)]`}>{ex.id}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Positions() {
  const { data: snapshot, isLoading, error, refetch, isFetching } = useQuery({
    queryKey: ["executors-snapshot"],
    queryFn: () => api.getExecutorsSnapshot(),
    refetchInterval: 15000,
  });

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-[var(--color-text)]">Positions</h1>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          title="Refresh"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isFetching ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {isLoading ? (
        <FallbackSpinner />
      ) : error ? (
        <p className="text-sm text-[var(--color-red)]">
          {error instanceof Error ? error.message : "Failed to load snapshot"}
        </p>
      ) : snapshot ? (
        <>
          {/* Summary tiles */}
          <div className="flex gap-3">
            <StatTile icon={Activity} label="Open Executors" value={String(snapshot.open_count)} />
            <StatTile icon={ListOrdered} label="Live Orders" value={String(snapshot.live_order_count)} />
            <StatTile
              icon={Layers}
              label="Attributed Exposure"
              value={snapshot.attributed_exposure_quote.toFixed(2)}
            />
          </div>

          {/* Open executors */}
          <div className="space-y-2">
            <h2 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
              Open Executors & Orders
            </h2>
            {snapshot.open_executors.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-[var(--color-border)] py-10 text-[var(--color-text-muted)]">
                <Activity className="h-8 w-8 opacity-40" />
                <p className="text-sm">No open executors</p>
              </div>
            ) : (
              <OpenExecutorsTable executors={snapshot.open_executors} />
            )}
          </div>

          {/* Owned inventory */}
          {Object.keys(snapshot.owned_inventory).length > 0 && (
            <div className="space-y-2">
              <h2 className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                <Wallet className="h-3.5 w-3.5" /> Owned Inventory
              </h2>
              <InventoryTable inventory={snapshot.owned_inventory} />
            </div>
          )}

          {/* Recent terminal executors */}
          {snapshot.recent_terminal.length > 0 && (
            <div className="space-y-2">
              <h2 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                Recent Terminal
              </h2>
              <TerminalTable executors={snapshot.recent_terminal} />
            </div>
          )}
        </>
      ) : null}
    </div>
  );
}
