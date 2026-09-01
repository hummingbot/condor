import { Activity, ChevronDown, ChevronRight, Circle, Layers, Server, Shapes } from "lucide-react";
import { useMemo } from "react";

import { formatCurrencyPnl, formatCurrencyVolume, pnlColor } from "@/lib/formatters";
import type { ConvertQuote, PerfNode } from "@/lib/perf-tree";
import { foldLeaves } from "@/lib/perf-tree";

/**
 * The status marker, at one size for every row.
 *
 * Both branches are `shrink-0`, and that is load-bearing rather than tidy: the
 * dot sits in a flex row beside a `truncate`d name, whose flex base is its full
 * max-content width. Shrinkage is distributed in proportion to those bases, so
 * a name long enough to overflow the sidebar handed the 8px marker its share of
 * the deficit and drew it a pixel or two smaller than the one in the row above
 * — the longer the name, the smaller the dot. The two branches are also the
 * same 8px as each other, so a controller does not change the size of its
 * marker on its way to stopping.
 */
export function StatusDot({ status }: { status: string }) {
  const isStopping = status === "stopping";
  const color =
    status === "running" || status === "active"
      ? "text-[var(--color-green)]"
      : status === "stopped" || status === "error" || status === "failed"
        ? "text-[var(--color-red)]"
        : "text-[var(--color-text-muted)]";
  return isStopping ? (
    <span className="h-2 w-2 shrink-0 animate-spin rounded-full border-[1.5px] border-[var(--color-yellow)] border-t-transparent" />
  ) : (
    <Circle className={`h-2 w-2 shrink-0 fill-current ${color}`} />
  );
}

/** The icon that says what kind of thing a node is, at every level. */
function NodeIcon({ node, active }: { node: PerfNode; active: boolean }) {
  const tone = active ? "text-[var(--color-primary)]" : "text-[var(--color-text-muted)]";
  if (node.kind === "fleet") return <Layers className={`h-3.5 w-3.5 shrink-0 ${tone}`} />;
  if (node.kind === "bot") return <Server className={`h-3 w-3 shrink-0 ${tone}`} />;
  if (node.kind === "type") return <Shapes className={`h-3 w-3 shrink-0 ${tone}`} />;
  if (node.kind === "executor") return <Activity className={`h-3 w-3 shrink-0 ${tone}`} />;
  // A controller's marker is its state, which is the one thing the icon slot
  // can say that the label beside it cannot.
  return <StatusDot status={node.leaves[0]?.status ?? ""} />;
}

/** What a row says under its name: how many things, or what and where. */
function subtitle(node: PerfNode): string {
  const n = node.children.length;
  switch (node.kind) {
    case "fleet":
      return `${node.leaves.length} in scope · ${n} group${n !== 1 ? "s" : ""}`;
    case "bot":
    case "type":
      return `${n} ${n === 1 ? "entry" : "entries"}`;
    case "controller": {
      const leaf = node.leaves[0];
      const pair = leaf?.pair ?? "";
      return n > 0 ? `${pair}${pair ? " · " : ""}${n} executor${n !== 1 ? "s" : ""}` : pair;
    }
    case "executor":
      return [node.leaves[0]?.executorType, node.leaves[0]?.pair].filter(Boolean).join(" · ");
  }
}

interface RowProps {
  node: PerfNode;
  depth: number;
  activeId: string;
  collapsed: Set<string>;
  onSelect: (id: string) => void;
  onToggleCollapse: (id: string) => void;
  cv: ConvertQuote;
  currencySymbol: string;
  now: number;
  compact: boolean;
}

function rowClass(active: boolean) {
  return `w-full text-left transition-all border-l-[3px] ${
    active
      ? "bg-[var(--color-primary)]/15 border-l-[var(--color-primary)] shadow-[inset_0_0_0_1px_var(--color-primary)]/10"
      : "border-l-transparent hover:bg-[var(--color-surface-hover)]"
  }`;
}

