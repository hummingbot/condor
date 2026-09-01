import { isValidElement, memo, useEffect, useRef, useState, type ReactNode } from "react";
import type { Element, ElementContent } from "hast";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Bell,
  Check,
  Copy,
  CornerDownLeft,
  CornerDownRight,
  Loader2,
  ShieldAlert,
  Square,
  Zap,
} from "lucide-react";
import type { ChatMessage as ChatMessageType } from "@/hooks/useChatSocket";
import { agentColor } from "@/lib/agentColor";
import { copyText } from "@/lib/clipboard";
import { ChartBlock } from "./ChartBlock";
import { RunStrip } from "./ToolCallStatus";

/** Flatten a rendered subtree back into the source text it was parsed from. */
function nodeText(children: ReactNode): string {
  if (typeof children === "string") return children;
  if (typeof children === "number") return String(children);
  if (Array.isArray(children)) return children.map(nodeText).join("");
  if (isValidElement<{ children?: ReactNode }>(children)) {
    return nodeText(children.props.children);
  }
  return "";
}

/**
 * A cell holding one number and nothing else — a price, a size, a percentage.
 *
 * Deliberately strict: a cell that also carries a word ("0.9667 BTC") is a
 * sentence, not a figure, and dragging it to the right would only tear it away
 * from the label beside it. The typographic minus (U+2212) is accepted beside
 * the hyphen because that is the one models actually write in a table.
 */
const NUMERIC_CELL = /^[+\-\u2212]?(?:[$€£]|R\$)?\s?\d[\d,_ ]*(\.\d+)?\s?%?$/;

/** The text a hast subtree renders to, for the questions markdown cannot
 *  answer about its own content. */
function hastText(node: Element | ElementContent): string {
  if (node.type === "comment") return "";
  if ("value" in node) return node.value;
  if ("children" in node) return node.children.map(hastText).join("");
  return "";
}

/**
 * Which of a table's columns hold numbers, 1-based, as an attribute token list.
 *
 * Decided per *column* rather than per cell: a column of amounts reads as a
 * column when its digits line up on the right, but a lone number in a column of
 * prose ("Controllers | 6") shoved to the far edge reads as a mistake. So a
 * column is numeric only when every body cell in it is, and the header follows
 * the column it heads.
 *
 * Asked here rather than in CSS because CSS cannot: `:has()` matches a cell's
 * elements, never its text. The answer travels as `data-num-cols` and the
 * alignment itself stays in the stylesheet with the rest of the table's rules.
 */
function numericColumns(node?: Element): string | undefined {
  if (!node) return undefined;
  const columns: (boolean | undefined)[] = [];
  const rows = (function collect(el: Element): Element[] {
    return el.children.flatMap((child) =>
      child.type === "element"
        ? child.tagName === "tr"
          ? [child]
          : collect(child)
        : [],
    );
  })(node);
  for (const row of rows) {
    let index = 0;
    for (const cell of row.children) {
      if (cell.type !== "element") continue;
      if (cell.tagName !== "td") continue; // headers follow their column
      const text = hastText(cell).trim();
      if (text !== "") {
        columns[index] = (columns[index] ?? true) && NUMERIC_CELL.test(text);
      }
      index++;
    }
  }
  const numeric = columns.flatMap((isNum, i) => (isNum ? [String(i + 1)] : []));
  return numeric.length > 0 ? numeric.join(" ") : undefined;
}

/**
 * What the markdown pipeline can render, beyond markdown.
 *
 * A ```chart fence becomes a real chart; every other fence stays the code
 * block it already was. The override sits on `pre` rather than on `code`
 * because react-markdown v10 dropped the `inline` prop and still wraps fences
 * in a <pre> — replacing the wrapper is what keeps the chart out of a
 * whitespace-preserving box it was never meant to live in.
 *
 * The `table` override is the other thing markdown cannot say: a column of
 * amounts only reads as a column when its digits align on the right, and GFM
 * alignment is the author's to set — the model does not set it.
 */
function markdownComponents(live: boolean): Components {
  return {
    pre({ children, ...props }) {
      const child = Array.isArray(children) ? children[0] : children;
      if (isValidElement<{ className?: string; children?: ReactNode }>(child)) {
        const className = child.props.className ?? "";
        if (className.split(" ").includes("language-chart")) {
          return <ChartBlock raw={nodeText(child.props.children)} live={live} />;
        }
      }
      return <pre {...props}>{children}</pre>;
    },
    table({ node, children, ...props }) {
      return (
        <table {...props} data-num-cols={numericColumns(node)}>
          {children}
        </table>
      );
    },
  };
}

