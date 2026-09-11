import { AlertTriangle, Power, Repeat, Zap } from "lucide-react";

import { ModeBadge } from "@/components/agent/ModeBadge";
import { useSeconds } from "@/hooks/useSeconds";
import { countdown } from "@/lib/agent-attribution";
import type { RunningInstance } from "@/lib/api";

/**
 * What the loop is doing, said out loud.
 *
 * A strategy *is* a loop — a playbook the agent re-reads on a cadence — and
 * every surface that showed one used to describe it as a status word and a row
 * of PnL. "IDLE", "running": the two states a loop can be in, with nothing
 * about the mechanism between them. A reader could not tell whether a running
 * loop had ticked once or four hundred times, when the next tick was due, what
 * the last one actually did, or that the last one had failed — and an idle one
 * said nothing at all about what pressing Start would set in motion.
 *
 * The engine has computed all of it since it was written (`get_status()`); it
 * was the wire model that dropped it. So this is mostly a rendering problem,
 * and the shape it wants is a **pulse**: a cadence, a beat, and the last thing
 * the beat produced.
 *
 * ## Three statements, in order of how much they can be trusted
 *
 * The **spine** is configuration: mode, cadence, tick count against its bound.
 * True whether or not anything has run.
 *
 * The **beat** is the clock: which ticks have happened, and how far through the
 * gap we are to the next one. Only meaningful while running, and it says
 * *overdue* rather than printing a negative number when a tick runs long — the
 * same judgement `loopFacts` makes for the fleet band, because a slow tick is a
 * real state and a negative countdown reads as a bug in the page.
 *
 * The **deed** is what the last tick did: one mutating tool call from
 * `actions.jsonl`, and under it the agent's own narration. Kept apart for the
 * reason the fleet band keeps them apart — one is what happened, the other is
 * what the model wrote about it — and the deed is the one that can be clicked,
 * because a deed has a tick and a tick has a snapshot.
 *
 * ## Why the ticks are a strip and not a number
 *
 * "Ticks: 14" is a fact you read; a row of beats is a thing you point at. Each
 * beat is the tick's snapshot, so the strip is also the fastest way into what
 * the loop was thinking at any moment of the session — the walk that otherwise
 * costs a session table, a reviewer and a snapshot picker. It shows the last
 * dozen and no more: this is a pulse, not a history, and the history has a
 * reviewer of its own.
 */

/** The beats we draw. Enough to read a rhythm, few enough to stay a strip. */
const MAX_BEATS = 12;

