import type { TokenUsage } from "@/lib/api";
import { formatCurrency } from "@/lib/formatters";
import { CONTEXT_WARN_RATIO, costDisplay, formatTokens } from "@/lib/usage";

const COST_TITLE = "≈ API cost; a Claude subscription is not billed per token";

/**
 * What this conversation has cost so far, on one muted line (FEAT-120).
 *
 * `38.2k tokens · context 45k / 200k · ≈ $0.41`. Every part is only as
 * specific as the backend could be: the context reading appears once one was
 * reported and gains its window only when the window is known, and the cost is
 * hidden where nothing could be priced (a local model) and marked `≥` where
 * only some of it could. Hidden entirely until a turn has been measured, so a
 * conversation older than the measurement shows nothing rather than a zero.
 */
export function ConversationUsage({ usage }: { usage?: TokenUsage }) {
  if (!usage || usage.total_tokens <= 0) return null;

  const cost = costDisplay(usage);
  const used = usage.context_used;
  const size = usage.context_size;
  // The moment `/compact` becomes worth knowing about.
  const nearlyFull = used != null && size != null && size > 0 && used / size > CONTEXT_WARN_RATIO;
  const breakdown = [
    `in ${usage.input_tokens.toLocaleString()}`,
    `out ${usage.output_tokens.toLocaleString()}`,
    `cache read ${usage.cache_read_tokens.toLocaleString()}`,
    `cache write ${usage.cache_write_tokens.toLocaleString()}`,
  ].join(" · ");

  return (
    <div
      className="flex flex-wrap items-center justify-end gap-x-1.5 border-b border-[var(--chat-rule)] px-4 py-1 text-[11px] tabular-nums text-[var(--color-text-muted)]"
      data-testid="conversation-usage"
    >
      <span title={`${breakdown} tokens`}>{formatTokens(usage.total_tokens)} tokens</span>
      {used != null && (
        <>
          <span aria-hidden>·</span>
          <span
            className={nearlyFull ? "text-[var(--color-yellow)]" : undefined}
            title={
              nearlyFull
                ? "The context window is filling up — /compact summarises the chat into a fresh one"
                : "How much of the model's context window the last answer occupied"
            }
          >
            context {formatTokens(used)}
            {size != null && ` / ${formatTokens(size)}`}
          </span>
        </>
      )}
      {cost && (
        <>
          <span aria-hidden>·</span>
          <span title={COST_TITLE}>
            {cost.lowerBound ? "≥ " : "≈ "}
            {formatCurrency(usage.cost_usd)}
          </span>
        </>
      )}
    </div>
  );
}
