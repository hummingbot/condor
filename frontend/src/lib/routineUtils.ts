import type { QueryClient } from "@tanstack/react-query";
import type { RoutineInfo } from "@/lib/api";
import { ROUTINE_CONFIG_KEY_PREFIX } from "@/lib/sessionState";

// ── Config persistence ──

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

// ── Scope: whose routines a list is showing ──

/**
 * The vocabulary of a routine list's scope.
 *
 * `"all"` is everything, `"condor"` the general library — routines that belong
 * to no agent — and anything else is one agent's slug. `"routine"` and
 * `"agent"` are the older spellings the `/routines` page still filters with,
 * understood here so one predicate answers for every surface.
 */
export type RoutineScope = string;

/** The agent that owns a routine, or `null` for the general library. */
export function routineAgent(r: RoutineInfo): string | null {
  return r.source.startsWith("agent:") ? r.source.slice("agent:".length) : null;
}

/** The routines one scope covers. */
export function inScope(
  routines: RoutineInfo[],
  scope: RoutineScope,
): RoutineInfo[] {
  if (scope === "all") return routines;
  if (scope === "condor" || scope === "routine")
    return routines.filter((r) => !routineAgent(r));
  if (scope === "agent") return routines.filter((r) => routineAgent(r));
  return routines.filter((r) => routineAgent(r) === scope);
}

/** Every agent that owns at least one routine, in the order a picker lists them. */
export function routineAgents(routines: RoutineInfo[]): string[] {
  const names = new Set<string>();
  for (const r of routines) {
    const owner = routineAgent(r);
    if (owner) names.add(owner);
  }
  return Array.from(names).sort();
}

/** A slug as a person reads it: `mm_expert` → `Mm Expert`. */
export function formatSlug(slug: string): string {
  return slug.replace(/[_-]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/** What a scope is called in a picker. */
export function formatScope(scope: RoutineScope): string {
  if (scope === "all") return "All routines";
  if (scope === "condor" || scope === "routine") return "Condor";
  if (scope === "agent") return "All agents";
  return formatSlug(scope);
}

/**
 * The routine a name refers to, however it was spelled.
 *
 * Two spellings reach the library for one routine. A run names it as the store
 * registered it — `{slug}/{name}` for an agent's own — while a report names it
 * as the report index filed it, which is the bare name. Both have to land on
 * the same routine, or Run and Config would act on nothing.
 */
export function resolveRoutine(
  routines: RoutineInfo[],
  name?: string,
): RoutineInfo | undefined {
  if (!name) return undefined;
  return (
    routines.find((r) => r.name === name) ??
    routines.find((r) => r.name.split("/").pop() === name.split("/").pop())
  );
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

// Format a schedule interval (in seconds) into a compact label, e.g. 86400 -> "1d"
export function formatInterval(sec: number): string {
  if (sec % 604800 === 0) return `${sec / 604800}w`;
  if (sec % 86400 === 0) return `${sec / 86400}d`;
  if (sec % 3600 === 0) return `${sec / 3600}h`;
  if (sec % 60 === 0) return `${sec / 60}m`;
  return `${sec}s`;
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
