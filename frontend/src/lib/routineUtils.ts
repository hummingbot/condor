import type { QueryClient } from "@tanstack/react-query";
import type { RoutineInfo } from "@/lib/api";

// ── Config persistence ──

export const ROUTINE_CONFIG_KEY_PREFIX = "routine_config:";

export function loadSavedConfig(
  routineName: string,
): Record<string, unknown> | null {
  try {
    const raw = localStorage.getItem(ROUTINE_CONFIG_KEY_PREFIX + routineName);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveConfig(
  routineName: string,
  values: Record<string, unknown>,
): void {
  try {
    localStorage.setItem(
      ROUTINE_CONFIG_KEY_PREFIX + routineName,
      JSON.stringify(values),
    );
  } catch {
    // storage full or unavailable
  }
}

export function buildConfigValues(
  routine: RoutineInfo,
): Record<string, unknown> {
  const saved = loadSavedConfig(routine.name);
  const values: Record<string, unknown> = {};
  for (const [key, field] of Object.entries(routine.fields)) {
    if (saved && key in saved) {
      values[key] = saved[key];
    } else {
      values[key] = field.default;
    }
  }
  return values;
}

// ── Formatters ──

export function formatRoutineName(name: string): string {
  const display = name.includes("/") ? name.split("/").pop()! : name;
  return display.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export function formatAgo(iso: string): string {
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

// ── Query invalidation ──

export function invalidateRoutineQueries(
  qc: QueryClient,
  routineName?: string,
): void {
  qc.invalidateQueries({ queryKey: ["routine-instances"] });
  qc.invalidateQueries({ queryKey: ["reports-grouped"] });
  qc.invalidateQueries({ queryKey: ["routines"] });
  if (routineName) {
    qc.invalidateQueries({ queryKey: ["routine-reports", routineName] });
  }
}
