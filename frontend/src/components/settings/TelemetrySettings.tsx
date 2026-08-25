import { BarChart3, Check, Loader2, Lock, ShieldCheck, ShieldOff } from "lucide-react";

import { useSetTelemetryLevel, useTelemetry } from "@/hooks/useTelemetry";
import type { TelemetryLevel } from "@/lib/api";

/**
 * What this install shares, and — for the admin — the switch that changes it.
 *
 * Readable by every seat on purpose: anyone using an install should be able to
 * see what it reports, even if only the admin may answer. It is also the one
 * surface a local-mode install has, since there is no Telegram prompt there.
 */
export function TelemetrySettings() {
  const { data, isLoading } = useTelemetry();
  const mutation = useSetTelemetryLevel();

  if (isLoading || !data) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  const { disclosure, level, consent, env_overridden, can_change } = data;
  const locked = env_overridden || !can_change;
  // At `unknown` no option is the answer yet, so none is shown as chosen —
  // the install is at the ping floor, but nobody has said so. `denied` is an
  // answer, and the one it selects is the off row below.
  const chosen = consent === "granted" ? level : consent === "denied" ? "off" : null;
  const offChosen = chosen === "off";

  return (
    <div className="space-y-6">
      <section>
        <h3 className="mb-1 text-sm font-semibold text-[var(--color-text)]">
          <ShieldCheck className="mr-1.5 inline h-4 w-4" />
          {disclosure.headline}
        </h3>
        <div className="space-y-2 text-xs leading-relaxed text-[var(--color-text-muted)]">
          <p>{disclosure.always_on}</p>
          <p>{disclosure.optional}</p>
        </div>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          Never sent
        </h4>
        <ul className="flex flex-wrap gap-1.5">
          {disclosure.never.map((item) => (
            <li
              key={item}
              className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-0.5 text-xs text-[var(--color-text-muted)]"
            >
              {item}
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-[var(--color-text-muted)]">
          This install
        </h4>
        <div className="space-y-1">
          {disclosure.options.map((option) => {
            const active = chosen === option.level;
            return (
              <button
                key={option.level}
                disabled={locked || mutation.isPending}
                onClick={() => mutation.mutate(option.level as TelemetryLevel)}
                className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
                  active
                    ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10"
                    : "border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
                } ${locked ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
              >
                <BarChart3
                  className={`h-4 w-4 shrink-0 ${
                    active
                      ? "text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)]"
                  }`}
                />
                <span className="flex-1 text-sm text-[var(--color-text)]">
                  {option.label}
                </span>
                {active && (
                  <Check className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />
                )}
              </button>
            );
          })}

          {/* Not one of `disclosure.options`: the consent form has no "off"
              button, because ignoring a prompt must not read as a refusal.
              Saying no out loud is a different act, and it needs somewhere to
              be said other than the operator's `.env`. */}
          <button
            disabled={locked || mutation.isPending}
            onClick={() => mutation.mutate("off")}
            className={`flex w-full items-center gap-3 rounded-lg border px-3 py-2 text-left transition-colors ${
              offChosen
                ? "border-[var(--color-primary)] bg-[var(--color-primary)]/10"
                : "border-[var(--color-border)] hover:bg-[var(--color-surface-hover)]"
            } ${locked ? "cursor-not-allowed opacity-60" : "cursor-pointer"}`}
          >
            <ShieldOff
              className={`h-4 w-4 shrink-0 ${
                offChosen ? "text-[var(--color-primary)]" : "text-[var(--color-text-muted)]"
              }`}
            />
            <span className="flex-1 text-sm text-[var(--color-text)]">
              Nothing at all — stop reporting this install
            </span>
            {offChosen && <Check className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />}
          </button>
        </div>

        {offChosen && !env_overridden && (
          <p className="mt-2 text-xs text-[var(--color-text-muted)]">
            This install reports nothing, and stays that way across upgrades.
            Anything already collected was deleted. Pick a level above to turn
            reporting back on.
          </p>
        )}
        {env_overridden && (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-[var(--color-text-muted)]">
            <Lock className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              <code>CONDOR_TELEMETRY</code> is set in this install's environment
              and overrides anything chosen here. Effective level:{" "}
              <strong>{level}</strong>.
            </span>
          </p>
        )}
        {!env_overridden && !can_change && (
          <p className="mt-2 flex items-start gap-1.5 text-xs text-[var(--color-text-muted)]">
            <Lock className="mt-0.5 h-3 w-3 shrink-0" />
            <span>
              Telemetry is install-wide, so only the admin can change it.
              Effective level: <strong>{level}</strong>.
            </span>
          </p>
        )}
        {mutation.isError && (
          <p className="mt-2 text-xs text-[var(--color-red)]">
            Could not save that. {(mutation.error as Error).message}
          </p>
        )}
      </section>

      <p className="text-xs text-[var(--color-text-muted)]">{disclosure.doc}</p>
    </div>
  );
}
