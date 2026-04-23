import { ChevronDown } from "lucide-react";
import { useState } from "react";

const PRESETS = [
  { label: "30s", sec: 30 },
  { label: "1m", sec: 60 },
  { label: "5m", sec: 300 },
  { label: "15m", sec: 900 },
  { label: "30m", sec: 1800 },
  { label: "1h", sec: 3600 },
];

interface Props {
  onSchedule: (intervalSec: number) => void;
  disabled?: boolean;
}

export function ScheduleDropdown({ onSchedule, disabled }: Props) {
  const [open, setOpen] = useState(false);

  return (
    <div className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5 text-xs font-medium text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:opacity-50"
      >
        Schedule
        <ChevronDown className="h-3 w-3" />
      </button>
      {open && (
        <div className="absolute right-0 z-10 mt-1 w-28 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] py-1 shadow-lg">
          {PRESETS.map((p) => (
            <button
              key={p.sec}
              type="button"
              onClick={() => {
                onSchedule(p.sec);
                setOpen(false);
              }}
              className="block w-full px-3 py-1.5 text-left text-xs text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
            >
              Every {p.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
