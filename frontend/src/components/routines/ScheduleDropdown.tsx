import { ChevronDown, Clock, Trash2 } from "lucide-react";
import { useState } from "react";

import type { RoutineSchedule } from "@/lib/api";

interface Props {
  schedules: RoutineSchedule[];
  onSchedule: (cron: string, tz?: string) => void;
  onRemove: (scheduleId: string) => void;
  disabled?: boolean;
}

export function ScheduleDropdown({ schedules, onSchedule, onRemove, disabled }: Props) {
  const [open, setOpen] = useState(false);
  const [cron, setCron] = useState("");
  const [tz, setTz] = useState("");

  const submit = () => {
    if (!cron.trim()) return;
    onSchedule(cron.trim(), tz.trim() || undefined);
    setCron("");
  };

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
      >
        Schedule
        {schedules.length > 0 && (
          <span className="rounded-full bg-[var(--color-primary)]/10 px-1.5 text-[10px] font-semibold text-[var(--color-primary)]">
            {schedules.length}
          </span>
        )}
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 w-72 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] p-3 shadow-lg">
          <label className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
            Cron expression
          </label>
          <input
            type="text"
            value={cron}
            onChange={(e) => setCron(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="*/15 * * * *"
            className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/50 focus:border-[var(--color-primary)] focus:outline-none"
          />
          <label className="mb-1 mt-2 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
            Timezone (optional)
          </label>
          <input
            type="text"
            value={tz}
            onChange={(e) => setTz(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
            placeholder="UTC"
            className="w-full rounded border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 font-mono text-xs text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/50 focus:border-[var(--color-primary)] focus:outline-none"
          />
          <button
            type="button"
            onClick={submit}
            disabled={disabled || !cron.trim()}
            className="mt-2 w-full rounded bg-[var(--color-primary)] px-3 py-1.5 text-xs font-semibold text-white transition-colors hover:bg-[var(--color-primary)]/80 disabled:opacity-50"
          >
            Add schedule
          </button>

          {schedules.length > 0 && (
            <div className="mt-3 border-t border-[var(--color-border)] pt-2">
              <p className="mb-1 text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">
                Existing schedules
              </p>
              {schedules.map((s) => (
                <div
                  key={s.schedule_id}
                  className="flex items-center justify-between gap-2 py-1"
                >
                  <div className="min-w-0">
                    <span className="flex items-center gap-1 font-mono text-xs text-[var(--color-text)]">
                      <Clock className="h-2.5 w-2.5 shrink-0 text-[var(--color-text-muted)]" />
                      {s.cron}
                      <span className="text-[10px] text-[var(--color-text-muted)]">{s.tz}</span>
                    </span>
                    {s.disabled && (
                      <p className="truncate text-[10px] text-amber-400">
                        disabled{s.disabled_reason ? `: ${s.disabled_reason}` : ""}
                      </p>
                    )}
                  </div>
                  <button
                    type="button"
                    onClick={() => onRemove(s.schedule_id)}
                    className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-red)]/10 hover:text-[var(--color-red)]"
                    title="Remove schedule"
                  >
                    <Trash2 className="h-3 w-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
