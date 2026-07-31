import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  Check,
  Loader2,
  MessageSquare,
  Pencil,
  Plus,
  Send,
  Trash2,
  X,
} from "lucide-react";

import { api, type ConversationMeta } from "@/lib/api";

const QUERY_KEY = ["conversations"] as const;

/** Day buckets, newest first. A conversation is filed by when it last moved. */
const GROUPS = ["Today", "Yesterday", "Earlier"] as const;
type Group = (typeof GROUPS)[number];

function groupOf(iso: string): Group {
  const then = new Date(iso);
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  if (then >= midnight) return "Today";
  const yesterday = new Date(midnight.getTime() - 86_400_000);
  return then >= yesterday ? "Yesterday" : "Earlier";
}

function relativeTime(iso: string): string {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

/**
 * Every conversation the user has ever held, on either surface.
 *
 * Two shapes, one list. In the overlay panel it is a slide-over, because the
 * panel is available on every page and history should not cost the user that;
 * in the `/agents` workspace it is the rail, in flow. Grouping, rename, delete,
 * the live dot and the Telegram badge are shared — only the chrome differs.
 * Everything is keyed on conversation id, so the two cannot drift.
 */
export function ConversationList({
  liveIds,
  activeId,
  onNew,
  onOpen,
  onClose,
  variant = "drawer",
}: {
  /** Conversations with a live session, drawn from the panel's slots. */
  liveIds: Set<string>;
  activeId: string | null;
  onNew: () => void;
  onOpen: (meta: ConversationMeta) => void;
  onClose?: () => void;
  /** `drawer` slides over the panel; `inline` renders in flow, no backdrop. */
  variant?: "drawer" | "inline";
}) {
  const queryClient = useQueryClient();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  // The drawer is mounted only while it is open, and it opens on a deliberate
  // click, so "always" is one fetch per open — and a chat started seconds ago
  // is at the top where the user expects it.
  const { data: conversations = [], isLoading } = useQuery({
    queryKey: QUERY_KEY,
    queryFn: () => api.listConversations(),
    refetchOnMount: "always",
  });

  const invalidate = () => queryClient.invalidateQueries({ queryKey: QUERY_KEY });

  const renameMutation = useMutation({
    mutationFn: ({ id, title }: { id: string; title: string }) =>
      api.renameConversation(id, title),
    onSuccess: invalidate,
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => api.deleteConversation(id),
    onSuccess: invalidate,
  });

  const commitRename = (id: string) => {
    const title = draftTitle.trim();
    if (title) renameMutation.mutate({ id, title });
    setRenaming(null);
  };

  const grouped = GROUPS.map((group) => ({
    group,
    items: conversations.filter((c) => groupOf(c.updated_at) === group),
  })).filter((g) => g.items.length > 0);

  const isDrawer = variant === "drawer";

  return (
    <>
      {isDrawer && (
        <div className="absolute inset-0 z-[70] bg-black/40" onClick={onClose} />
      )}
      <div
        className={
          isDrawer
            ? "absolute inset-y-0 left-0 z-[71] flex w-full max-w-[340px] flex-col border-r border-[var(--color-border)] bg-[var(--color-bg)] shadow-2xl"
            : "flex min-h-0 flex-1 flex-col"
        }
      >
        <div
          className={`flex items-center justify-between border-b border-[var(--color-border)] px-3 py-2 ${
            isDrawer ? "bg-[var(--color-surface)]" : ""
          }`}
        >
          <span
            className={
              isDrawer
                ? "text-sm font-semibold"
                : "text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]"
            }
          >
            Conversations
          </span>
          {isDrawer && (
            <button
              onClick={onClose}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              title="Close"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <button
          onClick={onNew}
          className="flex items-center gap-2 border-b border-[var(--color-border)] px-3 py-2.5 text-left text-sm text-[var(--color-primary)] hover:bg-[var(--color-surface-hover)]"
        >
          <Plus className="h-4 w-4" />
          New chat
        </button>

        <div className="flex-1 overflow-y-auto">
          {isLoading && (
            <div className="flex items-center gap-2 px-3 py-4 text-xs text-[var(--color-text-muted)]">
              <Loader2 className="h-3 w-3 animate-spin" /> Loading…
            </div>
          )}
          {!isLoading && conversations.length === 0 && (
            <div className="px-3 py-6 text-center text-xs text-[var(--color-text-muted)]">
              Nothing yet. Start a chat and it will be here — from Telegram too.
            </div>
          )}

          {grouped.map(({ group, items }) => (
            <div key={group}>
              <div className="sticky top-0 bg-[var(--color-bg)] px-3 pb-1 pt-2.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                {group}
              </div>
              {items.map((meta) => {
                const isActive = meta.id === activeId;
                const isRenaming = renaming === meta.id;
                return (
                  <div
                    key={meta.id}
                    className={`group relative border-b border-[var(--color-border)]/40 px-3 py-2 ${
                      isActive
                        ? "bg-[var(--color-primary)]/10"
                        : "hover:bg-[var(--color-surface-hover)]"
                    }`}
                  >
                    {isRenaming ? (
                      <div className="flex items-center gap-1">
                        <input
                          autoFocus
                          value={draftTitle}
                          onChange={(e) => setDraftTitle(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") commitRename(meta.id);
                            if (e.key === "Escape") setRenaming(null);
                          }}
                          className="w-full rounded border border-[var(--color-border)] bg-[var(--color-surface)] px-2 py-1 text-xs text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none"
                        />
                        <button
                          onClick={() => commitRename(meta.id)}
                          className="rounded p-1 text-[var(--color-green)] hover:bg-[var(--color-surface-hover)]"
                          title="Save"
                        >
                          <Check className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    ) : confirmDelete === meta.id ? (
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-xs text-[var(--color-text-muted)]">
                          Delete this conversation?
                        </span>
                        <div className="flex shrink-0 gap-1">
                          <button
                            onClick={() => {
                              deleteMutation.mutate(meta.id);
                              setConfirmDelete(null);
                            }}
                            className="rounded bg-[var(--color-red)] px-2 py-0.5 text-[10px] font-medium text-white"
                          >
                            Delete
                          </button>
                          <button
                            onClick={() => setConfirmDelete(null)}
                            className="rounded border border-[var(--color-border)] px-2 py-0.5 text-[10px]"
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <button
                          onClick={() => onOpen(meta)}
                          className="w-full text-left"
                        >
                          <div className="flex items-center gap-1.5">
                            <span className="min-w-0 flex-1 truncate text-xs font-medium text-[var(--color-text)]">
                              {meta.title || "Untitled chat"}
                            </span>
                            {liveIds.has(meta.id) && (
                              <span
                                className="h-1.5 w-1.5 shrink-0 rounded-full bg-green-500"
                                title="Session live"
                              />
                            )}
                            <span className="shrink-0 text-[10px] text-[var(--color-text-muted)]">
                              {relativeTime(meta.updated_at)}
                            </span>
                          </div>
                          {meta.last_snippet && (
                            <p className="mt-0.5 truncate text-[11px] text-[var(--color-text-muted)]">
                              {meta.last_snippet}
                            </p>
                          )}
                          <div className="mt-1 flex items-center gap-1.5 text-[10px] text-[var(--color-text-muted)]">
                            {meta.agent_slug ? (
                              <span className="flex items-center gap-1 text-[var(--color-accent)]">
                                <Bot className="h-2.5 w-2.5" />
                                {meta.agent_slug}
                              </span>
                            ) : (
                              <span className="flex items-center gap-1">
                                <MessageSquare className="h-2.5 w-2.5" />
                                Condor
                              </span>
                            )}
                            {meta.surface === "tg" && (
                              <span
                                className="flex items-center gap-1 rounded bg-[var(--color-surface-hover)] px-1 py-0.5"
                                title="Started in Telegram"
                              >
                                <Send className="h-2.5 w-2.5" />
                                Telegram
                              </span>
                            )}
                          </div>
                        </button>
                        <div className="absolute right-2 top-1.5 flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                          <button
                            onClick={() => {
                              setDraftTitle(meta.title);
                              setRenaming(meta.id);
                            }}
                            className="rounded bg-[var(--color-surface)] p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                            title="Rename"
                          >
                            <Pencil className="h-3 w-3" />
                          </button>
                          <button
                            onClick={() => setConfirmDelete(meta.id)}
                            className="rounded bg-[var(--color-surface)] p-1 text-[var(--color-text-muted)] hover:text-[var(--color-red)]"
                            title="Delete"
                          >
                            <Trash2 className="h-3 w-3" />
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
