import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronRight, User, Bot } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "@/hooks/useChatSocket";
import { ToolCallStatus } from "./ToolCallStatus";

function ThoughtBlock({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="mb-1">
      <button
        onClick={() => setExpanded(!expanded)}
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

export function ChatMessageView({ message }: { message: ChatMessageType }) {
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
          {message.thought && <ThoughtBlock text={message.thought} />}
          <ToolCallStatus toolCalls={message.toolCalls} />
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
        </div>
      </div>
    </div>
  );
}
