import { memo, useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Archive,
  ArchiveRestore,
  Check,
  Loader2,
  Pencil,
  Share2,
  Trash2,
} from "lucide-react";

import { ShareConversation } from "@/components/chat/ShareConversation";
import { api, CHAT_SLUG, type AgentSummary, type ConversationMeta } from "@/lib/api";

const QUERY_KEY = ["conversations"] as const;

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
 * The last message as one line of plain text. Snippets are the agent's raw
 * markdown, and `**$0.9990**` in a list of titles reads as noise.
 */
function plainSnippet(md: string): string {
  return md
    .replace(/```[\s\S]*?(```|$)/g, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_`~]+/g, "")
    .replace(/^\s*(#+|>|[-+]|\d+\.)\s+/gm, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** `fundamental_analyst` → "Fundamental analyst", for an agent no longer installed. */
function humanize(slug: string): string {
  const words = slug.replace(/[_-]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Chats shown per agent before "Show more". */
const PER_AGENT = 5;

/** Chats their owner archived — and only those. */
export const ARCHIVED_GROUP = "__archived__";

/** The foot-of-column group, which lists each chat with its agent's name. */
const isFootGroup = (slug: string) => slug === ARCHIVED_GROUP;

/** What an agent's header row needs to draw itself. */
export interface AgentGroupHeader {
  /** `null` for an agent no longer installed, known only by its chats. */
  agent: AgentSummary | null;
  slug: string;
  name: string;
  count: number;
  expanded: boolean;
  /** The open chat is one of this agent's. */
  holdsActive: boolean;
  onToggle: () => void;
}

/**
 * Every conversation, filed under the agent it was with.
 *
 * It was two lists: the agents, and below them the chats by date, each
 * repeating its agent's name — so every agent was on screen twice and a busy
 * one many times. Now each agent is one row, and its chats sit inside it.
 * Agents with recent chats come first, newest first; the ones never talked to
 * follow, in the registry's order, so starting a chat with one is still a
 * click on its row.
 *
 * The header rows are drawn by the rail (`renderAgent`) — they carry the
 * rail's own gestures, the `+` and the `⋯` menu — and this owns the chats:
 * the query, rename, share, delete, and which groups are open.
 */
export const ConversationList = memo(function ConversationList({
  agents,
  busyIds,
  waitingIds,
  activeId,
  onOpen,
  renderAgent,
}: {
  /** The registry, Condor included (`CHAT_SLUG`). */
  agents: AgentSummary[];
  /** Answering right now — the row shows a spinner where the time was. */
  busyIds?: Set<string>;
  /** Waiting on the user's approval — the row says so. */
  waitingIds?: Set<string>;
  activeId: string | null;
  onOpen: (meta: ConversationMeta) => void;
  renderAgent: (header: AgentGroupHeader) => ReactNode;
}) {
  const queryClient = useQueryClient();
  const [renaming, setRenaming] = useState<string | null>(null);
  const [draftTitle, setDraftTitle] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);
  const [sharing, setSharing] = useState<string | null>(null);
  // Groups the reader opened or closed by hand; the rest follow the default.
  const [toggled, setToggled] = useState<Record<string, boolean>>({});
  const [showAll, setShowAll] = useState<Record<string, boolean>>({});

  // The rail mounts with the chat workspace, so "always" is one fetch per
  // arrival at the workspace — and a chat started seconds ago is at the top
  // where the user expects it.
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

  // Optimistic like rename: the row moves at once, and moves back if refused.
  const archiveMutation = useMutation({
    mutationFn: ({ id, archived }: { id: string; archived: boolean }) =>
      api.setConversationArchived(id, archived),
    onMutate: async ({ id, archived }) => {
      await queryClient.cancelQueries({ queryKey: QUERY_KEY });
      const prev = queryClient.getQueryData<ConversationMeta[]>(QUERY_KEY);
      queryClient.setQueryData<ConversationMeta[]>(QUERY_KEY, (list) =>
        list?.map((c) => (c.id === id ? { ...c, archived } : c)),
      );
      return { prev };
    },
    onError: (_e, _v, ctx) => {
      if (ctx?.prev) queryClient.setQueryData(QUERY_KEY, ctx.prev);
    },
    onSettled: invalidate,
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

  // Held across renders: react-query keeps `conversations` identity-stable
  // between refetches, so the rail does not re-file 100 rows while an answer
  // streams beside it.
  const groups = useMemo(() => {
    const bySlug = new Map<string, ConversationMeta[]>();
    for (const c of conversations) {
      const slug = c.archived ? ARCHIVED_GROUP : c.agent_slug || CHAT_SLUG;
      const list = bySlug.get(slug);
      if (list) list.push(c);
      else bySlug.set(slug, [c]);
    }
    const known = new Map(agents.map((a) => [a.slug, a]));
    // Newest chat first within a group (the API already sorts), groups by
    // their newest chat; agents with no chats after, in registry order.
    const archived = bySlug.get(ARCHIVED_GROUP) ?? [];
    bySlug.delete(ARCHIVED_GROUP);
    // Chats bound to an agent that is not installed are not listed: deleting
    // an agent deletes its chats, so these are leftovers (another user's
    // agent, a reinstall under a new slug) that as groups of their own read as
    // duplicate agents with no one to answer them.
    for (const slug of [...bySlug.keys()]) {
      if (!known.has(slug)) bySlug.delete(slug);
    }
    const talked = [...bySlug.entries()]
      .map(([slug, items]) => ({
        slug,
        agent: known.get(slug) ?? null,
        items,
        latest: items[0]?.updated_at ?? "",
      }))
      .sort((x, y) => (x.latest < y.latest ? 1 : x.latest > y.latest ? -1 : 0));
    const quiet = agents
      .filter((a) => !bySlug.has(a.slug))
      .map((a) => ({ slug: a.slug, agent: a, items: [] as ConversationMeta[], latest: "" }));
    const tail = [
      // Always listed, empty or not: where archived chats go has to be
      // findable before the first one is archived.
      { slug: ARCHIVED_GROUP, agent: null, items: archived, latest: "" },
    ];
    return [...talked, ...quiet, ...tail];
  }, [conversations, agents]);

  const activeSlugOfList = conversations.find((c) => c.id === activeId);
  const activeAgentSlug = activeSlugOfList ? activeSlugOfList.agent_slug || CHAT_SLUG : null;
  const activeGroup = activeSlugOfList?.archived ? ARCHIVED_GROUP : activeAgentSlug;
  // Open by default: the agent of the chat on screen, and the two most recent.
  const isExpanded = (slug: string, index: number, count: number) =>
    count > 0 &&
    (toggled[slug] ?? (slug === activeGroup || (index < 2 && !isFootGroup(slug))));

  const renderRow = (meta: ConversationMeta, withAgent = false) => {
    const isActive = meta.id === activeId;
    const snippet = meta.last_snippet ? plainSnippet(meta.last_snippet) : "";
    const title = meta.title || snippet || "New chat";
    if (renaming === meta.id) {
      return (
        <div key={meta.id} className="flex items-center gap-1 py-1 pl-9 pr-3">
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
      );
    }
    if (confirmDelete === meta.id) {
      return (
        <div key={meta.id} className="flex items-center justify-between gap-2 py-1.5 pl-9 pr-3">
          <span className="truncate text-xs text-[var(--color-text-muted)]">Delete this chat?</span>
          <div className="flex shrink-0 gap-1">
            <button
              onClick={() => {
                deleteMutation.mutate(meta.id);
                setConfirmDelete(null);
              }}
              className="rounded bg-[var(--color-red)] px-2 py-0.5 text-2xs font-medium text-white"
            >
              Delete
            </button>
            <button
              onClick={() => setConfirmDelete(null)}
              className="rounded border border-[var(--color-border)] px-2 py-0.5 text-2xs"
            >
              Cancel
            </button>
          </div>
        </div>
      );
    }
    return (
      <div key={meta.id} data-chat={meta.id} className="px-2">
      <div
        // The open chat is a raised card on the column, the way a selected
        // item sits in a sidebar tree, rather than a tint.
        className={`group/row relative ml-7 flex items-center rounded-lg ${
          isActive
            ? "bg-[var(--color-surface)] shadow-sm ring-1 ring-[var(--color-border)]"
            : "hover:bg-[var(--color-surface-hover)]"
        }`}
      >
        <button
          onClick={() => onOpen(meta)}
          // The date is a detail, not a column: it lives in the tooltip.
          title={`${title} · ${relativeTime(meta.updated_at)}${snippet ? `\n\n${snippet}` : ""}`}
          className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-1.5 text-left"
        >
          <span
            className={`min-w-0 flex-1 truncate text-md ${
              isActive
                ? "font-medium text-[var(--color-text)]"
                : "text-[var(--color-text-muted)] group-hover/row:text-[var(--color-text)]"
            }`}
          >
            {title}
            {withAgent && (
              <span className="text-[var(--color-text-muted)]">
                {" · "}
                {agents.find((a) => a.slug === (meta.agent_slug || CHAT_SLUG))?.name ??
                  (meta.agent_slug ? humanize(meta.agent_slug) : "Condor")}
              </span>
            )}
          </span>
          {waitingIds?.has(meta.id) ? (
            <AlertTriangle
              className="h-3 w-3 shrink-0 text-[var(--color-yellow)]"
              aria-label="Waiting for your approval"
            >
              <title>Waiting for your approval</title>
            </AlertTriangle>
          ) : busyIds?.has(meta.id) ? (
            <Loader2
              className="h-3 w-3 shrink-0 animate-spin text-[var(--color-primary)]"
              aria-label="Answering"
            />
          ) : null}
        </button>
        <div className="absolute right-1 flex gap-0.5 opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100">
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
            onClick={() => setSharing(meta.id)}
            className={`rounded bg-[var(--color-surface)] p-1 hover:text-[var(--color-primary)] ${
              meta.share_id ? "text-[var(--color-primary)]" : "text-[var(--color-text-muted)]"
            }`}
            title={meta.share_id ? "Shared with Condor — review or unshare" : "Share with Condor"}
          >
            <Share2 className="h-3 w-3" />
          </button>
          <button
            onClick={() => archiveMutation.mutate({ id: meta.id, archived: !meta.archived })}
            className="rounded bg-[var(--color-surface)] p-1 text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
            title={meta.archived ? "Move back to your chats" : "Archive"}
          >
            {meta.archived ? <ArchiveRestore className="h-3 w-3" /> : <Archive className="h-3 w-3" />}
          </button>
          <button
            onClick={() => setConfirmDelete(meta.id)}
            className="rounded bg-[var(--color-surface)] p-1 text-[var(--color-text-muted)] hover:text-[var(--color-red)]"
            title="Delete"
          >
            <Trash2 className="h-3 w-3" />
          </button>
        </div>
      </div>
      </div>
    );
  };

  // Talked-to agents scroll in the column's body; the ones never talked to
  // and the archive of removed agents sit at its foot, pinned above the Hub —
  // they are where to *start*, not part of the history above them.
  const talked = groups.filter((g) => g.items.length > 0 && !isFootGroup(g.slug));
  const quiet = groups.filter((g) => g.items.length === 0 && !isFootGroup(g.slug));
  const foot = groups.filter((g) => isFootGroup(g.slug));

  const renderGroup = (g: (typeof groups)[number], i: number) => {
    const expanded = isExpanded(g.slug, i, g.items.length);
    const shown = showAll[g.slug] ? g.items : g.items.slice(0, PER_AGENT);
    const hidden = g.items.length - shown.length;
    return (
      <div key={g.slug}>
        {renderAgent({
          agent: g.agent,
          slug: g.slug,
          name: g.slug === ARCHIVED_GROUP ? "Archived" : (g.agent?.name ?? humanize(g.slug)),
          count: g.items.length,
          expanded,
          holdsActive: g.slug === activeGroup,
          onToggle: () => setToggled((t) => ({ ...t, [g.slug]: !expanded })),
        })}
        {expanded && (
          <div className="pb-1">
            {shown.map((m) => renderRow(m, isFootGroup(g.slug)))}
            {hidden > 0 && (
              <button
                onClick={() => setShowAll((s) => ({ ...s, [g.slug]: true }))}
                className="w-full py-1.5 pl-12 text-left text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
              >
                Show {hidden} more
              </button>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto py-1">
        {isLoading && (
          <div className="flex items-center gap-2 px-3 py-4 text-xs text-[var(--color-text-muted)]">
            <Loader2 className="h-3 w-3 animate-spin" /> Loading…
          </div>
        )}
        {talked.length > 0 && (
          <div className="px-4 pb-1.5 pt-2 text-xs font-medium text-[var(--color-text-muted)]">
            Your chats
          </div>
        )}
        {talked.map((g, i) => renderGroup(g, i))}
      </div>

      {(quiet.length > 0 || foot.length > 0) && (
        // Its own scroll past a third of the column, so a long list of unused
        // agents never pushes the chats off screen.
        <div className="flex max-h-[38%] shrink-0 flex-col gap-0.5 overflow-y-auto border-t border-[var(--color-border)] py-1">
          {quiet.length > 0 && (
            <div className="px-4 pb-1.5 pt-2 text-xs font-medium text-[var(--color-text-muted)]">
              Start a chat with
            </div>
          )}
          {quiet.map((g) => renderGroup(g, Infinity))}
          {foot.length > 0 && quiet.length > 0 && (
            <div className="mx-4 my-1 border-t border-[var(--color-border)]" />
          )}
          {foot.map((g) => renderGroup(g, Infinity))}
        </div>
      )}

      {/* One dialog for the whole rail rather than one per row: it is mounted
          only while a conversation is selected, and its preview query is keyed
          on that id. */}
      {sharing && (
        <ShareConversation
          conversationId={sharing}
          open
          onClose={() => setSharing(null)}
        />
      )}
    </div>
  );
});