// Both variants are built once: a fresh `components` object per render would
// remount every block in the bubble on each streamed chunk.
const LIVE_COMPONENTS = markdownComponents(true);
const STATIC_COMPONENTS = markdownComponents(false);

/** The turn's clock time, in the reader's own timezone. */
function clockTime(ts?: number): string {
  if (!ts) return "";
  // The reader's own convention, 12h or 24h — and `timeStyle` rather than
  // 2-digit parts, which pads a 12h clock into "07:19 PM".
  return new Date(ts * 1000).toLocaleTimeString([], { timeStyle: "short" });
}

/** Copy a turn's text. Keyboard-reachable: it is revealed by focus as well as
 *  by hover, so it is not a mouse-only affordance. */
function CopyTurn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, []);

  return (
    <button
      onClick={async () => {
        if (!(await copyText(text))) return;
        setCopied(true);
        if (timer.current) clearTimeout(timer.current);
        timer.current = setTimeout(() => setCopied(false), 1500);
      }}
      className="rounded p-0.5 text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      title="Copy this message"
      aria-label="Copy this message"
    >
      {copied ? (
        <Check className="h-3.5 w-3.5 text-[var(--color-green)]" />
      ) : (
        <Copy className="h-3.5 w-3.5" />
      )}
    </button>
  );
}

/** The time and the copy button, held together so every turn exposes the same
 *  pair in the same place: quiet until the turn is hovered or focused. */
function TurnActions({ ts, text }: { ts?: number; text: string }) {
  const time = clockTime(ts);
  return (
    <div className="flex items-center gap-1.5 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
      {time && (
        <time
          dateTime={ts ? new Date(ts * 1000).toISOString() : undefined}
          className="text-[10px] tabular-nums text-[var(--color-text-muted)]"
        >
          {time}
        </time>
      )}
      {text && <CopyTurn text={text} />}
    </div>
  );
}

/** An eyebrow above a turn: who or what it is, in the gutter's own colour. */
function TurnLabel({ label, color }: { label: string; color: string }) {
  return (
    <span
      className="text-[10px] font-semibold uppercase tracking-[0.14em]"
      style={{ color }}
    >
      {label}
    </span>
  );
}

/** Out-of-band entries in the transcript: nobody typed them, and each one is
 *  an event worth naming rather than an anonymous inset box. */
const NOTE_KINDS: Record<string, { label: string; Icon: typeof Zap }> = {
  delegation: { label: "Delegation", Icon: CornerDownRight },
  resume: { label: "Resumed", Icon: CornerDownLeft },
  notification: { label: "Notified", Icon: Bell },
  routine: { label: "Routine", Icon: Zap },
};

