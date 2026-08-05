import { useCallback, useEffect, useRef } from "react";
import { AlertTriangle, Bot, Brain, Loader2, MessageSquare, X } from "lucide-react";

import type { ChatSlot } from "@/hooks/useChatSocket";
import type { ChatAgentOption } from "@/lib/api";
import { ChatInput } from "./ChatInput";
import { ChatMessageView } from "./ChatMessage";

/** How close to the end still counts as "following the answer", in pixels. */
const NEAR_BOTTOM_PX = 80;

/** Resolve a short label for an agent key. */
export function resolveAgentLabel(agentKey: string, agents: ChatAgentOption[]): string {
  const match = agents.find((a) => a.key === agentKey);
  if (match) return match.label;
  // Handle dynamic keys like "openrouter:anthropic/claude-3.5-sonnet"
  if (agentKey.includes(":")) {
    const [provider, model] = agentKey.split(":", 2);
    return model || provider;
  }
  return agentKey;
}

/**
 * The conversation itself, at any width.
 *
 * The 480 px overlay panel and the `/agents` workspace render this same
 * component, so a tool call, a thought block or a handover divider can never
 * look like two different things depending on where the user opened the chat.
 * Everything around it — who is answering, which conversations exist — is the
 * surface's business; this is only the transcript and the composer.
 */
