import { Activity, Bot, ChevronDown, ChevronRight, Circle, Layers, Server } from "lucide-react";
import { useMemo, type ReactNode } from "react";

import { runKeyLabel } from "@/lib/agent-attribution";
import { agentColor } from "@/lib/agentColor";
import { formatCurrencyPnl, formatCurrencyVolume, pnlColor, shortBotName } from "@/lib/formatters";
import type { ConvertQuote, PerfNode } from "@/lib/perf-tree";
import { agentOfNodeId, foldLeaves, TOP_LEVEL_KINDS } from "@/lib/perf-tree";

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
  // An agent keeps its own colour here, not the row's tone: it is the same
  // identity palette the chat gutter gives it (`agentColor`, four CVD-validated
  // series vars), so the agent you are reading on `/bots` is the colour you
  // already know it by.
  if (node.kind === "agent") {
    return (
      <Bot
        className="h-3.5 w-3.5 shrink-0"
        style={{ color: agentColor(agentOfNodeId(node.id) ?? node.label) }}
      />
    );
  }
  if (node.kind === "bot") return <Server className={`h-3.5 w-3.5 shrink-0 ${tone}`} />;
  // The "Unattached" row and the per-controller-id buckets beneath it wear the
  // executor's own glyph at the parent size: both hold executors and nothing
  // else, and the honest thing for either to say is which. `Layers` would read
  // as a second fleet, and `Server` as a bot — which is exactly what neither is.
  if (node.kind === "orphans" || node.kind === "group") {
    return <Activity className={`h-3.5 w-3.5 shrink-0 ${tone}`} />;
  }
  if (node.kind === "executor") return <Activity className={`h-3 w-3 shrink-0 ${tone}`} />;
  // A controller's marker is its state, which is the one thing the icon slot
  // can say that the label beside it cannot.
  return <StatusDot status={node.leaves[0]?.status ?? ""} />;
}

/**
 * What a row says under its name: how many things, or what and where.
 *
 * A controller row names its bot when more than one is in scope. It used to
 * hang under a bot node that said so; with the grouping level retired in favour
 * of the bot bubbles above the tree, the row has to carry that fact itself or
 * two identically-configured controllers of two different bots become
 * indistinguishable.
 */
function subtitle(node: PerfNode, showBot: boolean): string {
  const n = node.children.length;
  switch (node.kind) {
    case "fleet": {
      const ctrls = node.children.filter((c) => c.kind === "controller").length;
      return `${node.leaves.length} in scope · ${ctrls} controller${ctrls !== 1 ? "s" : ""}`;
    }
    // Counted by kind rather than as one total, because that *is* the fact the
    // row exists to state: an agent operates bots and creates loose executors,
    // and which of the two it is doing is the shape of its strategy.
    case "agent": {
      const counted = (["bot", "controller", "executor"] as const)
        .map((kind) => [kind, node.children.filter((c) => c.kind === kind).length] as const)
        .filter(([, count]) => count > 0)
        .map(([kind, count]) => `${count} ${kind}${count !== 1 ? "s" : ""}`);
      return counted.join(" · ") || "nothing in scope";
    }
    case "bot":
      return `${n} controller${n !== 1 ? "s" : ""}`;
    // Counted by dead controller id rather than by executor: that is the
    // number a reader who opens this row is actually choosing between, and it
    // is usually far smaller than the executor count folded beneath it.
    case "orphans":
      return `${n} controller${n !== 1 ? "s" : ""} left no record`;
    // Counted in executors, because that is all a group ever holds: it exists
    // precisely for the leaves no controller claims, so the bot row's wording
    // would name a level that is not there.
    case "group":
      return `${n} executor${n !== 1 ? "s" : ""}`;
    case "controller": {
      const leaf = node.leaves[0];
      return [
        leaf?.pair,
        showBot && leaf?.bot ? shortBotName(leaf.bot) : "",
        n > 0 ? `${n} executor${n !== 1 ? "s" : ""}` : "",
      ]
        .filter(Boolean)
        .join(" · ");
    }
    case "executor":
      return [node.leaves[0]?.executorType, node.leaves[0]?.pair].filter(Boolean).join(" · ");
  }
}

interface RowProps {
  node: PerfNode;
  depth: number;
  activeId: string;
  /** The nodes whose children are drawn. Everything else is shut. */
  open: Set<string>;
  showBot: boolean;
  onSelect: (id: string) => void;
  onToggleOpen: (id: string) => void;
  cv: ConvertQuote;
  currencySymbol: string;
  now: number;
  compact: boolean;
  renderAction?: (node: PerfNode) => ReactNode;
}

/**
 * What the row calls itself.
 *
 * A bot node carries its *full* name — that is the name the API takes, and the
 * Stop button on the row posts it — so the shortening a 288px column needs
 * happens here rather than in the tree.
 */
