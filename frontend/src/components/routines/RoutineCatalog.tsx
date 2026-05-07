import {
  Activity,
  Brain,
  Clock,
  FileText,
  Zap,
} from "lucide-react";

import { type RoutineInfo, type RoutineInstance } from "@/lib/api";

interface RoutineCatalogProps {
  routines: RoutineInfo[];
  instances: RoutineInstance[];
  selected: string | null;
  onSelect: (routine: RoutineInfo) => void;
}

export function RoutineCatalog({ routines, instances, selected, onSelect }: RoutineCatalogProps) {
  return (
    <div className="space-y-1">
      {routines.map((r) => {
        const isActive = r.name === selected;
        const hasRunning = instances.some(
          (i) => i.routine_name === r.name && (i.status === "running" || i.status === "scheduled"),
        );
        const hasScheduled = instances.some(
          (i) => i.routine_name === r.name && i.status === "scheduled",
        );
        const isAgent = r.source.startsWith("agent:");

        return (
          <button
            key={r.name}
            onClick={() => onSelect(r)}
            className={`w-full rounded-lg border p-3 text-left transition-all ${
              isActive
                ? "border-[var(--color-primary)]/40 bg-[var(--color-primary)]/5"
                : "border-transparent bg-transparent hover:bg-[var(--color-surface-hover)]"
            }`}
          >
            <div className="flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <span className="truncate text-sm font-medium text-[var(--color-text)]">
                  {formatRoutineName(r.name)}
                </span>
                {isAgent && (
                  <span className="shrink-0 flex items-center gap-0.5 rounded bg-purple-500/10 px-1.5 py-0.5 text-[9px] font-bold uppercase text-purple-400">
                    <Brain className="h-2.5 w-2.5" />
                    {r.source.replace("agent:", "")}
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5 shrink-0">
                {r.report_count > 0 && (
                  <span className="flex items-center gap-0.5 rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)]">
                    <FileText className="h-2.5 w-2.5" />
                    {r.report_count}
                  </span>
                )}
                {hasScheduled && (
                  <Clock className="h-3 w-3 text-amber-400" />
                )}
                {hasRunning && (
                  <span className="h-2 w-2 rounded-full bg-emerald-400 shadow-[0_0_6px_theme(colors.emerald.400)]" />
                )}
              </div>
            </div>
            <p className="mt-0.5 text-[11px] text-[var(--color-text-muted)] line-clamp-1">
              {r.description}
            </p>
            <div className="mt-1.5 flex items-center gap-2">
              <span className="inline-flex items-center gap-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]/70">
                {r.is_continuous ? (
                  <>
                    <Activity className="h-2.5 w-2.5" /> Loop
                  </>
                ) : (
                  <>
                    <Zap className="h-2.5 w-2.5" /> One-shot
                  </>
                )}
              </span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

function formatRoutineName(name: string): string {
  // Handle agent/routine format
  const display = name.includes("/") ? name.split("/")[1] : name;
  return display.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}
