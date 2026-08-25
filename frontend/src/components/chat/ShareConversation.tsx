import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import {
  AlertTriangle,
  Download,
  Loader2,
  Send,
  Share2,
  ShieldCheck,
  Trash2,
} from "lucide-react";

import { useEscapeKey } from "@/hooks/useEscapeKey";
import { api, type RedactionCounts, type SharePreview } from "@/lib/api";

/** Human names for the scrubber's categories, in the order a sentence wants
 *  them. Anything the backend adds later that is not listed falls back to its
 *  own key, so a new category shows up as a number rather than disappearing. */
const LABELS: Record<string, [string, string]> = {
  known_key: ["API key", "API keys"],
  known_server: ["server name", "server names"],
  known_url: ["server address", "server addresses"],
  known_wallet: ["wallet address", "wallet addresses"],
  known_user: ["account name", "account names"],
  known_path: ["file path", "file paths"],
  evm_addr: ["wallet address", "wallet addresses"],
  sol_addr: ["wallet address", "wallet addresses"],
  hex64: ["key or hash", "keys and hashes"],
  api_key: ["API key", "API keys"],
  secret: ["secret", "secrets"],
  email: ["email address", "email addresses"],
  url: ["link with credentials", "links with credentials"],
  ip: ["IP address", "IP addresses"],
  seed_phrase: ["recovery phrase", "recovery phrases"],
};

/**
 * "2 wallet addresses and 1 API key were replaced" — the counts as a sentence.
 *
 * A table of fifteen zeros is not something anybody reads before consenting, so
 * the zeros are dropped here even though they are deliberately *reported* to
 * the collector (that is where an all-zero share is the signal that a build's
 * scrubber broke). Categories that map to the same words are summed, so a chat
 * with an EVM and a Solana address says "2 wallet addresses" rather than
 * itemising the chains at somebody who does not care.
 */
function redactionSentence(counts: RedactionCounts): string {
  const merged = new Map<string, { count: number; plural: string }>();
  for (const [key, count] of Object.entries(counts)) {
    if (!count) continue;
    const [one, many] = LABELS[key] ?? [key, key];
    const existing = merged.get(one);
    merged.set(one, { count: (existing?.count ?? 0) + count, plural: many });
  }
  if (merged.size === 0)
    return "Nothing was replaced — this chat named no keys, wallets or addresses.";

  const parts = [...merged.entries()].map(
    ([one, { count, plural }]) => `${count} ${count === 1 ? one : plural}`,
  );
  const listed =
    parts.length === 1
      ? parts[0]
      : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
  return `${listed} ${merged.size === 1 && parts[0].startsWith("1 ") ? "was" : "were"} replaced.`;
}

