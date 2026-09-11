import { Brain, ChevronDown, ChevronUp, Search, Zap } from "lucide-react";
import { useMemo, useRef, useState } from "react";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
import type { RoutineInfo, RoutineInstance } from "@/lib/api";
import {
  formatRoutineName,
  formatScope,
  inScope,
  resolveRoutine,
  routineAgent,
  routineAgents,
  type RoutineScope,
} from "@/lib/routineUtils";

/**
 * Which routine the library is reading, and whose routines it lists.
 *
 * Two controls, one question: the scope narrows the list — the general Condor
 * library, or one agent's own — and the second picks a routine out of it. They
 * live together because a filter kept away from the list it filters is a filter
 * nobody uses, which is what the old arrangement proved twice: a scope select
 * stranded in the report's header, over a list that was a 48px rail.
 *
 * Both live in the library sheet's own nav bar, over the report they name.
 * The scope used to be asked in the dock instead, one column away, which is a
 * filter that goes off screen whenever that column or its Routines section is
 * folded — and `parts` is what is left of that split, for a surface that wants
 * only one of the two. The report browser's own header is that surface: it
 * names the routine itself and asks only the scope here, rather than keeping a
 * second select with a second option list to drift from this one.
 *
 * ↑/↓ step through the list, from the closed trigger's keyboard or from the
 * `arrows` buttons beside it; the routine changes under you, as it did in the
 * sidebar this replaced.
 */
