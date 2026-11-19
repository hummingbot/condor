import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Bot, Loader2, MessageSquare, Pencil } from "lucide-react";
import { useEffect, useState } from "react";

import { api, type ConversationMeta } from "@/lib/api";

/**
 * The bar above the transcript: who this chat is with, and what it is about.
 *
 * It took the session tabs' place when the workspace became one chat at a
 * time. The agent leads, because it is the fact that changes what the chat
 * can do — a specialist with its own tools and model, or Condor — and it was
 * otherwise only in the rail, which most readers keep collapsed.
 *
 * The title is renamed in place, where it is read: a click turns it into a
 * field, Enter keeps it, Escape or leaving it unchanged drops it. Same call
 * as the rail's own rename, so the two can never disagree.
 */
export function ChatIdentity({
  agentName,
  bound,
  title,
  busy,
  conversationId,
}: {
  agentName: string;
  /** A specialist rather than Condor, the unbound assistant. */
  bound: boolean;
  /** The conversation's title; empty for a chat nothing has been said in. */
  title: string;
  /** Answering, or still starting. */
  busy: boolean;
  /** The saved conversation behind the chat; absent until one exists. */
  conversationId?: string;
}) {
  const Icon = bound ? Bot : MessageSquare;
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");

  const [error, setError] = useState<string | null>(null);
  // The notice clears itself; it is about the attempt, not the chat.
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 5000);
    return () => clearTimeout(t);
  }, [error]);

  // Optimistic, and honest about it: the new name shows at once (here and in
  // the rail, which reads the same cache), and a refused save puts the old one
  // back *and says so* — it used to revert silently, which read as "renaming
  // does not stick".
  const rename = useMutation({
    mutationFn: (next: string) => api.renameConversation(conversationId!, next),
    onMutate: async (next: string) => {
      setError(null);
      await queryClient.cancelQueries({ queryKey: ["conversations"] });
      const prev = queryClient.getQueryData<ConversationMeta[]>(["conversations"]);
      queryClient.setQueryData<ConversationMeta[]>(["conversations"], (list) =>
        list?.map((c) => (c.id === conversationId ? { ...c, title: next } : c)),
      );
      return { prev };
    },
    onError: (err, _next, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(["conversations"], ctx.prev);
      setError(err instanceof Error && err.message ? err.message : "Could not save the name");
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["conversations"] }),
  });

  const commit = () => {
    const next = draft.trim();
    if (next && next !== title) rename.mutate(next);
    setEditing(false);
  };

  return (
    <div className="flex min-w-0 flex-1 items-center gap-2 text-sm">
      <span
        className="flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--color-surface-hover)] px-2 py-0.5 text-xs font-medium text-[var(--color-text)]"
        title={bound ? `This chat is with ${agentName}` : "This chat is with Condor, the general assistant"}
      >
        <Icon className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        {agentName}
      </span>

      {editing ? (
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onFocus={(e) => e.currentTarget.select()}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === "Enter") commit();
            if (e.key === "Escape") setEditing(false);
          }}
          aria-label="Chat name"
          maxLength={120}
          className="min-w-0 max-w-md flex-1 rounded-md border border-[var(--color-primary)] bg-[var(--color-surface)] px-2 py-0.5 text-sm text-[var(--color-text)] outline-none"
        />
      ) : conversationId ? (
        <button
          type="button"
          onClick={() => {
            setDraft(title);
            setEditing(true);
          }}
          title="Rename this chat"
          className="group flex min-w-0 items-center gap-1.5 rounded-md px-1.5 py-0.5 text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)]"
        >
          <span className="min-w-0 truncate">{title || "New chat"}</span>
          <Pencil className="h-3 w-3 shrink-0 text-[var(--color-text-muted)] opacity-0 transition-opacity group-hover:opacity-100" />
        </button>
      ) : (
        <span
          className="min-w-0 truncate px-1.5 text-[var(--color-text-muted)]"
          title="A chat can be renamed once something has been said in it"
        >
          {title || "New chat"}
        </span>
      )}

      {error && (
        <span
          role="alert"
          className="shrink-0 rounded-md bg-[var(--color-red)]/10 px-1.5 py-0.5 text-xs font-medium text-[var(--color-red)]"
          title={`The name was not saved: ${error}`}
        >
          Not saved
        </span>
      )}

      {busy && (
        <Loader2
          className="h-3.5 w-3.5 shrink-0 animate-spin text-[var(--color-primary)]"
          aria-label="Answering"
        />
      )}
    </div>
  );
}