export function ChatThread({
  slot,
  agents,
  isStreaming,
  isQueued = false,
  permissionRequest,
  onResolvePermission,
  switchError,
  onDismissSwitchError,
  onSend,
  onAbort,
  emptyState,
  boundAgent,
  columnClassName = "",
  autoFocus = false,
}: {
  /** The conversation on screen, or null when there is none yet. */
  slot: ChatSlot | null;
  agents: ChatAgentOption[];
  isStreaming: boolean;
  /**
   * The turn was accepted but has not started — it is waiting behind the one
   * in front of it. Shown rather than hidden: a queued message that looked
   * identical to a dropped one is the bug this replaces.
   */
  isQueued?: boolean;
  permissionRequest: { request_id: string; summary: string } | null;
  onResolvePermission: (requestId: string, approved: boolean) => void;
  switchError?: string | null;
  onDismissSwitchError?: () => void;
  onSend: (text: string) => void;
  onAbort: () => void;
  /** Rendered instead of a transcript when there is no slot. */
  emptyState?: React.ReactNode;
  /**
   * The domain Agent this conversation is bound to, when the surface knows it.
   * A bound chat opens under that agent's name — the user picked Backpack MM,
   * so "Condor / General trading assistant" would name the wrong counterpart.
   */
  boundAgent?: { name: string; description?: string };
  /** Reading column for the messages, e.g. `mx-auto w-full max-w-3xl`. */
  columnClassName?: string;
  autoFocus?: boolean;
}) {
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const scrollAreaRef = useRef<HTMLDivElement>(null);
  const scrollFrameRef = useRef<number | null>(null);

  /**
   * Scroll the transcript to its end, at most once per animation frame.
   *
   * Streaming replaces the messages array on every chunk, so an ungated
   * `scrollIntoView` runs once per token: each call restarts the browser's
   * smooth-scroll animation from wherever the last one got to and forces a
   * synchronous layout of the whole transcript, which is what makes a long
   * answer stutter. Holding a single frame handle and cancelling the pending
   * one collapses a burst of chunks into the one scroll that matters.
   *
   * `force` is for the discrete events — a new message, a different
   * conversation — that should always land at the bottom. Everything else is
   * gated on the viewport already being near the end, so a user who scrolled
   * back to re-read an earlier answer is not dragged down by the next token.
   */
  const scrollToBottom = useCallback((behavior: ScrollBehavior, force: boolean) => {
    if (scrollFrameRef.current !== null) cancelAnimationFrame(scrollFrameRef.current);
    scrollFrameRef.current = requestAnimationFrame(() => {
      scrollFrameRef.current = null;
      const area = scrollAreaRef.current;
      if (!force && area) {
        const distanceFromBottom = area.scrollHeight - area.scrollTop - area.clientHeight;
        if (distanceFromBottom > NEAR_BOTTOM_PX) return;
      }
      messagesEndRef.current?.scrollIntoView({ behavior });
    });
  }, []);

  const slotId = slot?.info.slot_id;
  const messageCount = slot?.messages.length ?? 0;
  // Seeded empty rather than with the current values, so the first run after
  // mount counts as a slot change: a transcript that is already on screen when
  // this mounts still opens at its end.
  const prevSlotIdRef = useRef<string | undefined>(undefined);
  const prevCountRef = useRef(0);

  // Auto-scroll on new messages in the slot on screen, and follow a streaming
  // answer while the user is watching its end. A chunk only ever rewrites the
  // last bubble — tool calls and thoughts attach to it too — so the message
  // count moving is the honest signal for "something new arrived".
  useEffect(() => {
    const slotChanged = prevSlotIdRef.current !== slotId;
    const countChanged = prevCountRef.current !== messageCount;
    prevSlotIdRef.current = slotId;
    prevCountRef.current = messageCount;
    // A freshly selected transcript opens at its end, without animating
    // through everything above it.
    if (slotChanged) scrollToBottom("auto", true);
    else if (countChanged) scrollToBottom("smooth", true);
    else scrollToBottom("auto", false);
  }, [slotId, messageCount, slot?.messages, scrollToBottom]);

  useEffect(() => {
    return () => {
      if (scrollFrameRef.current !== null) cancelAnimationFrame(scrollFrameRef.current);
    };
  }, []);

  return (
    <>
      {/* Permission request banner */}
      {permissionRequest && (
        <div className="border-b border-amber-500/30 bg-amber-500/10 px-4 py-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-yellow)]" />
            <div className="flex-1 text-sm">
              <p className="font-medium text-[var(--color-yellow)]">Confirm action</p>
              <p className="mt-0.5 text-[var(--color-text-muted)]">
                {permissionRequest.summary}
              </p>
              <div className="mt-2 flex gap-2">
                <button
                  onClick={() => onResolvePermission(permissionRequest.request_id, true)}
                  className="rounded bg-[var(--color-green)] px-3 py-1 text-xs font-medium text-white hover:opacity-90"
                >
                  Approve
                </button>
                <button
                  onClick={() => onResolvePermission(permissionRequest.request_id, false)}
                  className="rounded bg-[var(--color-red)] px-3 py-1 text-xs font-medium text-white hover:opacity-90"
                >
                  Reject
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Switch failure — the header still shows whoever is actually answering */}
      {switchError && (
        <div className="flex items-start gap-2 border-b border-red-500/30 bg-red-500/10 px-4 py-2 text-xs text-red-400">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span className="flex-1">{switchError}</span>
          <button onClick={onDismissSwitchError} title="Dismiss">
            <X className="h-3 w-3" />
          </button>
        </div>
      )}

      {/* Messages area */}
      <div ref={scrollAreaRef} className="flex-1 overflow-y-auto px-4 py-4">
        {/* `min-h-full` so an empty state can centre itself in the viewport
            while a long transcript is still free to grow past it. */}
        <div className={`flex min-h-full flex-col ${columnClassName}`}>
          {!slot ? (
            emptyState
          ) : slot.messages.length === 0 ? (
            <div className="flex flex-1 flex-col items-center justify-center text-center">
              {(() => {
                // Who the user is about to talk to. A bound agent answers as
                // itself, so it names the screen; only the brain stays in the
                // footnote.
                const bound = slot.info.agent_slug
                  ? {
                      name: boundAgent?.name || slot.info.label || slot.info.agent_slug,
                      description: boundAgent?.description,
                    }
                  : null;
                const Icon = bound ? Bot : MessageSquare;
                const brain = resolveAgentLabel(slot.info.agent_key, agents);
                return (
                  <>
                    <Icon className="mb-3 h-10 w-10 text-[var(--color-text-muted)] opacity-30" />
                    <p className="text-sm font-medium text-[var(--color-text)]">
                      {bound ? bound.name : "Assistant"}
                    </p>
                    <p className="mt-1 text-xs text-[var(--color-text-muted)]">
                      {bound
                        ? bound.description || `Ask ${bound.name} anything.`
                        : "Ask about your portfolio, prices, trades, or bot status."}
                    </p>
                    <p className="mt-2 flex items-center gap-1 text-[10px] text-[var(--color-text-muted)] opacity-60">
                      <Brain className="h-2.5 w-2.5" />
                      {brain}
                    </p>
                  </>
                );
              })()}
              {slot.pending && (
                <p className="mt-3 flex items-center gap-1.5 text-[10px] text-[var(--color-text-muted)]">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Warming up — type away, it will be sent
                </p>
              )}
            </div>
          ) : (
            // Chunks only ever land in the last bubble, so that is the one
            // whose thinking and tool blocks are still being written — and
            // `isStreaming` is already scoped to the slot on screen.
            slot.messages.map((msg, i) => (
              <ChatMessageView
                key={msg.id}
                message={msg}
                live={isStreaming && i === slot.messages.length - 1}
              />
            ))
          )}
          {isQueued && (
            <div className="mb-3 flex items-center gap-1.5 pl-8 text-[11px] text-[var(--color-text-muted)]">
              <Loader2 className="h-3 w-3 animate-spin" />
              Waiting its turn
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>
      </div>

      {/* Input */}
      {slot && (
        <div className={columnClassName}>
          <ChatInput
            onSend={onSend}
            // Deliberately not `disabled={isStreaming}`. The composer stays
            // live while the agent answers: sending mid-answer is how the user
            // redirects it, and a dead input was the reason the correction had
            // nowhere to go (FEAT-030).
            isStreaming={isStreaming}
            onAbort={onAbort}
            autoFocus={autoFocus}
            // The composer names whoever is bound: a chat with Backpack MM is
            // not a chat with Condor, and the placeholder was the last place
            // the UI still said otherwise (FEAT-025).
            placeholder={`Ask ${(slot.info.agent_slug && slot.info.label) || "Condor"}...`}
          />
        </div>
      )}
    </>
  );
}
