import { isValidElement, memo, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ChevronDown, ChevronRight, Loader2, Square, User, Bot } from "lucide-react";
import type { ChatMessage as ChatMessageType } from "@/hooks/useChatSocket";
import { useLiveDisclosure } from "@/hooks/useLiveDisclosure";
import { ChartBlock } from "./ChartBlock";
import { ToolCallStatus } from "./ToolCallStatus";

/** Flatten a fence's children back into the source text it was parsed from. */
function fenceText(children: ReactNode): string {
  if (typeof children === "string") return children;
  if (Array.isArray(children)) return children.map(fenceText).join("");
  return "";
}

/**
 * What the markdown pipeline can render, beyond markdown.
 *
 * A ```chart fence becomes a real chart; every other fence stays the code
 * block it already was. The override sits on `pre` rather than on `code`
 * because react-markdown v10 dropped the `inline` prop and still wraps fences
 * in a <pre> — replacing the wrapper is what keeps the chart out of a
 * whitespace-preserving box it was never meant to live in.
 */
function markdownComponents(live: boolean): Components {
  return {
    pre({ children, ...props }) {
      const child = Array.isArray(children) ? children[0] : children;
      if (isValidElement<{ className?: string; children?: ReactNode }>(child)) {
        const className = child.props.className ?? "";
        if (className.split(" ").includes("language-chart")) {
          return <ChartBlock raw={fenceText(child.props.children)} live={live} />;
        }
      }
      return <pre {...props}>{children}</pre>;
    },
  };
}

// Both variants are built once: a fresh `components` object per render would
// remount every block in the bubble on each streamed chunk.
const LIVE_COMPONENTS = markdownComponents(true);
const STATIC_COMPONENTS = markdownComponents(false);

function ThoughtBlock({ text, live }: { text: string; live: boolean }) {
  const { expanded, toggle } = useLiveDisclosure(live);

  return (
    <div className="mb-1">
      <button
        onClick={toggle}
        className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
      >
        {expanded ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        {live ? (
          // Same treatment the tool list gets while it runs: the spinner is
          // what says the reasoning is still arriving rather than finished and
          // merely worded in the present tense.
          <span className="flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            Thinking...
          </span>
        ) : (
          <span>Thought</span>
        )}
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
  //
  // A `resume` note is the same shape: it is what a finished background task
  // said to the agent to make it continue. Nobody typed it, so it must not
  // render as a user bubble. So is a `notification`: the agent announced it to
  // the user out of band, and the transcript records that it did. And so is a
  // `routine` outcome — a run's result, often a multi-line error, which the
  // divider rendered as one shouting, unreadable, un-wrapped line.
  const isNote =
    message.kind === "delegation" ||
    message.kind === "resume" ||
    message.kind === "notification" ||
    message.kind === "routine";
  if (message.role === "system" && isNote) {
    return (
      <div className="my-3 flex justify-start">
        <div className="chat-markdown max-w-[85%] rounded-xl border border-dashed border-[var(--color-border)] px-3.5 py-2 text-sm text-[var(--color-text-muted)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={STATIC_COMPONENTS}>
            {message.text}
          </ReactMarkdown>
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

  // A bubble that shrink-wraps its text is right for prose and wrong for a
  // chart: a plot squeezed into the width of the sentence above it is
  // unreadable. A chart-bearing answer claims the whole chat column instead.
  const hasChart = message.text.includes("```chart");

  return (
    <div className="flex justify-start mb-3">
      <div className={`flex items-start gap-2 ${hasChart ? "w-full" : "max-w-[85%]"}`}>
        <div className="mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--color-accent)]/20">
          <Bot className="h-3.5 w-3.5 text-[var(--color-accent)]" />
        </div>
        <div className={`min-w-0 ${hasChart ? "flex-1" : ""}`}>
          {/* The thought is the live thing only until the answer starts
              landing in this same bubble. Keying the collapse on `text` too
              means a lost `prompt_done` cannot leave it stuck open. */}
          {message.thought && <ThoughtBlock text={message.thought} live={live && !message.text} />}
          <ToolCallStatus toolCalls={message.toolCalls} live={live} />
          {message.text && (
            <div className="chat-markdown rounded-2xl rounded-tl-sm bg-[var(--color-surface-hover)] px-3.5 py-2 text-sm">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={live ? LIVE_COMPONENTS : STATIC_COMPONENTS}
              >
                {message.text}
              </ReactMarkdown>
            </div>
          )}
          {!message.text && message.toolCalls.length === 0 && !message.thought && (
            <div className="flex items-center gap-1.5 rounded-2xl rounded-tl-sm bg-[var(--color-surface-hover)] px-3.5 py-2 text-sm text-[var(--color-text-muted)]">
              {/* The bubble exists before anything is in it. While the turn is
                  live that gap is the agent working, so it spins like the tool
                  list does; a bubble that ended up empty just stays quiet. */}
              {live ? (
                <>
                  <Loader2 className="h-3 w-3 animate-spin" />
                  <span className="text-xs">Working...</span>
                </>
              ) : (
                "..."
              )}
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
