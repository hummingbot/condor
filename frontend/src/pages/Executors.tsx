import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronDown, ChevronRight } from "lucide-react";
import { useState } from "react";

import { useServer } from "@/hooks/useServer";
import { api, type ExecutorInfo } from "@/lib/api";

function ExecutorRow({ executor }: { executor: ExecutorInfo }) {
  const [expanded, setExpanded] = useState(false);
  const Chevron = expanded ? ChevronDown : ChevronRight;
  const pnlColor =
    executor.pnl >= 0
      ? "text-[var(--color-green)]"
      : "text-[var(--color-red)]";
  const statusColor =
    executor.status === "active" || executor.status === "running"
      ? "text-[var(--color-green)]"
      : "text-[var(--color-text-muted)]";

  return (
    <>
      <tr
        className="cursor-pointer border-b border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
        onClick={() => setExpanded(!expanded)}
      >
        <td className="flex items-center gap-2 px-4 py-3">
          <Chevron className="h-4 w-4 text-[var(--color-text-muted)]" />
          <span className="font-mono text-xs">{executor.id.slice(0, 8)}</span>
        </td>
        <td className="px-4 py-3">{executor.type}</td>
        <td className="px-4 py-3">{executor.connector}</td>
        <td className="px-4 py-3">{executor.trading_pair}</td>
        <td className="px-4 py-3">{executor.side}</td>
        <td className={`px-4 py-3 text-right ${pnlColor}`}>
          {executor.pnl >= 0 ? "+" : ""}
          {executor.pnl.toFixed(2)}
        </td>
        <td className="px-4 py-3 text-right">
          {executor.volume.toFixed(2)}
        </td>
        <td className={`px-4 py-3 ${statusColor}`}>{executor.status}</td>
        <td className="px-4 py-3 text-[var(--color-text-muted)]">
          {executor.close_type || "—"}
        </td>
      </tr>
      {expanded && Object.keys(executor.config).length > 0 && (
        <tr className="border-b border-[var(--color-border)]/50 bg-[var(--color-bg)]">
          <td colSpan={9} className="px-8 py-3">
            <dl className="grid grid-cols-2 gap-x-6 gap-y-1 text-xs sm:grid-cols-4">
              {Object.entries(executor.config).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-[var(--color-text-muted)]">{k}</dt>
                  <dd className="font-mono">{String(v)}</dd>
                </div>
              ))}
            </dl>
          </td>
        </tr>
      )}
    </>
  );
}

export function Executors() {
  const { server } = useServer();
  const [filters, setFilters] = useState({
    executor_type: "",
    trading_pair: "",
    status: "",
  });

  const { data, isLoading, error } = useQuery({
    queryKey: ["executors", server, filters],
    queryFn: () => api.getExecutors(server!, filters),
    enabled: !!server,
    refetchInterval: 10000,
  });

  if (!server)
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;

  return (
    <div>
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xl font-bold">Executors</h2>
        <div className="flex gap-2">
          <input
            type="text"
            placeholder="Filter pair..."
            value={filters.trading_pair}
            onChange={(e) =>
              setFilters((f) => ({ ...f, trading_pair: e.target.value }))
            }
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
          />
          <select
            value={filters.status}
            onChange={(e) =>
              setFilters((f) => ({ ...f, status: e.target.value }))
            }
            className="rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-sm focus:border-[var(--color-primary)] focus:outline-none"
          >
            <option value="">All statuses</option>
            <option value="active">Active</option>
            <option value="closed">Closed</option>
            <option value="failed">Failed</option>
          </select>
        </div>
      </div>

      {isLoading ? (
        <p className="text-[var(--color-text-muted)]">Loading...</p>
      ) : error ? (
        <p className="text-[var(--color-red)]">
          {error instanceof Error ? error.message : "Error"}
        </p>
      ) : !data?.length ? (
        <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
          <Activity className="h-10 w-10" />
          <p>No executors found</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
                <th className="px-4 py-3 text-left font-medium text-[var(--color-text-muted)]">
                  ID
                </th>
                <th className="px-4 py-3 text-left font-medium text-[var(--color-text-muted)]">
                  Type
                </th>
                <th className="px-4 py-3 text-left font-medium text-[var(--color-text-muted)]">
                  Connector
                </th>
                <th className="px-4 py-3 text-left font-medium text-[var(--color-text-muted)]">
                  Pair
                </th>
                <th className="px-4 py-3 text-left font-medium text-[var(--color-text-muted)]">
                  Side
                </th>
                <th className="px-4 py-3 text-right font-medium text-[var(--color-text-muted)]">
                  PnL
                </th>
                <th className="px-4 py-3 text-right font-medium text-[var(--color-text-muted)]">
                  Volume
                </th>
                <th className="px-4 py-3 text-left font-medium text-[var(--color-text-muted)]">
                  Status
                </th>
                <th className="px-4 py-3 text-left font-medium text-[var(--color-text-muted)]">
                  Close Type
                </th>
              </tr>
            </thead>
            <tbody>
              {data.map((ex) => (
                <ExecutorRow key={ex.id} executor={ex} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
