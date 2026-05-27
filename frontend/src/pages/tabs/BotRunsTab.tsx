import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Circle, Database, Filter, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";

import { useServer } from "@/hooks/useServer";
import { api, type BotRunInfo } from "@/lib/api";

function formatTimestamp(ts: string | null): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    return d.toLocaleString("en-US", {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return ts;
  }
}

function formatDuration(start: string | null, end: string | null): string {
  if (!start) return "—";
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  const diffMs = endMs - startMs;
  if (diffMs < 0) return "—";
  const days = Math.floor(diffMs / 86400000);
  const hours = Math.floor((diffMs % 86400000) / 3600000);
  const mins = Math.floor((diffMs % 3600000) / 60000);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

function formatPnl(value: number): string {
  if (value === 0) return "—";
  const sign = value >= 0 ? "+" : "";
  return `${sign}$${Math.abs(value).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

function formatVolume(value: number): string {
  if (value === 0) return "—";
  return `$${value.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;
}

const STATUS_COLORS: Record<string, string> = {
  RUNNING: "text-[var(--color-green)]",
  CREATED: "text-[var(--color-yellow)]",
  STOPPED: "text-[var(--color-text-muted)]",
  ERROR: "text-[var(--color-red)]",
};

const DEPLOYMENT_COLORS: Record<string, string> = {
  DEPLOYED: "bg-[var(--color-green)]/15 text-[var(--color-green)]",
  ARCHIVED: "bg-[var(--color-text-muted)]/15 text-[var(--color-text-muted)]",
  FAILED: "bg-[var(--color-red)]/15 text-[var(--color-red)]",
};

function StatusDot({ status }: { status: string }) {
  const color = STATUS_COLORS[status] ?? "text-[var(--color-text-muted)]";
  return <Circle className={`h-2 w-2 fill-current ${color}`} />;
}

type FilterStatus = "ALL" | "RUNNING" | "STOPPED" | "ARCHIVED";

export function BotRunsTab() {
  const { server } = useServer();
  const [filter, setFilter] = useState<FilterStatus>("ALL");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [deleting, setDeleting] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const { data, isLoading, error } = useQuery({
    queryKey: ["bot-runs", server],
    queryFn: () => api.getBotRuns(server!, { limit: 200 }),
    enabled: !!server,
    refetchInterval: 30_000,
  });

  const deleteMutation = useMutation({
    mutationFn: (botRunId: number) => api.deleteBotRun(server!, botRunId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["bot-runs", server] });
    },
  });

  const runs = data?.runs ?? [];

  const filtered = useMemo(() => {
    if (filter === "ALL") return runs;
    if (filter === "RUNNING") return runs.filter((r) => r.run_status === "RUNNING");
    if (filter === "STOPPED") return runs.filter((r) => r.run_status === "STOPPED" && r.deployment_status !== "ARCHIVED");
    if (filter === "ARCHIVED") return runs.filter((r) => r.deployment_status === "ARCHIVED");
    return runs;
  }, [runs, filter]);

  const counts = useMemo(() => {
    const c = { ALL: runs.length, RUNNING: 0, STOPPED: 0, ARCHIVED: 0 };
    for (const r of runs) {
      if (r.run_status === "RUNNING") c.RUNNING++;
      else if (r.deployment_status === "ARCHIVED") c.ARCHIVED++;
      else c.STOPPED++;
    }
    return c;
  }, [runs]);

  const archivedInView = useMemo(
    () => filtered.filter((r) => r.deployment_status === "ARCHIVED"),
    [filtered],
  );

  const allArchivedSelected = archivedInView.length > 0 && archivedInView.every((r) => selected.has(r.bot_name));

  const toggleSelect = (botName: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(botName)) next.delete(botName);
      else next.add(botName);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (allArchivedSelected) {
      setSelected(new Set());
    } else {
      setSelected(new Set(archivedInView.map((r) => r.bot_name)));
    }
  };

  const handleDelete = async (run: BotRunInfo) => {
    if (!run.bot_run_id) return;
    setDeleting(run.bot_name);
    try {
      await deleteMutation.mutateAsync(run.bot_run_id);
      setSelected((prev) => {
        const next = new Set(prev);
        next.delete(run.bot_name);
        return next;
      });
    } finally {
      setDeleting(null);
    }
  };

  const handleBulkDelete = async () => {
    const toDelete = runs.filter(
      (r) => selected.has(r.bot_name) && r.deployment_status === "ARCHIVED" && r.bot_run_id,
    );
    if (toDelete.length === 0) return;
    if (!confirm(`Delete ${toDelete.length} archived bot run(s)?`)) return;

    for (const run of toDelete) {
      setDeleting(run.bot_name);
      try {
        await deleteMutation.mutateAsync(run.bot_run_id!);
      } catch {
        // continue with remaining
      }
    }
    setSelected(new Set());
    setDeleting(null);
  };

  if (!server) {
    return <p className="text-[var(--color-text-muted)]">Select a server</p>;
  }

  if (isLoading) return <p className="text-[var(--color-text-muted)]">Loading...</p>;
  if (error) {
    return (
      <p className="text-[var(--color-red)]">
        {error instanceof Error ? error.message : "Error loading bot runs"}
      </p>
    );
  }

  if (runs.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-16 text-[var(--color-text-muted)]">
        <Database className="h-10 w-10" />
        <p>No bot runs found</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Filters + bulk actions */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Filter className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
          {(["ALL", "RUNNING", "STOPPED", "ARCHIVED"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded-md px-2.5 py-1 text-xs font-medium transition-colors ${
                filter === f
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {f.charAt(0) + f.slice(1).toLowerCase()} ({counts[f]})
            </button>
          ))}
        </div>

        {selected.size > 0 && (
          <button
            onClick={handleBulkDelete}
            disabled={deleting !== null}
            className="flex items-center gap-1.5 rounded-md bg-[var(--color-red)]/10 px-3 py-1.5 text-xs font-medium text-[var(--color-red)] hover:bg-[var(--color-red)]/20 transition-colors disabled:opacity-50"
          >
            <Trash2 className="h-3 w-3" />
            Delete {selected.size} selected
          </button>
        )}
      </div>

      {/* Table */}
      <div className="overflow-hidden rounded-lg border border-[var(--color-border)]">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--color-border)] bg-[var(--color-surface)]">
                <th className="w-8 px-2 py-3">
                  {archivedInView.length > 0 && (
                    <input
                      type="checkbox"
                      checked={allArchivedSelected}
                      onChange={toggleSelectAll}
                      className="rounded border-[var(--color-border)] accent-[var(--color-primary)]"
                    />
                  )}
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Bot Name
                </th>
                <th className="px-4 py-3 text-center text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Status
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  PnL
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Volume
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Started
                </th>
                <th className="px-4 py-3 text-right text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Duration
                </th>
                <th className="w-10 px-2 py-3" />
              </tr>
            </thead>
            <tbody>
              {filtered.map((run, i) => (
                <BotRunRow
                  key={`${run.bot_name}-${run.created_at}-${i}`}
                  run={run}
                  isSelected={selected.has(run.bot_name)}
                  isDeleting={deleting === run.bot_name}
                  onToggleSelect={() => toggleSelect(run.bot_name)}
                  onDelete={() => handleDelete(run)}
                />
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function BotRunRow({
  run,
  isSelected,
  isDeleting,
  onToggleSelect,
  onDelete,
}: {
  run: BotRunInfo;
  isSelected: boolean;
  isDeleting: boolean;
  onToggleSelect: () => void;
  onDelete: () => void;
}) {
  const deplClass = DEPLOYMENT_COLORS[run.deployment_status] ?? "bg-[var(--color-surface)] text-[var(--color-text-muted)]";
  const isArchived = run.deployment_status === "ARCHIVED";
  const pnl = run.global_pnl_quote;
  const pnlColor = pnl > 0 ? "text-[var(--color-green)]" : pnl < 0 ? "text-[var(--color-red)]" : "text-[var(--color-text-muted)]";

  return (
    <tr className={`border-b border-[var(--color-border)]/30 transition-colors ${isDeleting ? "opacity-40" : "hover:bg-[var(--color-surface-hover)]/50"}`}>
      <td className="w-8 px-2 py-2.5">
        {isArchived && (
          <input
            type="checkbox"
            checked={isSelected}
            onChange={onToggleSelect}
            disabled={isDeleting}
            className="rounded border-[var(--color-border)] accent-[var(--color-primary)]"
          />
        )}
      </td>
      <td className="px-4 py-2.5">
        <div>
          <span className="font-medium">{run.bot_name}</span>
          {run.num_controllers > 0 && (
            <span className="ml-2 text-[10px] text-[var(--color-text-muted)]">
              {run.num_controllers} ctrl{run.num_controllers > 1 ? "s" : ""}
            </span>
          )}
        </div>
        <div className="text-[11px] text-[var(--color-text-muted)]">
          {run.account_name || "—"}
        </div>
      </td>
      <td className="px-4 py-2.5">
        <div className="flex flex-col items-center gap-1">
          <div className="flex items-center gap-1.5">
            <StatusDot status={run.run_status} />
            <span className="text-xs capitalize">
              {run.run_status?.toLowerCase() || "—"}
            </span>
          </div>
          {run.deployment_status && (
            <span className={`inline-flex rounded-full px-2 py-0.5 text-[10px] font-medium ${deplClass}`}>
              {run.deployment_status}
            </span>
          )}
        </div>
      </td>
      <td className={`px-4 py-2.5 text-right tabular-nums font-medium ${pnlColor}`}>
        {formatPnl(pnl)}
      </td>
      <td className="px-4 py-2.5 text-right text-[var(--color-text-muted)] tabular-nums">
        {formatVolume(run.volume_traded)}
      </td>
      <td className="px-4 py-2.5 text-right text-[var(--color-text-muted)] tabular-nums">
        {formatTimestamp(run.created_at)}
      </td>
      <td className="px-4 py-2.5 text-right tabular-nums">
        {formatDuration(run.created_at, run.stopped_at)}
      </td>
      <td className="w-10 px-2 py-2.5">
        {isArchived && !isDeleting && (
          <button
            onClick={(e) => {
              e.stopPropagation();
              if (confirm(`Delete "${run.bot_name}"?`)) onDelete();
            }}
            className="rounded p-1 text-[var(--color-text-muted)] hover:text-[var(--color-red)] hover:bg-[var(--color-red)]/10 transition-colors"
            title="Delete bot run"
          >
            <Trash2 className="h-3.5 w-3.5" />
          </button>
        )}
      </td>
    </tr>
  );
}