export function LoopPulse({
  instance,
  status,
  config,
  onOpenTick,
  onSetRestartOnBoot,
  settingRestartOnBoot = false,
}: {
  /** The live session, when there is one. */
  instance: RunningInstance | null;
  /** The strategy's status when no instance is running: idle/stopped/paused. */
  status: string;
  /** The strategy's stored config, for what an idle loop *would* do. */
  config: Record<string, unknown>;
  /** Land on this tick's snapshot. Absent = the beats are not clickable. */
  onOpenTick?: (sessionNum: number, tick: number) => void;
  /**
   * Flip "resume after restart". Absent = the chip is read-only, which is what
   * a host that cannot write the strategy wants.
   */
  onSetRestartOnBoot?: (enabled: boolean) => void;
  /** Whether that write is in flight, so the chip can say so rather than lie. */
  settingRestartOnBoot?: boolean;
}) {
  const running = instance?.status === "running";
  const now = useSeconds(running);

  // An idle loop still has a cadence — the one it will start on. Saying "every
  // 60s" before anything runs is what makes Start a legible button rather than
  // a leap.
  const frequency = instance?.frequency_sec ?? Number(config.frequency_sec ?? 60);
  const mode = instance?.execution_mode ?? (config.execution_mode as string) ?? "loop";
  const ticks = instance?.tick_count ?? 0;
  const maxTicks = instance?.max_ticks ?? Number(config.max_ticks ?? 0);
  const lastTickAt = instance?.last_tick_at ?? 0;

  // How far through the gap between beats we are. Only while running and only
  // after a first tick: `0 + frequency` is 1970, and a bar filled from 1970 is
  // a bar pinned at 100% that means nothing.
  const elapsed = running && lastTickAt > 0 ? now / 1000 - lastTickAt : null;
  const dueIn = elapsed === null ? null : frequency - elapsed;
  const progress =
    elapsed === null ? 0 : Math.max(0, Math.min(1, elapsed / Math.max(1, frequency)));
  const overdue = dueIn !== null && dueIn <= 0;

  // The last MAX_BEATS ticks, oldest first. A tick number is its snapshot's
  // name, so these are addresses, not decoration.
  const beats: number[] = [];
  for (let t = Math.max(1, ticks - MAX_BEATS + 1); t <= ticks; t++) beats.push(t);

  const did = instance?.last_did ?? null;
  const said = instance?.last_action ?? "";
  const error = instance?.last_error ?? "";

  const dotClass = running
    ? "bg-emerald-400"
    : status === "paused"
      ? "bg-amber-400"
      : "bg-[var(--color-text-muted)]/40";

  return (
    <div
      data-testid="loop-pulse"
      className={`rounded-lg border p-4 ${
        running
          ? "border-emerald-500/25 bg-emerald-500/[0.03]"
          : "border-[var(--color-border)] bg-[var(--color-surface)]"
      }`}
    >
      {/* ── The spine: what this loop is, running or not ── */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <span className="flex items-center gap-1.5 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
          <Repeat className={`h-3.5 w-3.5 ${running ? "text-emerald-400" : ""}`} />
          Loop
        </span>
        <span className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 rounded-full ${dotClass} ${running ? "animate-pulse" : ""}`} />
          <span className="text-xs capitalize text-[var(--color-text)]">
            {instance?.status || status || "idle"}
          </span>
        </span>
        <ModeBadge mode={mode} />
        <span className="font-mono text-xs tabular-nums text-[var(--color-text-muted)]">
          every {countdown(frequency)}
        </span>
        <span className="font-mono text-xs tabular-nums text-[var(--color-text-muted)]">
          tick {ticks}
          <span className="opacity-50">/{maxTicks > 0 ? maxTicks : "∞"}</span>
        </span>
        {instance && (
          <span className="font-mono text-xs tabular-nums text-[var(--color-text-muted)]">
            session {instance.session_num}
          </span>
        )}
        {/* Whether the loop survives a Condor restart belongs on the spine and
            not in the start dialog alone: it is a property of the loop, true
            or false whether or not anything is running, and the moment you
            want to set it is while watching one run. Pushed to the end of the
            row so the cadence and the tick count — read far more often — keep
            their place. */}
        <RestartChip
          enabled={!!config.restart_on_boot}
          onChange={onSetRestartOnBoot}
          pending={settingRestartOnBoot}
        />
      </div>

      {/* ── The beat: which ticks happened, and the gap to the next ── */}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <div className="flex items-center gap-1">
          {beats.length === 0 ? (
            // An empty strip, not a hidden one: the shape of what is coming is
            // itself the answer to "what does Start do".
            <span className="text-xs text-[var(--color-text-muted)]">
              {running ? "first tick pending…" : "no ticks yet"}
            </span>
          ) : (
            beats.map((t) => (
              <BeatDot
                key={t}
                tick={t}
                latest={t === ticks}
                running={running}
                failed={t === ticks && !!error}
                onClick={
                  onOpenTick && instance
                    ? () => onOpenTick(instance.session_num, t)
                    : undefined
                }
              />
            ))
          )}
        </div>

        {running && (
          <div className="flex min-w-[9rem] flex-1 items-center gap-2">
            <div className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--color-border)]">
              <div
                className={`h-full rounded-full transition-[width] duration-1000 ease-linear ${
                  overdue ? "bg-amber-400" : "bg-emerald-400"
                }`}
                style={{ width: `${progress * 100}%` }}
              />
            </div>
            <span className="shrink-0 font-mono text-[11px] tabular-nums text-[var(--color-text-muted)]">
              {dueIn === null
                ? "—"
                : overdue
                  ? `overdue ${countdown(-dueIn)}`
                  : `next in ${countdown(dueIn)}`}
            </span>
          </div>
        )}
      </div>

      {/* ── The deed: what the last tick did, and what it said about it ── */}
      {(did || said || error) && (
        <div className="mt-3 space-y-1 border-t border-[var(--color-border)]/50 pt-2.5">
          {did && (
            <button
              type="button"
              disabled={!onOpenTick || !instance}
              onClick={() =>
                onOpenTick && instance && onOpenTick(instance.session_num, did.tick)
              }
              title={
                onOpenTick
                  ? `Open tick #${did.tick}${did.error ? ` — ${did.error}` : ""}`
                  : did.summary
              }
              className={`flex w-full items-center gap-1.5 truncate text-left text-xs ${
                did.ok ? "text-[var(--color-text)]" : "text-amber-500"
              } ${onOpenTick && instance ? "hover:text-[var(--color-primary)]" : "cursor-default"}`}
            >
              <Zap className="h-3 w-3 shrink-0 opacity-60" />
              <span className="shrink-0 font-mono tabular-nums opacity-60">#{did.tick}</span>
              <span className="truncate">
                {did.summary}
                {!did.ok && ` — failed${did.error ? `: ${did.error}` : ""}`}
              </span>
            </button>
          )}
          {said && (
            <p
              className="truncate text-xs italic text-[var(--color-text-muted)]"
              title={said}
            >
              “{said}”
            </p>
          )}
          {error && (
            <p className="flex items-start gap-1.5 text-xs text-[var(--color-red)]">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0" />
              <span className="break-words">{error}</span>
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Whether this loop comes back by itself after Condor restarts.
 *
 * Off is the default and off is drawn *plainly* rather than in a warning
 * colour: not resuming is the safe state, and a muted chip is how the spine
 * says "this is a setting" instead of "this is a problem". On is emerald, the
 * same green every other live thing on this card uses, because an armed loop
 * is a live fact about it.
 *
 * It always states its own meaning in words. The whole reason this control
 * exists is a reader who restarted Condor and was surprised to find the loop
 * stopped, so a chip they have to hover to decode would be the same failure in
 * a smaller font.
 */
function RestartChip({
  enabled,
  onChange,
  pending,
}: {
  enabled: boolean;
  onChange?: (enabled: boolean) => void;
  pending: boolean;
}) {
  const label = enabled ? "resumes on restart" : "stops on restart";
  const tone = enabled
    ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-500"
    : "border-[var(--color-border)] text-[var(--color-text-muted)]";

  if (!onChange) {
    return (
      <span
        className={`flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] ${tone}`}
      >
        <Power className="h-3 w-3" />
        {label}
      </span>
    );
  }

  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={pending}
      onClick={() => onChange(!enabled)}
      title={
        enabled
          ? "Condor restarts this loop in a fresh session after it restarts. Click to turn off."
          : "This loop stays stopped after Condor restarts. Click to have it resume."
      }
      className={`flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] transition-colors hover:border-[var(--color-primary)]/50 hover:text-[var(--color-primary)] disabled:opacity-50 ${tone}`}
    >
      <Power className="h-3 w-3" />
      {pending ? "saving…" : label}
    </button>
  );
}

/**
 * One tick, as a thing you can point at.
 *
 * The newest beat is hollow while the loop runs — that tick is the one still
 * being decided, and drawing it identically to the twelve settled ones behind
 * it claims a result the session does not have yet.
 */
function BeatDot({
  tick,
  latest,
  running,
  failed,
  onClick,
}: {
  tick: number;
  latest: boolean;
  running: boolean;
  failed: boolean;
  onClick?: () => void;
}) {
  const live = latest && running;
  return (
    <button
      type="button"
      disabled={!onClick}
      onClick={onClick}
      title={`Tick #${tick}${failed ? " — errored" : ""}`}
      aria-label={`Tick ${tick}`}
      className={`h-2.5 w-2.5 rounded-full border transition-transform ${
        onClick ? "hover:scale-150" : "cursor-default"
      } ${
        failed
          ? "border-[var(--color-red)] bg-[var(--color-red)]"
          : live
            ? "animate-pulse border-emerald-400 bg-transparent"
            : "border-transparent bg-emerald-400/70"
      }`}
    />
  );
}
