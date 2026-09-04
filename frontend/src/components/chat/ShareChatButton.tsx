import { useQuery } from "@tanstack/react-query";
import { Share2 } from "lucide-react";
import { useState } from "react";

import { ShareConversation } from "@/components/chat/ShareConversation";
import { api } from "@/lib/api";

/**
 * Share *this* conversation, from the composer it is written in.
 *
 * The gesture already existed, in the rail: a `Share2` on the conversation row,
 * inside a `group-hover:opacity-100` cluster beside Rename and Delete. That is
 * the right place for "share one of the others" — you are already pointing at
 * the row — and the wrong place for the case that actually comes up, which is
 * wanting to share the chat you are reading. It cost a reach into a column most
 * readers keep collapsed, a hover over the correct row, and knowing the icon
 * was there at all; nothing on screen said so until the pointer was on top of
 * it.
 *
 * So the open conversation gets a button that is simply *visible*. It sits at
 * the left edge of the composer, on the same baseline as the mic and Send,
 * rather than in the bar above the transcript: that bar is about *which*
 * session you are in — the tab strip, the rail toggle — so a share icon landed
 * there read as "share something about the workspace", and the one target it
 * actually acts on, the chat below it, was the one thing the bar never named.
 * In the composer it is unambiguous, because everything else in that box acts
 * on this conversation too.
 *
 * It opens the same dialog the rail opens — one consent surface, one preview of
 * what would be sent — and it is only the affordance that is duplicated, not
 * the flow.
 *
 * It reads the rail's own `["conversations"]` query rather than taking the
 * share state as a prop: the two are then the same fact from the same cache,
 * so sharing from here re-tints the rail's icon and unsharing from the rail
 * un-tints this one, with no second source to keep in step. When the rail is
 * collapsed this is simply the query's only subscriber.
 */
export function ShareChatButton({
  conversationId,
  className = "",
}: {
  conversationId: string | null;
  className?: string;
}) {
  const [open, setOpen] = useState(false);

  const { data: conversations = [] } = useQuery({
    queryKey: ["conversations"],
    queryFn: () => api.listConversations(),
    staleTime: 30_000,
    enabled: Boolean(conversationId),
  });

  // A brand new session has no row on the server yet, so there is nothing to
  // preview and nothing to send. The button stays out of the composer rather
  // than sitting there disabled: an empty chat is the one state where sharing
  // is not a thing you could have wanted.
  if (!conversationId) return null;

  const meta = conversations.find((c) => c.id === conversationId);
  if (!meta) return null;

  const shared = Boolean(meta.share_id);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        // Sized like the mic and Send it shares the box with — same 36px
        // square, same radius — so the composer reads as one row of controls
        // and not as a button that wandered in from somewhere else.
        className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg transition-colors hover:bg-[var(--color-surface-hover)] ${
          shared
            ? "text-[var(--color-primary)]"
            : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        } ${className}`}
        title={
          shared
            ? "Shared with Condor — review or unshare"
            : "Share this chat with Condor"
        }
        aria-label={shared ? "Review this share" : "Share this chat"}
      >
        <Share2 className="h-4 w-4" />
      </button>

      {open && (
        <ShareConversation
          conversationId={conversationId}
          open
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}
