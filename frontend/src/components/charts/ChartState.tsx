/**
 * What a chart's box says when there is no chart in it.
 *
 * "Loading", "nothing recorded" and "the request failed" are three different
 * facts, and a host that only handles the first two renders a failed request
 * as "no history" — an outage reading as an empty bot (FP-10). One component,
 * so every chart box says the three the same way and the failed one always
 * comes with a way to ask again.
 */
export function ChartState({
  kind,
  message,
  onRetry,
  height,
  className = "",
}: {
  kind: "loading" | "empty" | "error";
  message: string;
  /** Offered on an error only: asking again is the one thing left to do. */
  onRetry?: () => void;
  /** Fixed pixel height; omit to fill the parent (`h-full`). */
  height?: number;
  className?: string;
}) {
  return (
    <div
      data-chart-state={kind}
      role={kind === "error" ? "alert" : "status"}
      className={`flex flex-col items-center justify-center gap-2 px-6 ${height === undefined ? "h-full" : ""} ${className}`}
      style={height === undefined ? undefined : { height }}
    >
      <div
        className={`flex max-w-sm items-center gap-2 text-center text-xs ${
          kind === "error" ? "text-[var(--color-red)]" : "text-[var(--color-text-muted)]"
        }`}
      >
        {kind === "loading" && (
          <span className="h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-current border-t-transparent" />
        )}
        {message}
      </div>
      {kind === "error" && onRetry && (
        <button
          onClick={onRetry}
          className="rounded border border-[var(--color-border)] px-2 py-0.5 text-[11px] text-[var(--color-text)] hover:bg-[var(--color-surface-hover)]"
        >
          Retry
        </button>
      )}
    </div>
  );
}