export function RoutinePicker({
  routines,
  instances = [],
  scope,
  onScopeChange,
  source,
  onSelect,
  variant = "dock",
  parts = "both",
  arrows = false,
}: {
  routines: RoutineInfo[];
  /** Live runs, for the dot that says one of these is running right now. */
  instances?: RoutineInstance[];
  scope: RoutineScope;
  onScopeChange: (next: RoutineScope) => void;
  /** The routine on screen, in either spelling (see `resolveRoutine`). */
  source?: string;
  onSelect: (name: string) => void;
  /** `"dock"` stacks the controls in the column; `"inline"` is one row. */
  variant?: "dock" | "inline";
  /** Which controls to render — the two live on different surfaces. */
  parts?: "both" | "scope" | "routine";
  /** Step buttons beside the trigger, for the reader who is not on a keyboard. */
  arrows?: boolean;
}) {
  const [open, setOpen] = useState(false);
  // State, not a ref: the portalled panel only gets coordinates once a render
  // has handed it the resolved trigger element.
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const [query, setQuery] = useState("");
  const panelRef = useRef<HTMLDivElement>(null);

  const agents = useMemo(() => routineAgents(routines), [routines]);
  const listed = useMemo(() => inScope(routines, scope), [routines, scope]);
  const active = useMemo(
    () => resolveRoutine(routines, source),
    [routines, source],
  );
  const activeIdx = listed.findIndex((r) => r.name === active?.name);

  const matches = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return listed;
    return listed.filter(
      (r) =>
        r.name.toLowerCase().includes(q) ||
        r.description.toLowerCase().includes(q),
    );
  }, [listed, query]);

  const running = useMemo(
    () =>
      new Set(
        instances
          .filter((i) => i.status === "running" || i.status === "scheduled")
          .map((i) => i.routine_name),
      ),
    [instances],
  );

  /** ↑/↓ on the closed trigger: the next routine, read straight away. */
  const step = (delta: number) => {
    const next = listed[activeIdx + delta];
    if (next) onSelect(next.name);
  };

  const pick = (name: string) => {
    setOpen(false);
    setQuery("");
    onSelect(name);
  };

  const onTriggerKey = (e: React.KeyboardEvent) => {
    if (open) return;
    if (e.key === "ArrowDown") {
      step(1);
      e.preventDefault();
    } else if (e.key === "ArrowUp") {
      step(-1);
      e.preventDefault();
    }
  };

  /** Roving focus down the options, so the list is walkable once it is open. */
  const onPanelKey = (e: React.KeyboardEvent) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    const rows = Array.from(
      panelRef.current?.querySelectorAll<HTMLButtonElement>(
        "[data-routine-row]",
      ) ?? [],
    );
    if (rows.length === 0) return;
    const at = rows.indexOf(document.activeElement as HTMLButtonElement);
    const next = e.key === "ArrowDown" ? at + 1 : at - 1;
    rows[Math.max(0, Math.min(rows.length - 1, next))]?.focus();
    e.preventDefault();
  };

  const owner = active ? routineAgent(active) : null;
  const dock = variant === "dock";

  const trigger = (
    <button
      type="button"
      ref={setAnchor}
      onClick={() => {
        setQuery("");
        setOpen((v) => !v);
      }}
      onKeyDown={onTriggerKey}
      aria-label="Routine"
      aria-haspopup="listbox"
      aria-expanded={open}
      title={
        active ? `${active.name} — ↑/↓ for the next routine` : "Pick a routine"
      }
      className={`flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-left transition-colors hover:border-[var(--color-primary)]/40 hover:bg-[var(--color-surface-hover)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]/40 ${
        dock ? "w-full" : "min-w-0 max-w-[260px] flex-1"
      }`}
    >
      {owner ? (
        <Brain className="h-3 w-3 shrink-0 text-purple-400" />
      ) : (
        <Zap className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
      )}
      <span className="min-w-0 flex-1 truncate text-[11px] font-medium text-[var(--color-text)]">
        {active ? formatRoutineName(active.name) : "Pick a routine"}
      </span>
      {active && active.report_count > 0 && (
        <span className="shrink-0 text-[9px] text-[var(--color-text-muted)]/70">
          {active.report_count}
        </span>
      )}
      <ChevronDown className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
    </button>
  );

  const scopeSelect = (
    <div className={`relative ${dock ? "w-full" : "shrink-0"}`}>
      <select
        value={scope}
        onChange={(e) => onScopeChange(e.target.value)}
        aria-label="Routine scope"
        title="Whose routines this list shows"
        className={`w-full cursor-pointer appearance-none rounded-md border py-1 pl-2 pr-6 text-[10px] font-medium transition-colors focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]/40 ${
          scope === "all" || scope === "condor" || scope === "routine"
            ? "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            : "border-purple-500/30 bg-purple-500/10 text-purple-400"
        }`}
      >
        <option value="all">{formatScope("all")}</option>
        <option value="condor">{formatScope("condor")}</option>
        {/* The other half of the split: everything an agent owns, whoever it
            is. Only the report browser used to ask for it, from a select of
            its own. */}
        {agents.length > 0 && (
          <option value="agent">{formatScope("agent")}</option>
        )}
        {agents.map((slug) => (
          <option key={slug} value={slug}>
            {formatScope(slug)}
          </option>
        ))}
      </select>
      <ChevronDown className="pointer-events-none absolute right-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-current opacity-70" />
    </div>
  );

  /** One step button of the ↑/↓ pair, dead at the end of the list. */
  const arrow = (delta: number) => {
    const target = listed[activeIdx + delta];
    const up = delta < 0;
    return (
      <button
        type="button"
        onClick={() => step(delta)}
        disabled={!target}
        aria-label={up ? "Previous routine" : "Next routine"}
        title={
          target
            ? `${up ? "Previous" : "Next"}: ${formatRoutineName(target.name)}`
            : `No ${up ? "previous" : "next"} routine`
        }
        className="rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:cursor-default disabled:opacity-30 disabled:hover:bg-transparent"
      >
        {up ? (
          <ChevronUp className="h-3.5 w-3.5" />
        ) : (
          <ChevronDown className="h-3.5 w-3.5" />
        )}
      </button>
    );
  };

  return (
    <div
      className={
        dock
          ? "flex flex-col gap-1 px-3 pb-2 pt-1"
          : "flex min-w-0 items-center gap-1.5"
      }
    >
      {parts !== "routine" && scopeSelect}
      {parts !== "scope" && trigger}
      {parts !== "scope" && arrows && (
        <span className="flex shrink-0 items-center">
          {arrow(-1)}
          {arrow(1)}
        </span>
      )}
      <AnchoredMenu
        anchor={anchor}
        open={open && parts !== "scope"}
        onClose={() => setOpen(false)}
        matchAnchorWidth="min"
        maxHeight={360}
        role="listbox"
        className="w-[280px]"
      >
        <div ref={panelRef} onKeyDown={onPanelKey}>
          <div className="sticky top-0 flex items-center gap-1.5 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1.5">
            <Search className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
            <input
              autoFocus
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder={`Search ${formatScope(scope).toLowerCase()}…`}
              className="min-w-0 flex-1 bg-transparent text-[11px] text-[var(--color-text)] placeholder:text-[var(--color-text-muted)]/60 focus:outline-none"
            />
          </div>
          {matches.length === 0 ? (
            <p className="px-3 py-3 text-[11px] text-[var(--color-text-muted)]">
              No routine matches.
            </p>
          ) : (
            matches.map((r) => {
              const isActive = r.name === active?.name;
              const rowOwner = routineAgent(r);
              return (
                <button
                  key={r.name}
                  type="button"
                  data-routine-row
                  role="option"
                  aria-selected={isActive}
                  onClick={() => pick(r.name)}
                  className={`block w-full border-l-2 px-2.5 py-1.5 text-left transition-colors focus:outline-none ${
                    isActive
                      ? "border-l-[var(--color-primary)] bg-[var(--color-primary)]/5"
                      : "border-l-transparent hover:bg-[var(--color-surface-hover)] focus:bg-[var(--color-surface-hover)]"
                  }`}
                >
                  <span className="flex items-center gap-1.5">
                    {rowOwner ? (
                      <Brain className="h-2.5 w-2.5 shrink-0 text-purple-400" />
                    ) : (
                      <Zap className="h-2.5 w-2.5 shrink-0 text-[var(--color-text-muted)]/60" />
                    )}
                    <span
                      className={`min-w-0 flex-1 truncate text-[11px] ${
                        isActive
                          ? "font-semibold text-[var(--color-text)]"
                          : "text-[var(--color-text)]"
                      }`}
                    >
                      {formatRoutineName(r.name)}
                    </span>
                    {running.has(r.name) && (
                      <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-emerald-400 shadow-[0_0_4px_theme(colors.emerald.400)]" />
                    )}
                    {r.report_count > 0 && (
                      <span className="shrink-0 text-[9px] text-[var(--color-text-muted)]/60">
                        {r.report_count}
                      </span>
                    )}
                  </span>
                  <span className="block truncate pl-4 text-[10px] text-[var(--color-text-muted)]/70">
                    {/* Only a mixed list has to say whose routine this is. */}
                    {rowOwner && scope === "all" ? `${rowOwner} · ` : ""}
                    {r.description}
                  </span>
                </button>
              );
            })
          )}
        </div>
      </AnchoredMenu>
    </div>
  );
}