export const ChatMessageView = memo(function ChatMessageView({
  message,
  live = false,
  agentName = "Assistant",
}: {
  message: ChatMessageType;
  /**
   * This is the turn the answer is currently streaming into. Only the
   * disclosure blocks care: it is what lets the reasoning and the tool list
   * show themselves while they are being produced, and close once they are
   * not the interesting part of the turn anymore.
   */
  live?: boolean;
  /**
   * Who said this. The thread resolves it per message — a handover means the
   * transcript above and below it is two different counterparts — and it is
   * what the gutter takes its colour from.
   */
  agentName?: string;
}) {
  // Key material in what was just sent (FEAT-056). It gets the warning
  // treatment rather than the quiet note: half of these say text the user can
  // see is gone, and the other half say a value is still there and should be
  // rotated. Neither is something to scroll past — it keeps the amber fill,
  // the one filled ground in the transcript, and takes the gutter geometry
  // only so it lines up with everything else.
  if (message.role === "system" && message.kind === "secret_notice") {
    return (
      <div className="group mb-4 border-l-2 border-[var(--color-yellow)] bg-amber-500/10 py-2 pl-3 pr-3">
        <div className="mb-1 flex items-center gap-1.5">
          <ShieldAlert className="h-3.5 w-3.5 shrink-0 text-[var(--color-yellow)]" />
          <TurnLabel label="Key material" color="var(--color-yellow)" />
          <div className="ml-auto">
            <TurnActions ts={message.ts} text={message.text} />
          </div>
        </div>
        <div className="chat-markdown text-sm text-[var(--color-text-muted)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={STATIC_COMPONENTS}>
            {message.text}
          </ReactMarkdown>
        </div>
      </div>
    );
  }

  // A delegation's outcome is the answer to something asked minutes ago, so it
  // is prose, not a label: the divider treatment (uppercase, nowrap, 10px)
  // would render it as one unreadable line. It stays visibly not-the-agent's
  // own words — a muted gutter rather than an agent's colour — while still
  // being readable.
  //
  // A `resume` note is the same shape: it is what a finished background task
  // said to the agent to make it continue. Nobody typed it, so it must not
  // render as a user bubble. So is a `notification`: the agent announced it to
  // the user out of band, and the transcript records that it did. And so is a
  // `routine` outcome — a run's result, often a multi-line error, which the
  // divider rendered as one shouting, unreadable, un-wrapped line.
  const note = message.kind ? NOTE_KINDS[message.kind] : undefined;
  if (message.role === "system" && note) {
    const { label, Icon } = note;
    return (
      <div className="group mb-4 border-l-2 border-[var(--chat-rule)] pl-3">
        <div className="mb-1 flex items-center gap-1.5">
          <Icon className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
          <TurnLabel label={label} color="var(--color-text-muted)" />
          <div className="ml-auto">
            <TurnActions ts={message.ts} text={message.text} />
          </div>
        </div>
        <div className="chat-markdown text-sm text-[var(--color-text-muted)]">
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

  // The user speaks in a bubble; the agent publishes a document. Keeping the
  // bubble here is what makes that inversion legible — and capping it at 70%
  // keeps a one-line question reading as an aside rather than as a banner.
  if (message.role === "user") {
    return (
      <div className="group mb-4 flex items-start justify-end gap-2">
        <div className="mt-1">
          <TurnActions ts={message.ts} text={message.text} />
        </div>
        <div className="max-w-[70%] rounded-2xl rounded-tr-sm bg-[var(--color-primary)] px-3.5 py-2 text-sm text-[var(--on-primary)]">
          <p className="whitespace-pre-wrap">{message.text}</p>
        </div>
      </div>
    );
  }

  // The agent's turn: a block on the page ground, owned by a left gutter in
  // its own colour that carries the whole turn — name, run strip and answer.
  //
  // Not a bubble. A bubble drawn on `--color-surface-hover` was invisible in
  // light theme (1.06:1 against the page), and shrink-wrapping the answer to
  // its longest sentence squeezed every table and chart it contained — which
  // is why a chart-bearing answer needed a carve-out to claim the column. On
  // the page ground every answer has the column, so that case is the ordinary
  // one now.
  const color = agentColor(agentName);

  return (
    <div className="group mb-4 border-l-2 pl-3" style={{ borderColor: color }}>
      <div className="mb-1 flex items-center gap-2">
        <TurnLabel label={agentName} color={color} />
        <div className="ml-auto">
          <TurnActions ts={message.ts} text={message.text} />
        </div>
      </div>
      {/* The thought is the live thing only until the answer starts landing in
          this same turn. Keying the collapse on `text` too means a lost
          `prompt_done` cannot leave it stuck open. */}
      <RunStrip
        thought={message.thought}
        toolCalls={message.toolCalls}
        live={live}
        thinking={live && !message.text}
      />
      {message.text && (
        <div className="chat-markdown text-sm">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={live ? LIVE_COMPONENTS : STATIC_COMPONENTS}
          >
            {message.text}
          </ReactMarkdown>
        </div>
      )}
      {!message.text && message.toolCalls.length === 0 && !message.thought && (
        <div className="flex items-center gap-1.5 text-sm text-[var(--color-text-muted)]">
          {/* The turn exists before anything is in it. While it is live that
              gap is the agent working, so it spins like the run strip does; a
              turn that ended up empty just stays quiet. */}
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
      {/* The user redirected the agent here. Subdued on purpose: the partial is
          still worth reading and its context carried into the next turn, so
          this is a seam, not a failure. */}
      {message.interrupted && (
        <div className="mt-1 flex items-center gap-1.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
          <Square className="h-2.5 w-2.5" />
          <span className="whitespace-nowrap">Interrupted</span>
          <div className="h-px flex-1 bg-[var(--color-border)]" />
        </div>
      )}
    </div>
  );
});