function TurnPreview({ turn }: { turn: SharePreview["turns"][number] }) {
  const label =
    turn.role === "user"
      ? "You"
      : turn.role === "assistant"
        ? "Condor"
        : "System";
  return (
    <div className="border-b border-[var(--color-border)]/40 px-3 py-2 last:border-0">
      <div className="mb-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
        {label}
      </div>
      {turn.text && (
        <p className="whitespace-pre-wrap break-words text-xs text-[var(--color-text)]">
          {turn.text}
        </p>
      )}
      {turn.thought && (
        <p className="mt-1 whitespace-pre-wrap break-words text-xs italic text-[var(--color-text-muted)]">
          {turn.thought}
        </p>
      )}
      {turn.tool_calls?.length > 0 && (
        <div className="mt-1 flex flex-wrap gap-1">
          {turn.tool_calls.map((call, i) => (
            <span
              key={call.id ?? i}
              className="rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] text-[var(--color-text-muted)]"
            >
              {call.title ?? "tool"}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

/**
 * Hand one conversation to the Condor project — after reading exactly what goes.
 *
 * The dialog is the last gate, and it is the reason this feature ships an
 * explicit button rather than a background sweep. The scrubber replaces every
 * value the install knows about itself and then pattern-matches the shapes it
 * does not, but no regex knows that "the vault key is hunter2" is a secret. So
 * the payload is rendered here, verbatim, before anyone consents to it: the
 * bytes below are the bytes that leave.
 *
 * Quantities are deliberately kept, and the copy says so rather than hiding it.
 * A corpus without the numbers cannot tell whether the agent got the answer
 * right, which is the whole reason for collecting one.
 */
export function ShareConversation({
  conversationId,
  open,
  onClose,
}: {
  conversationId: string;
  open: boolean;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  useEscapeKey(open, onClose);

  const { data, isLoading, error } = useQuery({
    queryKey: ["share-preview", conversationId],
    queryFn: () => api.previewShare(conversationId),
    enabled: open,
    // Never cached: the transcript grows, and a stale preview would show the
    // user bytes that are not the ones about to be sent.
    staleTime: 0,
    gcTime: 0,
    retry: false,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["conversations"] });
    queryClient.invalidateQueries({ queryKey: ["shared-conversations"] });
  };

  const shareMutation = useMutation({
    mutationFn: () => api.shareConversation(conversationId),
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });

  const unshareMutation = useMutation({
    mutationFn: () => api.unshareConversation(conversationId),
    onSuccess: () => {
      invalidate();
      onClose();
    },
  });

  if (!open) return null;

  /** The escape hatch: the same redacted bytes, downloaded instead of sent.
   *  Somebody who wants to read the payload in an editor, or send it another
   *  way, should not have to choose between this button and nothing. */
  const download = () => {
    if (!data) return;
    const blob = new Blob([JSON.stringify(data.turns, null, 2)], {
      type: "application/json",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `condor-conversation-${conversationId}.json`;
    link.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-full w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-[var(--color-border)] bg-[var(--color-surface)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="border-b border-[var(--color-border)] p-5">
          <h2 className="flex items-center gap-2 text-lg font-semibold text-[var(--color-text)]">
            <Share2 className="h-4 w-4" />
            Share this chat with Condor
          </h2>
          <p className="mt-1 text-xs leading-relaxed text-[var(--color-text-muted)]">
            Real conversations are what make the agents better. Below is exactly
            what would be sent — read it first. Nothing leaves this install
            unless you press Share.
          </p>
        </div>

        {isLoading && (
          <div className="flex items-center justify-center gap-2 p-12 text-sm text-[var(--color-text-muted)]">
            <Loader2 className="h-4 w-4 animate-spin" /> Redacting…
          </div>
        )}

        {error && (
          <div className="p-6 text-sm text-[var(--color-red)]">
            {(error as Error).message}
          </div>
        )}

        {data && (
          <>
            <div className="space-y-2 border-b border-[var(--color-border)] px-5 py-3">
              <p className="flex items-start gap-2 text-xs text-[var(--color-text)]">
                <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-green)]" />
                <span>{redactionSentence(data.counts)}</span>
              </p>
              <p className="flex items-start gap-2 text-xs text-[var(--color-text-muted)]">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>
                  Amounts, prices and PnL are <strong>kept</strong> — without
                  them nobody can tell whether the agent got the answer right.
                  Redaction is best-effort on free text, so if you pasted
                  something sensitive into the chat, check for it below.
                </span>
              </p>
              {data.truncated && (
                <p className="text-xs text-[var(--color-text-muted)]">
                  This chat is over the size limit, so {data.turns_omitted}{" "}
                  turns from the middle were dropped. The start and the end are
                  intact.
                </p>
              )}
              {data.shared && (
                <p className="text-xs text-[var(--color-text-muted)]">
                  Already shared (revision {data.revision}). Sharing again
                  replaces that copy rather than adding a second one.
                </p>
              )}
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto bg-[var(--color-bg)]">
              {data.turns.map((turn, i) => (
                <TurnPreview key={i} turn={turn} />
              ))}
            </div>

            <div className="flex items-center justify-between gap-2 border-t border-[var(--color-border)] p-4">
              <div className="flex gap-2">
                <button
                  onClick={download}
                  className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                >
                  <Download className="h-3.5 w-3.5" />
                  Download instead
                </button>
                {data.shared && (
                  <button
                    onClick={() => unshareMutation.mutate()}
                    disabled={unshareMutation.isPending}
                    className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs text-[var(--color-red)] hover:bg-[var(--color-surface-hover)]"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    Unshare
                  </button>
                )}
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={onClose}
                  className="rounded-md px-3 py-1.5 text-xs text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
                >
                  Cancel
                </button>
                {confirming ? (
                  <button
                    onClick={() => shareMutation.mutate()}
                    disabled={shareMutation.isPending}
                    className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-60"
                  >
                    {shareMutation.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Send className="h-3.5 w-3.5" />
                    )}
                    Yes, send this
                  </button>
                ) : (
                  <button
                    onClick={() => setConfirming(true)}
                    className="flex items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-white"
                  >
                    <Share2 className="h-3.5 w-3.5" />
                    {data.shared ? "Share again" : "Share"}
                  </button>
                )}
              </div>
            </div>

            {shareMutation.isError && (
              <p className="border-t border-[var(--color-border)] px-4 py-2 text-xs text-[var(--color-red)]">
                {(shareMutation.error as Error).message}
              </p>
            )}
          </>
        )}
      </div>
    </div>
  );
}
