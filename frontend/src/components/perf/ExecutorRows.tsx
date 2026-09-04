import { Download, Square, X } from "lucide-react";
import { useCallback, useMemo, useState, type FormEvent } from "react";

import {
  ExecutorTable,
  type SortDir,
  type SortKey,
} from "@/components/perf/ExecutorTable";
import { exportExecutorsCsv, type ExecutorStop } from "@/components/perf/executorActions";
import { useEscapeKey } from "@/hooks/useEscapeKey";
import { type ExecutorInfo } from "@/lib/api";
import { stopKeepCopy } from "@/lib/executorStopCopy";
import { isExecutorActive } from "@/lib/formatters";

/**
 * The choice that makes stopping an executor two different actions.
 *
 * Keeping the position leaves the exposure on the exchange with nothing
 * managing it; closing it does not. Neither is recoverable by looking at the
 * result afterwards, so the dialog asks rather than defaulting quietly.
 *
 * What the choice *means* depends on what is selected — an LP executor closes
 * its pool position either way — so the wording comes from `stopKeepCopy`
 * rather than being stated once and hoped over. `executors` is the list the
 * ids are resolved against; without it the dialog falls back to the non-LP
 * wording it has always used.
 */
export function StopConfirmDialog({
  ids,
  executors = [],
  onConfirm,
  onCancel,
}: {
  ids: string[];
  executors?: ExecutorInfo[];
  onConfirm: (ids: string[], keepPosition: boolean) => void;
  onCancel: () => void;
}) {
  useEscapeKey(true, onCancel);
  const [keepPosition, setKeepPosition] = useState(false);
  const count = ids.length;
  const copy = useMemo(
    () => stopKeepCopy(executors.filter((ex) => ids.includes(ex.id))),
    [executors, ids],
  );

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    onConfirm(ids, keepPosition);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onCancel}>
      <div
        className="bg-[var(--color-surface)] border border-[var(--color-border)] rounded-xl shadow-xl p-6 w-full max-w-sm space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold">
          Stop {count === 1 ? "Executor" : `${count} Executors`}?
        </h3>
        <p className="text-xs text-[var(--color-text-muted)]">
          {count === 1
            ? "This will stop the executor."
            : `This will stop ${count} active executors.`}
        </p>

        <form onSubmit={handleSubmit} className="space-y-4">
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={keepPosition}
              onChange={(e) => setKeepPosition(e.target.checked)}
              className="h-4 w-4 rounded border-[var(--color-border)] accent-[var(--color-primary)]"
            />
            <span className="text-sm">{copy.label}</span>
          </label>
          <p className="text-[10px] text-[var(--color-text-muted)] -mt-2 ml-6">
            {keepPosition ? copy.checked : copy.unchecked}
          </p>

          <div className="flex items-center gap-2 justify-end">
            <button
              type="button"
              onClick={onCancel}
              className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              className="rounded-md bg-[var(--color-red)] px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 transition-colors"
            >
              Confirm Stop
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/** What a selection lets you do to it, drawn only while there is one. */
function BulkActionBar({
  count,
  onStop,
  onExport,
  onClear,
  stopping,
}: {
  count: number;
  onStop: () => void;
  onExport: () => void;
  onClear: () => void;
  stopping: boolean;
}) {
  if (count === 0) return null;
  return (
    <div className="flex items-center gap-3 border-b border-[var(--color-primary)]/30 bg-[var(--color-primary)]/5 px-3 py-1.5">
      <span className="text-xs font-medium">{count} selected</span>
      <div className="flex-1" />
      <button
        onClick={onExport}
        className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1 text-[11px] font-medium hover:bg-[var(--color-surface-hover)] transition-colors"
      >
        <Download className="h-3 w-3" />
        Export CSV
      </button>
      <button
        onClick={onStop}
        disabled={stopping}
        className="flex items-center gap-1.5 rounded-md bg-[var(--color-red)] px-2.5 py-1 text-[11px] font-medium text-white hover:opacity-90 transition-colors disabled:opacity-50"
      >
        <Square className="h-3 w-3" />
        {stopping ? "Stopping…" : "Stop Selected"}
      </button>
      <button
        onClick={onClear}
        className="rounded p-1 hover:bg-[var(--color-surface-hover)] transition-colors"
        title="Clear selection"
        aria-label="Clear selection"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

/**
 * The executors under the current scope, as rows.
 *
 * This is the browser's bottom band for any scope whose subtree holds
 * executors — the same table the executors page drew, with the same sorting,
 * the same selection, the same bulk stop and the same CSV export, now reporting
 * whichever slice of the fleet the sidebar has selected rather than the whole
 * history at once.
 *
 * Only what is *active* can be stopped, so the bulk action filters the
 * selection down to those rather than issuing calls that would fail: a
 * selection dragged across a closed executor should not turn into an error
 * about it.
 */
export function ExecutorRows({
  executors,
  stop,
  selectedId,
  onSelect,
  rateFormatPnl,
  rateFormatValue,
  rateFormatDetailed,
}: {
  executors: ExecutorInfo[];
  stop: ExecutorStop;
  selectedId: string | null;
  onSelect: (ex: ExecutorInfo) => void;
  rateFormatPnl?: (val: number, quote: string) => string;
  rateFormatValue?: (val: number, quote: string) => string;
  rateFormatDetailed?: (val: number, quote: string) => string;
}) {
  const [sortKey, setSortKey] = useState<SortKey>("timestamp");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const handleSort = useCallback((key: SortKey) => {
    setSortKey((prevKey) => {
      if (prevKey === key) {
        setSortDir((d) => (d === "asc" ? "desc" : "asc"));
        return prevKey;
      }
      setSortDir("desc");
      return key;
    });
  }, []);

  const toggleSelect = useCallback((id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // The rows on screen, not every row ever selected: walking the sidebar
  // changes which executors are listed, and "select all" means the ones the
  // reader can see.
  const allSelected = executors.length > 0 && executors.every((ex) => selectedIds.has(ex.id));
  const toggleSelectAll = useCallback(() => {
    setSelectedIds((prev) => {
      const everyOne = executors.length > 0 && executors.every((ex) => prev.has(ex.id));
      const next = new Set(prev);
      for (const ex of executors) {
        if (everyOne) next.delete(ex.id);
        else next.add(ex.id);
      }
      return next;
    });
  }, [executors]);

  const selected = useMemo(
    () => executors.filter((ex) => selectedIds.has(ex.id)),
    [executors, selectedIds],
  );

  const handleBulkStop = useCallback(
    () => stop.request(selected.filter((ex) => isExecutorActive(ex.status)).map((ex) => ex.id)),
    [selected, stop],
  );

  const handleStopOne = useCallback((id: string) => stop.request([id]), [stop]);

  return (
    <div className="flex min-h-0 flex-col">
      <BulkActionBar
        count={selected.length}
        onStop={handleBulkStop}
        onExport={() => exportExecutorsCsv(selected.length > 0 ? selected : executors)}
        onClear={() => setSelectedIds(new Set())}
        stopping={stop.pending}
      />
      {stop.error && (
        <p className="px-3 py-1.5 text-[11px] text-[var(--color-red)] bg-[var(--color-red)]/5">
          {stop.error}
        </p>
      )}
      <div className="min-h-0 flex-1 overflow-y-auto scrollbar-thin">
        <ExecutorTable
          executors={executors}
          sortKey={sortKey}
          sortDir={sortDir}
          onSort={handleSort}
          selectedIds={selectedIds}
          onToggleSelect={toggleSelect}
          onSelectAll={toggleSelectAll}
          allSelected={allSelected}
          onRowClick={onSelect}
          selectedExecutorId={selectedId}
          onStop={handleStopOne}
          stoppingIds={stop.stoppingIds}
          rateFormatPnl={rateFormatPnl}
          rateFormatValue={rateFormatValue}
          rateFormatDetailed={rateFormatDetailed}
        />
      </div>
    </div>
  );
}