function rowLabel(node: PerfNode): string {
  // A group's label is the controller id its executors carry, which is `main`
  // for a hand-opened position but a bot-shaped name for one a stopped
  // deployment left behind — so it gets the same shortening, which is a no-op
  // for everything that is not one.
  if (node.kind === "bot" || node.kind === "group") return shortBotName(node.label);
  // The one "Unattached" row is a fixed label, not a name pulled off a record —
  // shortening it would be a no-op at best and a mangled label at worst.
  if (node.kind === "orphans") return node.label;
  // An agent row's label is its run key, which is its id — said out loud as
  // `agent / strategy`, the same two slugs the bot names beneath it are built
  // from, so the column can be matched by eye.
  if (node.kind === "agent") return runKeyLabel(node.label);
  return node.label;
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
  open,
  showBot,
  onSelect,
  onToggleOpen,
  cv,
  currencySymbol,
  now,
  compact,
  renderAction,
}: RowProps) {
  const active = node.id === activeId;
  // Each row shows what its own subtree adds up to, through the same fold the
  // report pane uses — a row and the pane it opens cannot disagree.
  const totals = useMemo(() => foldLeaves(node.leaves, cv, now), [node.leaves, cv, now]);
  const hasChildren = node.children.length > 0;
  const isOpen = open.has(node.id);
  const action = renderAction?.(node);

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
        {isOpen &&
          node.children.map((child) => (
            <ScopeRow
              key={child.id}
              {...{ activeId, open, showBot, onSelect, onToggleOpen, cv, currencySymbol, now, compact, renderAction }}
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
        className={`${rowClass(active)} flex items-stretch border-t border-[var(--color-border)]/30`}
        style={{ paddingLeft: depth === 0 ? 0 : Math.min(depth, 3) * 10 }}
      >
        {hasChildren ? (
          <button
            onClick={() => onToggleOpen(node.id)}
            className="px-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            title={isOpen ? "Collapse" : "Expand"}
            aria-expanded={isOpen}
          >
            {isOpen ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" />}
          </button>
        ) : (
          <span className="w-[26px] shrink-0" />
        )}
        <button
          onClick={() => onSelect(node.id)}
          {...(active ? { "data-active-scope": true } : {})}
          className={`min-w-0 flex-1 py-2 text-left ${action ? "pr-1" : "pr-3"}`}
        >
          <div className="flex items-center gap-1.5">
            <NodeIcon node={node} active={active} />
            <span
              className={`truncate text-[11px] ${
                active || TOP_LEVEL_KINDS.has(node.kind)
                  ? "font-semibold text-[var(--color-text)]"
                  : "font-medium text-[var(--color-text-muted)]"
              }`}
              title={node.label}
            >
              {rowLabel(node)}
            </span>
            <span
              className="ml-auto shrink-0 tabular-nums text-[11px] font-semibold"
              style={{ color: pnlColor(totals.net) }}
            >
              {formatCurrencyPnl(totals.net, currencySymbol)}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-2 pl-4.5 text-[10px] text-[var(--color-text-muted)]">
            <span className="truncate" title={subtitle(node, showBot)}>
              {subtitle(node, showBot)}
            </span>
            {/* Volume is what tells two similarly-profitable configs apart at a
                glance, so it rides beside the PnL. */}
            <span className="ml-auto shrink-0 tabular-nums">
              {formatCurrencyVolume(totals.volume, currencySymbol)}
            </span>
          </div>
        </button>
        {/* The row's own verb, outside the button that selects it: stopping a
            bot and reporting on it are two different intents, and one click
            must not be able to mean either. */}
        {action && <div className="flex shrink-0 items-center pr-1.5">{action}</div>}
      </div>
      {isOpen &&
        node.children.map((child) => (
          <ScopeRow
            key={child.id}
            {...{ activeId, open, showBot, onSelect, onToggleOpen, cv, currencySymbol, now, compact, renderAction }}
            node={child}
            depth={depth + 1}
          />
        ))}
    </>
  );
}

/**
 * The scope picker: a row per bot when the tree groups by one, a row per
 * controller, and one per executor under that.
 *
 * It is a picker over a `PerfNode` tree rather than a list of controllers, so a
 * controller and an executor are the same row drawn at a different depth —
 * which is the whole reason the two can be described by the same strip and the
 * same chart (FEAT-086).
 *
 * The **root is not drawn**. It used to be the first row: an "All controllers"
 * card carrying the fleet's PnL and volume, permanently at the top of the list.
 * A row is a thing you pick out of a list of comparable things, and the fleet
 * is not comparable with a controller — it is the sum of them, so it read as a
 * ranked entry that always won, took the widest slot on the narrowest column,
 * and pushed the rows the reader actually came to compare down the page. It is
 * a *button* above the list now (see `PerfBrowser`), which is the affordance it
 * always was: one click to report on everything, with no card spent on it.
 * `root.id` is still the scope that button selects and the id the arrow keys
 * walk to first — only its row is gone.
 */
export function ScopeTree({
  root,
  activeId,
  open,
  showBot = true,
  onSelect,
  onToggleOpen,
  cv,
  currencySymbol,
  now,
  compact = false,
  renderAction,
}: {
  root: PerfNode;
  activeId: string;
  open: Set<string>;
  /** Whether a controller row names its bot — pointless when only one is in scope. */
  showBot?: boolean;
  onSelect: (id: string) => void;
  onToggleOpen: (id: string) => void;
  cv: ConvertQuote;
  currencySymbol: string;
  now: number;
  compact?: boolean;
  /**
   * The verb a row carries beside its name, if any — today, Stop on a bot row.
   *
   * A render prop rather than a `onStopBot` callback: what a bot row's button
   * says depends on that bot's state (armed, stopping, failed), and that state
   * belongs to the browser that owns the mutation, not to a picker whose job is
   * to draw a tree.
   */
  renderAction?: (node: PerfNode) => ReactNode;
}) {
  // An empty tree is a real answer — every filter ticked off, or a window with
  // nothing in it — and it has to say so, or the sidebar reads as still loading.
  if (root.children.length === 0) {
    return compact ? null : (
      <p className="px-3 py-4 text-center text-[10px] text-[var(--color-text-muted)]">
        Nothing in scope.
      </p>
    );
  }

  return (
    <>
      {root.children.map((child) => (
        <ScopeRow
          key={child.id}
          node={child}
          depth={0}
          activeId={activeId}
          open={open}
          showBot={showBot}
          onSelect={onSelect}
          onToggleOpen={onToggleOpen}
          cv={cv}
          currencySymbol={currencySymbol}
          now={now}
          compact={compact}
          renderAction={renderAction}
        />
      ))}
    </>
  );
}
