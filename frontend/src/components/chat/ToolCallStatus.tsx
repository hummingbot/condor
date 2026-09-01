import { Check, ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import type { ToolCall } from "@/hooks/useChatSocket";
import { useLiveDisclosure } from "@/hooks/useLiveDisclosure";
import { formatToolName, toolCallState } from "@/lib/formatters";

/** The one tool-call status icon. Shared so the completed/failed/pending
 *  mapping is not re-derived per call site; `size` tunes the glyph only. */
export function ToolCallStatusIcon({
  status,
  size = "h-3 w-3",
}: {
  status: string;
  size?: string;
}) {
  switch (toolCallState(status)) {
    case "ok":
      return <Check className={`${size} shrink-0 text-[var(--color-green)]`} />;
    case "error":
      return <X className={`${size} shrink-0 text-[var(--color-red)]`} />;
    default:
      return (
        <Loader2
          className={`${size} shrink-0 animate-spin text-[var(--color-text-muted)]`}
        />
      );
  }
}

/**
 * What the agent did before it answered, as one row.
 *
 * Reasoning and tool calls used to be two unrelated disclosures stacked above
 * the bubble, each with its own chevron and its own wording — two afterthoughts
 * where the running of tools is the thing that makes this a trading console
 * rather than a chat window. They are one strip now: a single line summarising
 * the run, which opens into the run itself in the order it happened.
 */
export function RunStrip({
  thought,
  toolCalls,
  live = false,
  thinking = false,
}: {
  /** The reasoning behind this turn, when the model exposed any. */
  thought?: string;
  toolCalls: ToolCall[];
  /** Whether the turn these belong to is the one still streaming. */
  live?: boolean;
  /** The reasoning is still arriving — no answer has started landing yet. */
  thinking?: boolean;
}) {
  // Computed above the empty early return so the hook below always runs;
  // `every` on an empty list is `true`, which reads correctly as "not running".
  const allDone = toolCalls.every((tc) => toolCallState(tc.status) !== "pending");
  // Open for as long as the turn is being written, not only while a call is
  // in flight. The calls worth watching are the ones that *return instantly*
  // and keep working elsewhere — `delegate` hands back a task id, a routine is
  // created and then runs — and gating on a pending status collapsed the list
  // the moment those returned, which is the moment their name is the only
  // thing telling the user what was set in motion.
  //
  // `live` alone still keeps history closed: a prompt abandoned mid-tool
  // persists its non-terminal status, so a replayed transcript can contain a
  // call that looks pending forever, and that bubble is not streaming.
  const { expanded, toggle } = useLiveDisclosure(live);

  if (!thought && toolCalls.length === 0) return null;

  const running = thinking || !allDone;

  return (
    <div className="mb-1.5">
      <button
        onClick={toggle}
        aria-expanded={expanded}
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3 shrink-0" />
        ) : (
          <ChevronRight className="h-3 w-3 shrink-0" />
        )}
        {running && <Loader2 className="h-3 w-3 shrink-0 animate-spin" />}
        {thought && <span>{thinking ? "Thinking..." : "Thought"}</span>}
        {thought && toolCalls.length > 0 && <span aria-hidden="true">·</span>}
        {toolCalls.length > 0 && (
          <span>
            {allDone ? "Used " : "Running "}
            {toolCalls.length} tool{toolCalls.length > 1 ? "s" : ""}
          </span>
        )}
      </button>
      {expanded && (
        // The run, in the order it happened, down a rule of its own: the
        // reasoning that led to the calls, then the calls.
        <div className="mt-1 ml-1.5 space-y-1 border-l border-[var(--chat-rule)] pl-3">
          {thought && (
            <div className="whitespace-pre-wrap text-xs italic text-[var(--color-text-muted)]">
              {thought}
            </div>
          )}
          {toolCalls.map((tc) => (
            <div
              key={tc.tool_call_id}
              className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]"
            >
              <ToolCallStatusIcon status={tc.status} />
              <span className="font-mono">{formatToolName(tc.title)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