function ScopeRow({
  node,
  depth,
  activeId,
  collapsed,
  onSelect,
  onToggleCollapse,
  cv,
  currencySymbol,
  now,
  compact,
}: RowProps) {
  const active = node.id === activeId;
  // Each row shows what its own subtree adds up to, through the same fold the
  // report pane uses — a row and the pane it opens cannot disagree.
  const totals = useMemo(() => foldLeaves(node.leaves, cv, now), [node.leaves, cv, now]);
  const hasChildren = node.children.length > 0;
  const isCollapsed = collapsed.has(node.id);

  if (compact) {
    return (
      <>
        <button
          onClick={() => onSelect(node.id)}
          {...(active ? { "data-active-scope": true } : {})}
          className={`flex w-full items-center justify-center py-3 transition-colors ${
            active
              ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
              : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
          }`}
          title={node.label}
        >
          <NodeIcon node={node} active={active} />
        </button>
        {!isCollapsed &&
          node.children.map((child) => (
            <ScopeRow
              key={child.id}
              {...{ activeId, collapsed, onSelect, onToggleCollapse, cv, currencySymbol, now, compact }}
              node={child}
              depth={depth + 1}
            />
          ))}
      </>
    );
  }

  return (
    <>
      <div
        className={`${rowClass(active)} flex items-stretch ${depth === 0 ? "" : "border-t border-[var(--color-border)]/30"}`}
        style={{ paddingLeft: depth === 0 ? 0 : Math.min(depth, 3) * 10 }}
      >
        {hasChildren ? (
          <button
            onClick={() => onToggleCollapse(node.id)}
            className="px-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            title={isCollapsed ? "Expand" : "Collapse"}
            aria-expanded={!isCollapsed}
          >
            {isCollapsed ? <ChevronRight className="h-3.5 w-3.5" /> : <ChevronDown className="h-3.5 w-3.5" />}
          </button>
        ) : (
          <span className="w-[26px] shrink-0" />
        )}
        <button
          onClick={() => onSelect(node.id)}
          {...(active ? { "data-active-scope": true } : {})}
          className="min-w-0 flex-1 py-2 pr-3 text-left"
        >
          <div className="flex items-center gap-1.5">
            <NodeIcon node={node} active={active} />
            <span
              className={`truncate text-[11px] ${
                active ? "font-semibold text-[var(--color-text)]" : "font-medium text-[var(--color-text-muted)]"
              }`}
              title={node.label}
            >
              {node.label}
            </span>
            <span
              className="ml-auto shrink-0 tabular-nums text-[11px] font-semibold"
              style={{ color: pnlColor(totals.net) }}
            >
              {formatCurrencyPnl(totals.net, currencySymbol)}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-2 pl-4.5 text-[10px] text-[var(--color-text-muted)]">
            <span className="truncate">{subtitle(node)}</span>
            {/* Volume is what tells two similarly-profitable configs apart at a
                glance, so it rides beside the PnL. */}
            <span className="ml-auto shrink-0 tabular-nums">
              {formatCurrencyVolume(totals.volume, currencySymbol)}
            </span>
          </div>
        </button>
      </div>
      {!isCollapsed &&
        node.children.map((child) => (
          <ScopeRow
            key={child.id}
            {...{ activeId, collapsed, onSelect, onToggleCollapse, cv, currencySymbol, now, compact }}
            node={child}
            depth={depth + 1}
          />
        ))}
    </>
  );
}

/**
 * The scope picker: one row per node, at every level, reporting the same fold
 * the pane beside it reports.
 *
 * It is a picker over a `PerfNode` tree rather than a list of controllers, so
 * fleet, bot, executor type, controller and executor are all the same row drawn
 * at a different depth — which is the whole reason a bot and an executor can be
 * described by the same strip and the same chart (FEAT-086).
 */
export function ScopeTree({
  root,
  activeId,
  collapsed,
  onSelect,
  onToggleCollapse,
  cv,
  currencySymbol,
  now,
  compact = false,
}: {
  root: PerfNode;
  activeId: string;
  collapsed: Set<string>;
  onSelect: (id: string) => void;
  onToggleCollapse: (id: string) => void;
  cv: ConvertQuote;
  currencySymbol: string;
  now: number;
  compact?: boolean;
}) {
  return (
    <ScopeRow
      node={root}
      depth={0}
      activeId={activeId}
      collapsed={collapsed}
      onSelect={onSelect}
      onToggleCollapse={onToggleCollapse}
      cv={cv}
      currencySymbol={currencySymbol}
      now={now}
      compact={compact}
    />
  );
}
