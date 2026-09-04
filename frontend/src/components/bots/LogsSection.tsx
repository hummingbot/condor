import { useState } from "react";

import type { BotLogEntry } from "@/lib/api";

function formatLogTime(ts?: number): string {
  if (!ts) return "";
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-US", { hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "";
  }
}

/**
 * One bot's logs, with the all / general / error filter.
 *
 * Lived inside the bots accordion until the controller browser became the
 * `/bots` page (FEAT-084) and the accordion went; it is now the occupant of the
 * browser's right drawer at bot scope, which is why it is a component of its
 * own rather than a section of the page that used to own it.
 */
export function LogsSection({ logs }: { logs: BotLogEntry[] }) {
  const [filter, setFilter] = useState<"all" | "error" | "general">("all");
  const filtered = filter === "all" ? logs : logs.filter((l) => l.log_category === filter);

  if (logs.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)] py-2">No logs available</p>
    );
  }

  const errorCount = logs.filter((l) => l.log_category === "error").length;
  const generalCount = logs.filter((l) => l.log_category === "general").length;

  return (
    // A column rather than a stack: the drawer that holds it is screen-tall,
    // and the list is what should take the room the filter row does not.
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 items-center gap-1.5">
        {(["all", "general", "error"] as const).map((f) => {
          const count = f === "all" ? logs.length : f === "error" ? errorCount : generalCount;
          return (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded px-2 py-0.5 text-xs font-medium transition-colors ${
                filter === f
                  ? f === "error"
                    ? "bg-[var(--color-red)]/15 text-[var(--color-red)]"
                    : "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              }`}
            >
              {f} ({count})
            </button>
          );
        })}
      </div>
      <div className="flex-1 min-h-0 overflow-y-auto rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] font-mono text-[11px] leading-relaxed">
        {filtered.map((log, i) => (
          <div
            key={i}
            className={`flex gap-2 px-2.5 py-1 border-b border-[var(--color-border)]/20 last:border-b-0 ${
              log.log_category === "error" ? "bg-[var(--color-red)]/5" : ""
            }`}
          >
            <span className="text-[var(--color-text-muted)] shrink-0 tabular-nums">
              {formatLogTime(log.timestamp)}
            </span>
            <span
              className={`break-all ${
                log.log_category === "error" ? "text-[var(--color-red)]" : "text-[var(--color-text)]"
              }`}
            >
              {log.msg || JSON.stringify(log)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
