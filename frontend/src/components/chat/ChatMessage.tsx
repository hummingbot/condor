import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronRight, Square, User, Bot } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "@/hooks/useChatSocket";
import { useLiveDisclosure } from "@/hooks/useLiveDisclosure";
import { ToolCallStatus } from "./ToolCallStatus";

function ThoughtBlock({ text, live }: { text: string; live: boolean }) {
  const { expanded, toggle } = useLiveDisclosure(live);

  return (
    <div className="mb-1">
      <button
        onClick={toggle}
        className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        Thinking...
      </button>
      {expanded && (
        <div className="mt-1 ml-4 text-xs text-[var(--color-text-muted)] italic whitespace-pre-wrap">
          {text}
        </div>
      )}
    </div>
  );
}

export const ChatMessageView = memo(function ChatMessageView({
  message,
  live = false,
}: {
  message: ChatMessageType;
  /**
   * This is the bubble the answer is currently streaming into. Only the
   * disclosure blocks care: it is what lets the reasoning and the tool list
   * show themselves while they are being produced, and close once they are
   * not the interesting part of the bubble anymore.
   */
  live?: boolean;
}) {
  // A delegation's outcome is the answer to something asked minutes ago, so it
  // is prose, not a label: the divider treatment (uppercase, nowrap, 10px)
  // would render it as one unreadable line. It stays visibly not-the-agent's
  // own words — an inset note, no avatar — while still being readable.
  if (message.role === "system" && message.kind === "delegation") {
    return (
      <div className="my-3 flex justify-start">
        <div className="chat-markdown max-w-[85%] rounded-xl border border-dashed border-[var(--color-border)] px-3.5 py-2 text-sm text-[var(--color-text-muted)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
        </div>
      </div>
    );
  }

  // A handover is not a bubble. Rendering it as a divider is what makes the
  // switch visible in the scrollback instead of implied by a header that
  // silently changed.
  if (message.role === "system") {
    return (
      <div className="my-3 flex items-center gap-2">
        <div className="h-px flex-1 bg-[var(--color-border)]" />
        <span className="whitespace-nowrap text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          {message.text}
        </span>
        <div className="h-px flex-1 bg-[var(--color-border)]" />
      </div>
    );
  }

  if (message.role === "user") {
    return (
      <div className="flex justify-end mb-3">
        <div className="flex items-start gap-2 max-w-[85%]">
          <div className="rounded-2xl rounded-tr-sm bg-[var(--color-primary)] px-3.5 py-2 text-sm text-white">
            <p className="whitespace-pre-wrap">{message.text}</p>
          </div>
          <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--color-primary)]/20">
            <User className="h-3.5 w-3.5 text-[var(--color-primary)]" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex justify-start mb-3">
      <div className="flex items-start gap-2 max-w-[85%]">
        <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)]/20">
          <Bot className="h-3.5 w-3.5 text-[var(--color-accent)]" />
        </div>
        <div className="min-w-0">
          {/* The thought is the live thing only until the answer starts
              landing in this same bubble. Keying the collapse on `text` too
              means a lost `prompt_done` cannot leave it stuck open. */}
          {message.thought && <ThoughtBlock text={message.thought} live={live && !message.text} />}
          <ToolCallStatus toolCalls={message.toolCalls} live={live} />
          {message.text && (
            <div className="chat-markdown rounded-2xl rounded-tl-sm bg-[var(--color-surface-hover)] px-3.5 py-2 text-sm">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{message.text}</ReactMarkdown>
            </div>
          )}
          {!message.text && message.toolCalls.length === 0 && !message.thought && (
            <div className="rounded-2xl rounded-tl-sm bg-[var(--color-surface-hover)] px-3.5 py-2 text-sm text-[var(--color-text-muted)]">
              ...
            </div>
          )}
          {/* The user redirected the agent here. Subdued on purpose: the
              partial is still worth reading and its context carried into the
              next turn, so this is a seam, not a failure. */}
          {message.interrupted && (
            <div className="mt-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
              <Square className="h-2.5 w-2.5" />
              <span className="whitespace-nowrap">Interrupted</span>
              <div className="h-px flex-1 bg-[var(--color-border)]" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
});
