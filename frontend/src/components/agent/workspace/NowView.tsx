import { AlertTriangle, Clock, MessageSquareQuote } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { SessionCanvasPanel } from "@/components/agent/AgentSessionContent";
import { DeploymentLedger } from "@/components/agent/lab/DeploymentLedger";
import type { WorkspaceAlert } from "@/components/agent/workspace/views";
import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import type { RunningInstance } from "@/lib/api";
import type { Decision } from "@/lib/parse-agent";

/**
 * What this agent is doing, right now (FEAT-103).
 *
 * The one genuinely new body in the workspace, and it fetches nothing the rest
 * of the screen was not already fetching — it composes what is on disk in the
 * order a person actually asks for it:
 *
 * 1. **What needs you.** Derived from the run's own deeds and journal, not
 *    polled from anywhere: a failed action, a deploy the ledger never recorded,
 *    a tick that is late. The rules are pure, in `views.ts`.
 * 2. **What it last decided, in full.** The newest `## Decisions` entry, as
 *    markdown rather than as one truncated line of plain text. This is the
 *    single biggest legibility win in the feature and it costs no new query.
 * 3. **What it put into the world.** The deployment ledger for this run
 *    (FEAT-100), with its link into the fleet (FEAT-101).
 * 4. **When it goes again**, and the questions it has left open on its canvas.
 *
 * Before this, an agent opened on its `AGENT.md` — a document that does not
 * change while a loop runs — and the last thing it said was one truncated line
 * on a page two navigations away.
 */
export function NowView({
  slug,
  sslug,
  sessionNum,
  alerts,
  decisions,
  deployments,
  instance,
  onOpenTick,
}: {
  slug: string;
  sslug: string;
  /** The session in scope, or 0 when this strategy has no session run. */
  sessionNum: number;
  alerts: WorkspaceAlert[];
  /** The run's decisions, newest last. */
  decisions: Decision[];
  deployments: React.ComponentProps<typeof DeploymentLedger>["rows"];
  /** The live engine, when there is one. */
  instance: RunningInstance | null;
  /** An alert, or the decision's own tick badge, is an address into a tick. */
  onOpenTick: (tick: number) => void;
}) {
  const now = useSeconds(instance?.status === "running");
  const last = decisions[decisions.length - 1] ?? null;

  const dueIn =
    instance && instance.last_tick_at > 0 && instance.frequency_sec > 0
      ? instance.last_tick_at + instance.frequency_sec - now / 1000
      : null;

  return (
    <div className="space-y-4">
      {alerts.length > 0 && (
        <div data-now-alerts className="space-y-2">
          {alerts.map((alert) => (
            <Alert key={alert.kind} alert={alert} onOpenTick={onOpenTick} />
          ))}
        </div>
      )}

      {/* What it last decided, whole. Through the chat's own markdown renderer,
          because a model writes bold, lists and tables and the reader was
          getting the asterisks and the pipes. */}
      <div
        data-now-decision
        className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4"
      >
        <h3 className="mb-3 flex flex-wrap items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <MessageSquareQuote className="h-3.5 w-3.5" /> Last decision
          {last && last.tick > 0 && (
            <button
              type="button"
              onClick={() => onOpenTick(last.tick)}
              className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 font-mono text-[10px] normal-case tracking-normal text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-primary)]"
              title="Read the whole tick this came from"
            >
              #{last.tick}
            </button>
          )}
          {last?.time && (
            <span className="text-[10px] font-medium normal-case tracking-normal text-[var(--color-text-muted)]">
              {last.time}
            </span>
          )}
        </h3>
        {last ? (
          <>
            <div className="chat-markdown text-sm leading-relaxed text-[var(--color-text)]">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {last.action}
              </ReactMarkdown>
            </div>
            {last.reasoning && (
              <div className="chat-markdown mt-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                  {last.reasoning}
                </ReactMarkdown>
              </div>
            )}
            {last.riskNote && (
              <span className="mt-2 inline-block rounded-full border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[10px] font-bold uppercase text-amber-400">
                {last.riskNote}
              </span>
            )}
          </>
        ) : (
          <p className="text-xs text-[var(--color-text-muted)]">
            {sessionNum > 0
              ? "This run has not decided anything yet."
              : "This strategy has not run yet."}
          </p>
        )}
      </div>

      {/* What it put into the world (FEAT-100), read from the same response the
          money views fold — so the two can never disagree. */}
      <DeploymentLedger
        rows={deployments}
        runKey={`${slug}.${sslug}`}
        sessionNum={sessionNum || undefined}
      />

      <div className="flex flex-wrap items-center gap-2 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-2.5 text-xs text-[var(--color-text-muted)]">
        <Clock className="h-3.5 w-3.5" />
        {dueIn === null ? (
          <span>Nothing is looping.</span>
        ) : dueIn > 0 ? (
          <span data-now-countdown className="font-mono">
            Next tick in {countdown(dueIn)}
          </span>
        ) : (
          <span data-now-countdown className="font-mono text-amber-400">
            Overdue {countdown(-dueIn)}
          </span>
        )}
      </div>

      {/* The questions it has left open for itself — nothing at all when it has
          written none, which is the honest answer for most runs. */}
      {sessionNum > 0 && (
        <SessionCanvasPanel slug={slug} sslug={sslug} sessionNum={sessionNum} />
      )}
    </div>
  );
}

function Alert({
  alert,
  onOpenTick,
}: {
  alert: WorkspaceAlert;
  onOpenTick: (tick: number) => void;
}) {
  const failed = alert.kind === "failed";
  const tick = alert.tick;
  return (
    <div
      data-alert={alert.kind}
      className={`flex items-start gap-2 rounded-lg border p-3 text-xs ${
        failed
          ? "border-red-500/30 bg-red-500/5 text-red-300"
          : "border-amber-500/30 bg-amber-500/5 text-amber-300"
      }`}
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span className="min-w-0 flex-1">{alert.text}</span>
      {tick !== undefined && (
        <button
          type="button"
          onClick={() => onOpenTick(tick)}
          className="shrink-0 rounded border border-[currentColor]/30 px-1.5 py-0.5 font-mono text-[10px] transition-opacity hover:opacity-80"
        >
          Open #{tick}
        </button>
      )}
    </div>
  );
}
