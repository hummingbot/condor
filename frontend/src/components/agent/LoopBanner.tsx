import { ChevronRight, Repeat } from "lucide-react";

import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import type { StrategySummary } from "@/lib/api";

/**
 * Whether this agent is looping, said before anything else about it.
 *
 * Opening an agent in the chat's panel used to land on Brain — its AGENT.md,
 * which is the one thing about an agent that never changes. Finding out
 * whether it was *running* cost three clicks (Strategies → the card → the
 * workbench), and the answer was time-critical in a way the identity text
 * never is: a loop with real money in it is the reason you opened the panel.
 *
 * So the live loops come out of the sections and sit above them, as a strip
 * that is only there when there is something to say. It is deliberately not
 * another section — a section is a place you go, and this is a fact you should
 * not have to go anywhere for.
 *
 * The page host does not get one: `/agents/:slug` already opens on Now and
 * carries `LoopBar` across the top, and two strips saying the same thing is how
 * they start disagreeing. This is the chat pane's equivalent, at a width that
 * cannot hold `LoopBar`'s two pickers and tick spine.
 *
 * ## What it says, and what it deliberately does not
 *
 * Per running strategy: its name, the cadence, and the countdown to the next
 * tick. Not PnL — that is the card's job one click in, and a number here would
 * be a fourth place the same money is quoted. The whole row is the door to the
 * workbench, because "there is a loop running" and "take me to it" are one
 * thought.
 */
export function LoopBanner({
  strategies,
  onOpenStrategy,
}: {
  strategies: readonly StrategySummary[];
  /** Hand this strategy to the host's own surface — the pane's workbench. */
  onOpenStrategy: (sslug: string) => void;
}) {
  // A strategy with a live engine, whatever its own status field says: the
  // instance is the running thing, and `status` is the strategy's summary of it.
  const live = strategies.filter(
    (s) => s.status === "running" || s.status === "paused" || s.instances.length > 0,
  );

  // One clock for the strip, and only while something is actually ticking. A
  // paused loop has no next tick to count down to.
  const ticking = live.some((s) =>
    s.instances.some((i) => i.status === "running"),
  );
  const nowSec = useSeconds(ticking) / 1000;

  if (live.length === 0) return null;

  return (
    <div
      data-testid="loop-banner"
      className="shrink-0 border-b border-[var(--color-border)] bg-emerald-500/[0.04]"
    >
      {live.map((strategy) => {
        const instance =
          strategy.instances.find((i) => i.status === "running") ??
          strategy.instances[0] ??
          null;
        const paused = (instance?.status ?? strategy.status) === "paused";
        const dueIn =
          instance && instance.last_tick_at > 0 && !paused
            ? instance.last_tick_at + instance.frequency_sec - nowSec
            : null;

        return (
          <button
            key={strategy.slug}
            type="button"
            onClick={() => onOpenStrategy(strategy.slug)}
            title={`Open ${strategy.name}`}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs transition-colors hover:bg-[var(--color-surface-hover)]"
          >
            <Repeat
              className={`h-3.5 w-3.5 shrink-0 ${
                paused ? "text-amber-400" : "text-emerald-400"
              }`}
            />
            <span
              className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                paused ? "bg-amber-400" : "animate-pulse bg-emerald-400"
              }`}
            />
            <span className="truncate font-medium text-[var(--color-text)]">
              {strategy.name}
            </span>
            <span className="shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-muted)]">
              tick {instance?.tick_count ?? strategy.tick_count}
            </span>
            <span className="ml-auto shrink-0 font-mono text-[10px] tabular-nums text-[var(--color-text-muted)]">
              {paused
                ? "paused"
                : dueIn === null
                  ? instance
                    ? "first tick pending…"
                    : "running"
                  : dueIn <= 0
                    ? `overdue ${countdown(-dueIn)}`
                    : `next in ${countdown(dueIn)}`}
            </span>
            <ChevronRight className="h-3 w-3 shrink-0 text-[var(--color-text-muted)]" />
          </button>
        );
      })}
    </div>
  );
}
