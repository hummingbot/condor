import { ChevronDown, ChevronRight, Clock, Square } from "lucide-react";
import { useState } from "react";

import { type RoutineInstance } from "@/lib/api";

interface RoutineInstancesProps {
  instances: RoutineInstance[];
  onStop: (id: string) => void;
  stopping?: boolean;
}

function formatDuration(sec: number | null): string {
  if (sec == null) return "-";
  if (sec < 1) return `${(sec * 1000).toFixed(0)}ms`;
  if (sec < 60) return `${sec.toFixed(1)}s`;
  return `${Math.floor(sec / 60)}m ${Math.floor(sec % 60)}s`;
}

function formatAgo(ts: number | null): string {
  if (!ts) return "never";
  const diff = Date.now() / 1000 - ts;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

export function RoutineInstances({ instances, onStop, stopping }: RoutineInstancesProps) {
  if (instances.length === 0) return null;

  return (
    <div>
      <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
        Active Instances
      </h3>
      <div className="space-y-2">
        {instances.map((inst) => (
          <InstanceCard key={inst.instance_id} instance={inst} onStop={onStop} stopping={stopping} />
        ))}
      </div>
    </div>
  );
}

function InstanceCard({
  instance: inst,
  onStop,
  stopping,
}: {
  instance: RoutineInstance;
  onStop: (id: string) => void;
  stopping?: boolean;
}) {
  const [showConfig, setShowConfig] = useState(false);
  const configEntries = Object.entries(inst.config || {});

  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-surface-hover)]/50">
      <div className="flex items-center justify-between px-3 py-2">
        <div className="flex items-center gap-3 flex-wrap">
          <span
            className={`rounded px-1.5 py-0.5 text-[10px] font-bold uppercase ${
              inst.status === "running" || inst.status === "scheduled"
                ? "bg-emerald-500/10 text-emerald-400"
                : inst.status === "completed"
                  ? "bg-blue-500/10 text-blue-400"
                  : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
            }`}
          >
            {inst.status}
          </span>
          {inst.schedule?.type === "interval" && (
            <span className="flex items-center gap-1 text-[10px] text-[var(--color-text-muted)]">
              <Clock className="h-3 w-3" />
              every {inst.schedule.interval_sec as number}s
            </span>
          )}
          <span className="text-[10px] text-[var(--color-text-muted)]">
            {inst.run_count} runs · {formatAgo(inst.last_run_at)}
          </span>
          {inst.last_duration != null && (
            <span className="text-[10px] text-[var(--color-text-muted)]">
              {formatDuration(inst.last_duration)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          {configEntries.length > 0 && (
            <button
              onClick={() => setShowConfig(!showConfig)}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
              title="Show config"
            >
              {showConfig ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
            </button>
          )}
          <button
            type="button"
            onClick={() => onStop(inst.instance_id)}
            disabled={stopping}
            className="rounded p-1 text-[var(--color-red)] hover:bg-[var(--color-red)]/10"
            title="Stop"
          >
            <Square className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
      {showConfig && configEntries.length > 0 && (
        <div className="border-t border-[var(--color-border)]/50 px-3 py-2">
          <div className="grid grid-cols-2 gap-x-4 gap-y-1">
            {configEntries.map(([key, value]) => (
              <div key={key} className="flex items-baseline gap-2 text-[10px]">
                <span className="text-[var(--color-text-muted)]">{key}:</span>
                <span className="font-mono text-[var(--color-text)]">{String(value)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
