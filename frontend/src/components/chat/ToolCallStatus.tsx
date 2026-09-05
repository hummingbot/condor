import { Check, ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import type { RunStep, ToolCall } from "@/hooks/useChatSocket";
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

/** One line of the opened run: a stretch of reasoning, or a call. */
type RunRow =
  | { kind: "thought"; text: string }
  | { kind: "tool"; call: ToolCall };

/**
 * The run as rows, in the order it happened (ARCH-330).
 *
 * `events` is the recorded order and the tool steps in it name calls by id, so
 * this is where the two halves are joined. Two properties it must hold:
 *
 * - **Nothing disappears.** A call the order does not name — an order that is
 *   somehow incomplete, or a step whose id nothing matches — is still shown,
 *   after the steps that were named. The strip accounts for every tool that
 *   ran; a prettier list that quietly loses one is worse than an ugly one.
 * - **No order, no invention.** A turn recorded before the order was kept has
 *   no `events` at all, and gets exactly the render it always got: the
 *   reasoning, then the calls. That is the honest shape of what is on disk —
 *   it is not a claim that the agent thought once and then ran everything.
 */
function runRows(
  thought: string | undefined,
  toolCalls: ToolCall[],
  events?: RunStep[],
): RunRow[] {
  if (!events || events.length === 0) {
    return [
      ...(thought ? [{ kind: "thought", text: thought } as const] : []),
      ...toolCalls.map((call) => ({ kind: "tool", call }) as const),
    ];
  }
  // Bound by position, not merely by id: a call announced twice appears twice
  // in `toolCalls` and is named twice in the order, and each step should take
  // the next one rather than both landing on the first.
  const unbound = new Map<string, ToolCall[]>();
  for (const call of toolCalls) {
    const queue = unbound.get(call.tool_call_id);
    if (queue) queue.push(call);
    else unbound.set(call.tool_call_id, [call]);
  }
  const rows: RunRow[] = [];
  for (const step of events) {
    if (step.type === "thought") {
      if (step.text) rows.push({ kind: "thought", text: step.text });
      continue;
    }
    const call = unbound.get(step.id)?.shift();
    if (call) rows.push({ kind: "tool", call });
  }
  for (const queue of unbound.values()) {
    for (const call of queue) rows.push({ kind: "tool", call });
  }
  return rows;
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
  events,
  live = false,
  thinking = false,
}: {
  /** The reasoning behind this turn, when the model exposed any. */
  thought?: string;
  toolCalls: ToolCall[];
  /**
   * The order the two above happened in, when it was recorded. Absent means
   * it was not — see `runRows`, which then draws what this always drew.
   */
  events?: RunStep[];
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
        // The run, in the order it happened, down a rule of its own — think,
        // call, think again, call again, as the model produced it, rather than
        // all of the reasoning collapsed above all of the calls.
        <div className="mt-1 ml-1.5 space-y-1 border-l border-[var(--chat-rule)] pl-3">
          {runRows(thought, toolCalls, events).map((row, i) =>
            // Positional keys: the rows are append-only for the life of a turn
            // and none of them holds state, so a row's index is its identity.
            row.kind === "thought" ? (
              <div
                key={i}
                className="whitespace-pre-wrap text-xs italic text-[var(--color-text-muted)]"
              >
                {row.text}
              </div>
            ) : (
              <div
                key={i}
                className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)]"
              >
                <ToolCallStatusIcon status={row.call.status} />
                <span className="font-mono">{formatToolName(row.call.title)}</span>
              </div>
            ),
          )}
        </div>
      )}
    </div>
  );
}
