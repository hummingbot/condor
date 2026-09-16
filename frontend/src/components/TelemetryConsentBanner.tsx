import { Loader2, ShieldCheck } from "lucide-react";
import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";

import {
  shouldAskConsent,
  useMarkTelemetryNoticeShown,
  useSetTelemetryLevel,
  useTelemetry,
} from "@/hooks/useTelemetry";

/**
 * The dashboard's half of the telemetry notice.
 *
 * A Telegram install is told once, next to the boot notification, because that
 * is the one moment the admin is already looking. A local-mode install has no
 * bot to be told through, so the equivalent moment is the first dashboard it
 * opens — which is this strip.
 *
 * It is a notice, not a question: usage summaries are on, here is what they
 * are, here is where to turn them off. Rendering it is the delivery, so the
 * strip posts its own receipt on mount — that, not the click, is what moves an
 * unanswered install off the ping floor. "Got it" only records the answer so
 * the strip retires. It renders for nobody else: not for a non-admin seat, not
 * once answered on either surface, and not when `CONDOR_TELEMETRY` has already
 * decided.
 */
export function TelemetryConsentBanner() {
  const { data } = useTelemetry();
  const setLevel = useSetTelemetryLevel();
  const markShown = useMarkTelemetryNoticeShown();
  const receiptSent = useRef(false);

  const visible = shouldAskConsent(data);
  const needsReceipt = visible && !data?.notice_shown;

  useEffect(() => {
    if (!needsReceipt || receiptSent.current) return;
    receiptSent.current = true;
    markShown.mutate();
  }, [needsReceipt, markShown]);

  if (!visible || !data) return null;

  const { disclosure } = data;

  return (
    <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--color-border)] bg-[var(--color-primary)]/10 px-4 py-2">
      <ShieldCheck className="h-4 w-4 shrink-0 text-[var(--color-primary)]" />

      {/* `min-w-0` so the copy wraps inside its own column instead of pushing
          the buttons onto a row of their own. */}
      <p className="min-w-0 flex-1 text-xs leading-relaxed text-[var(--color-text)]">
        <strong className="font-semibold">{disclosure.headline}.</strong>{" "}
        <span className="text-[var(--color-text-muted)]">{disclosure.summary}</span>{" "}
        <Link
          to="/settings?tab=privacy"
          className="whitespace-nowrap underline underline-offset-2 hover:text-[var(--color-primary)]"
        >
          Turn off or see details
        </Link>
      </p>

      <div className="flex shrink-0 items-center gap-2">
        {setLevel.isPending && (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--color-text-muted)]" />
        )}
        <button
          disabled={setLevel.isPending}
          onClick={() => setLevel.mutate("usage")}
          className="whitespace-nowrap rounded-md bg-[var(--color-primary)] px-3 py-1 text-xs font-medium text-white transition-colors hover:opacity-90 disabled:opacity-60"
        >
          {disclosure.acknowledge}
        </button>
      </div>

      {setLevel.isError && (
        <p className="w-full text-xs text-[var(--color-red)]">
          Could not save that. {(setLevel.error as Error).message}
        </p>
      )}
    </div>
  );
}
