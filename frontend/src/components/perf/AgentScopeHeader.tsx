import { Bot, ExternalLink, Server, Zap } from "lucide-react";
import { type ReactNode } from "react";
import { useNavigate } from "react-router-dom";

import { agentColor } from "@/lib/agentColor";
import { loopFacts, loopStatus, runKeyLabel, type FleetOwner } from "@/lib/agent-attribution";
import { shortBotName } from "@/lib/formatters";
import { useSeconds } from "@/hooks/useSeconds";

import { StatusDot } from "./ScopeTree";

/**
 * What the agent driving this scope is doing right now (FEAT-096).
 *
 * The one thing an agent scope reports that no other scope can: the numbers
 * beside it are the same fold every scope gets, and this says whether the loop
 * that produced them is still alive.
 *
 * It lives in the header block and **not** in the KPI grid, whose fixed tile set
 * is deliberate: a conditional tile resizes the chart as the reader walks the
 * tree, and `CHART_CHROME_PX` is measured against that layout. The header is
 * already variable-height and already holds four mutually exclusive per-scope
 * subjects; this is the fifth.
 *
 * What it says is now two statements, not one (FEAT-097). The **deed** line is
 * a fact: the agent's last mutating tool call, read from the session's
 * `actions.jsonl` — the tool ran, and the row says whether it succeeded. The
 * **words** line under it is the agent's own narration (the journal's
 * `Last action:`, which is `response_text[:100]`), and it is the only thing
 * this band could say before the log existed. Keeping them apart is the point:
 * one is what happened, the other is what the model wrote about it.
 *
 * A session that ran before the log existed has no deed — nothing is
 * backfilled — and reads exactly as it did before: the words line alone.
 */

interface AgentScopeHeaderProps {
  /** The run key of the scope. Named even when the map no longer holds it. */
  runKey: string;
  owner?: FleetOwner;
  /**
   * The declared legacy bases actually present in this scope.
   *
   * An agent scope folds its bots' *whole* records. Where the strategy deployed
   * every base it owns that is exactly the strategy's own rollup; for a legacy
   * base declared into the namespace it is a superset, and the agent page's
   * session-sliced figure will differ. Saying so is the mitigation — the client
   * has no history to slice with, and inventing one here would be worse.
   */
  legacyBots?: string[];
  /**
   * The single bot this scope also happens to be, when there is one.
   *
   * A fleet the bubbles have narrowed to one agent *and* one bot is both
   * subjects at once, and dropping the bot name for the agent's would lose the
   * more specific of the two — so it rides along as a chip rather than
   * displacing anything.
   */
  botName?: string;
  /** What the generic header would have said underneath: what this scope folds. */
  children?: ReactNode;
}

export function AgentScopeHeader({
  runKey,
  owner,
  legacyBots = [],
  botName,
  children,
}: AgentScopeHeaderProps) {
  const navigate = useNavigate();
  const live = owner?.live ?? null;
  const status = loopStatus(live);
  const running = status === "running";
  const now = useSeconds(running);
  // The reading of the loop is pure and lives in lib/ where a test can reach it
  // (the ARCH-300 split); what stays here is the clock and the markup.
  const facts = loopFacts(live, now);

  // `snapshotTick` lands on that tick's full snapshot, collapsing the main page
  // → agent → session → snapshot walk a tick needs otherwise.
  //
  // A plain URL since FEAT-099. It used to be `location.state` carrying
  // `{ openReviewer, sessionNum, snapshotTick }`, because the reviewer was an
  // overlay with nowhere to put that state — which meant the tick this line
  // names could not be copied, bookmarked or sent to anyone. Putting the state
  // in the URL is the whole point of the Lab, so this is the shortest possible
  // demonstration of it.
  const openSession = (snapshotTick?: number) => {
    if (!owner) return;
    const params = new URLSearchParams({ strategy: owner.strategySlug });
    if (live?.sessionNum) params.set("run", `s${live.sessionNum}`);
    if (snapshotTick) params.set("tick", String(snapshotTick));
    navigate(`/agents/${owner.agentSlug}/runs?${params}`);
  };

  const did = live?.lastDid ?? null;

  return (
    <div className="min-w-0">
      <h2 className="flex items-center gap-2 text-sm font-semibold">
        <Bot className="h-3.5 w-3.5 shrink-0" style={{ color: agentColor(runKey) }} />
        <span
          className="truncate"
          title={
            owner
              ? `${owner.agentName || owner.agentSlug} / ${owner.strategyName || owner.strategySlug}`
              : runKey
          }
        >
          {runKeyLabel(runKey)}
        </span>
        {botName && (
          <span
            className="flex shrink-0 items-center gap-1 rounded bg-[var(--color-surface)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)] border border-[var(--color-border)]/50"
            title={botName}
          >
            <Server className="h-2.5 w-2.5" />
            {shortBotName(botName)}
          </span>
        )}
        <span className="flex shrink-0 items-center gap-1.5 font-normal">
          <StatusDot status={status} />
          <span className="text-xs capitalize text-[var(--color-text-muted)]">{status}</span>
        </span>
        {facts.length > 0 && (
          <span className="shrink-0 text-[10px] tabular-nums text-[var(--color-text-muted)]">
            {facts.join(" · ")}
          </span>
        )}
        {owner && (
          <button
            type="button"
            onClick={() => openSession()}
            className="flex shrink-0 items-center gap-1 rounded border border-[var(--color-border)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)]"
            title={
              live
                ? `Open session ${live.sessionNum} of ${owner.strategySlug}`
                : `Open ${owner.strategySlug}`
            }
          >
            Open session
            <ExternalLink className="h-2.5 w-2.5" />
          </button>
        )}
      </h2>
      {/* What the agent last *did* — a tool call that ran, not a paraphrase.
          Above the words because the deed is the fact and the narration is the
          commentary on it. Clicking lands on that tick's snapshot. */}
      {did && (
        <button
          type="button"
          onClick={() => openSession(did.tick)}
          disabled={!owner}
          title={
            owner
              ? `Open tick #${did.tick}${did.error ? ` — ${did.error}` : ""}`
              : did.summary
          }
          className={`flex max-w-full items-center gap-1 truncate text-left text-[10px] ${
            did.ok ? "text-[var(--color-text-muted)]" : "text-amber-500/90"
          } ${owner ? "hover:text-[var(--color-primary)]" : "cursor-default"}`}
        >
          <Zap className="h-2.5 w-2.5 shrink-0" />
          <span className="shrink-0 font-mono tabular-nums">#{did.tick}</span>
          <span className="truncate">
            {did.summary}
            {!did.ok && ` — failed${did.error ? `: ${did.error}` : ""}`}
          </span>
        </button>
      )}
      {/* What the agent last *said*. Truncated with the whole of it in `title`,
          the same idiom the unpriced note uses — a one-line summary that hides
          its own tail is worse than one that admits to having one. */}
      {live?.lastAction && (
        <span
          className="block truncate text-[10px] text-[var(--color-text-muted)] italic"
          title={live.lastAction}
        >
          “{live.lastAction}”
        </span>
      )}
      {live?.lastError && (
        <span className="block truncate text-[10px] text-amber-500/90" title={live.lastError}>
          Last tick errored: {live.lastError}
        </span>
      )}
      {legacyBots.length > 0 && (
        <span
          className="block truncate text-[10px] text-amber-500/90"
          title={`These were configured before this strategy's namespace existed, so this scope folds their whole record: ${legacyBots.join(", ")}`}
        >
          includes trading from before this strategy adopted {legacyBots.join(", ")}
        </span>
      )}
      {children}
    </div>
  );
}
