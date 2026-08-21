import { Loader2, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";

import { shouldAskConsent, useSetTelemetryLevel, useTelemetry } from "@/hooks/useTelemetry";
import type { TelemetryLevel } from "@/lib/api";

/**
 * The dashboard's half of the consent prompt.
 *
 * A Telegram install gets asked once, next to the boot notification, because
 * that is the one moment the admin is already looking. A local-mode install has
 * no bot to be asked through, so the equivalent moment is the first dashboard
 * it opens — which is this strip.
 *
 * There is no dismiss, because both buttons are answers and one click retires
 * it forever; a "later" would only add a way to be asked again. It renders for
 * nobody else: not for a non-admin seat, not once answered on either surface,
 * and not when `CONDOR_TELEMETRY` has already decided.
 */
export function TelemetryConsentBanner() {
  const { data } = useTelemetry();
  const mutation = useSetTelemetryLevel();

  if (!shouldAskConsent(data) || !data) return null;

  const { disclosure } = data;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-border)] bg-[var(--color-primary)]/10 px-4 py-2">
      <ShieldCheck className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />

      {/* `min-w-0` so the copy wraps inside its own column instead of pushing
          the buttons onto a row of their own. */}
      <p className="min-w-0 flex-1 text-xs leading-relaxed text-[var(--color-text)]">
        <strong className="font-semibold">{disclosure.headline}</strong>{" "}
        <span className="text-[var(--color-text-muted)]">{disclosure.optional}</span>{" "}
        <Link
          to="/settings?tab=privacy"
          className="whitespace-nowrap underline underline-offset-2 hover:text-[var(--color-primary)]"
        >
          What is never sent
        </Link>
      </p>

      <div className="flex shrink-0 items-center gap-2">
        {mutation.isPending && (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-text-muted)]" />
        )}
        {disclosure.options.map((option, index) => (
          <button
            key={option.level}
            disabled={mutation.isPending}
            onClick={() => mutation.mutate(option.level as TelemetryLevel)}
            className={`whitespace-nowrap rounded-md px-3 py-1 text-xs font-medium transition-colors disabled:opacity-60 ${
              index === 0
                ? "bg-[var(--color-primary)] text-white hover:opacity-90"
                : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
            }`}
          >
            {option.label}
          </button>
        ))}
      </div>

      {mutation.isError && (
        <p className="w-full text-xs text-[var(--color-red)]">
          Could not save that. {(mutation.error as Error).message}
        </p>
      )}
    </div>
  );
}
