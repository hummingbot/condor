import {
  type Signal,
  type SignalGroup,
  type SignalState,
} from "./fleet-diagnostics";

// ── Rendering ──

const STATE_STYLE: Record<SignalState, string> = {
  // Three visually distinct treatments, and `unknown` is deliberately neither
  // of the others: muted, italic and dashed, so it cannot be mistaken for the
  // solid green of a signal that was actually read.
  ok: "border-[var(--color-green)]/40 bg-[var(--color-green)]/5 text-[var(--color-green)]",
  flagged: "border-[var(--color-red)]/50 bg-[var(--color-red)]/10 text-[var(--color-red)]",
  unknown:
    "border-dashed border-[var(--color-border)] bg-transparent text-[var(--color-text-muted)] italic",
};

function SignalRow({ signal }: { signal: Signal }) {
  return (
    <div
      data-signal={signal.label}
      data-state={signal.state}
      className={`rounded border px-2 py-1.5 ${STATE_STYLE[signal.state]}`}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-[11px] font-medium text-[var(--color-text)] truncate" title={signal.label}>
          {signal.label}
        </span>
        <span className="shrink-0 text-[11px] font-semibold tabular-nums">{signal.value}</span>
      </div>
      <div className="mt-0.5 text-[10px] leading-snug text-[var(--color-text-muted)]">
        {signal.facts.join(" · ")}
      </div>
      {signal.note && (
        <div className="mt-0.5 text-[10px] leading-snug text-[var(--color-text-muted)]/70">
          {signal.note}
        </div>
      )}
    </div>
  );
}

function Group({ title, signals }: { title: string; signals: Signal[] }) {
  const flagged = signals.filter((s) => s.state === "flagged").length;
  return (
    <section data-group={title} className="flex flex-col gap-1">
      <h4 className="flex items-center gap-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        {title}
        {flagged > 0 && (
          <span className="rounded bg-[var(--color-red)]/15 px-1 text-[9px] font-semibold text-[var(--color-red)]">
            {flagged}
          </span>
        )}
      </h4>
      {signals.length === 0 ? (
        <p className="text-[10px] text-[var(--color-text-muted)]">Nothing to read here.</p>
      ) : (
        // Keyed by position as well as label: two venues on two hosts can
        // render the same label, and a duplicate key drops a row silently.
        signals.map((signal, i) => <SignalRow key={`${i}:${signal.label}`} signal={signal} />)
      )}
    </section>
  );
}

/**
 * The five groups, drawn.
 *
 * Takes the folded groups rather than the raw payloads, and that is the point:
 * the host also needs the flagged *count* for the button that opens this
 * drawer, and folding in both places would let the badge and the rows
 * disagree. {@link buildSignalGroups} is the one fold; this is the one
 * rendering of it.
 */
export function FleetDiagnostics({ groups }: { groups: readonly SignalGroup[] }) {
  return (
    <div className="flex h-full min-h-0 flex-col gap-3 overflow-y-auto">
      <p className="text-[10px] leading-snug text-[var(--color-text-muted)]">
        Signals, not logs. A reading this view could not take is drawn as{" "}
        <span className="italic">unknown</span> and never as ok.
      </p>
      {groups.map((group) => (
        <Group key={group.title} title={group.title} signals={group.signals} />
      ))}
    </div>
  );
}
