import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Share2, X } from "lucide-react";

import { api } from "@/lib/api";

/**
 * "This conversation will be shared" — and one click to say no.
 *
 * The compensating control for the one path where nobody reads the payload
 * first. With Always on, the scrubber is the last gate before a transcript
 * leaves, so the user has to be unable to *forget* that it is on — which is why
 * this sits in the chat header rather than in a settings page they visited once,
 * and why it has no dismiss. The ⨯ excludes this conversation from the sweep
 * forever; it does not hide the chip, because hiding it is the one thing a
 * persistent indicator must not offer.
 *
 * It renders nothing at all when Always is off, which is every install by
 * default: an indicator that is always present says nothing, and a user at Ask
 * is already reading a dialog before anything moves.
 */
export function SharingIndicator({ conversationId }: { conversationId: string }) {
  const queryClient = useQueryClient();
  const statusKey = ["conversation-sharing", conversationId] as const;

  const { data: preference } = useQuery({
    queryKey: ["sharing-preference"],
    queryFn: api.getSharingPreference,
    retry: false,
  });

  // Only asked once Always is on. On every other install this is the whole cost
  // of the feature in the chat view: one cached preference read, no per
  // conversation request at all.
  const { data: status } = useQuery({
    queryKey: statusKey,
    queryFn: () => api.getConversationSharing(conversationId),
    enabled: !!conversationId && !!preference?.sweeping,
    retry: false,
  });

  const exclude = useMutation({
    mutationFn: (excluded: boolean) =>
      api.setConversationExcluded(conversationId, excluded),
    onSuccess: (next) => {
      queryClient.setQueryData(statusKey, next);
      queryClient.invalidateQueries({ queryKey: ["conversations"] });
    },
  });

  if (!preference?.sweeping || !status) return null;

  // Not covered and not excluded means a rule other than the user's choice
  // refused it — an older conversation, or a room with other people in it.
  // Saying nothing is right: there is nothing for them to act on.
  if (!status.covered && !status.excluded) return null;

  if (status.excluded) {
    return (
      <div
        data-sharing-chip="excluded"
        className="flex shrink-0 items-center gap-1.5 border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-1.5 text-[11px] text-[var(--color-text-muted)]"
      >
        <Share2 className="h-3 w-3 shrink-0 opacity-50" />
        <span className="flex-1">
          Excluded — this conversation is not shared automatically.
        </span>
        <button
          onClick={() => exclude.mutate(false)}
          disabled={exclude.isPending}
          className="rounded px-1.5 py-0.5 hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-60"
        >
          Include it
        </button>
      </div>
    );
  }

  return (
    <div
      data-sharing-chip={status.shared ? "shared" : "will-share"}
      className="flex shrink-0 items-center gap-1.5 border-b border-[var(--color-primary)]/30 bg-[var(--color-primary)]/10 px-4 py-1.5 text-[11px] text-[var(--color-text)]"
    >
      <Share2 className="h-3 w-3 shrink-0 text-[var(--color-primary)]" />
      <span className="flex-1">
        {status.shared
          ? "Shared with Condor — redacted, and you can take it back any time."
          : "Will be shared with Condor once you are done, redacted."}
      </span>
      <button
        onClick={() => exclude.mutate(true)}
        disabled={exclude.isPending}
        title="Exclude this conversation from automatic sharing"
        className="flex items-center gap-1 rounded px-1.5 py-0.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-60"
      >
        Not this one
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}
