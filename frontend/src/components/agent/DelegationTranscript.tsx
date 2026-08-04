import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { ToolCallStatusIcon } from "@/components/chat/ToolCallStatus";
import { type DelegationEvent, api } from "@/lib/api";
import { formatToolName } from "@/lib/formatters";

/** Reasoning, collapsed by default — it is context, not the answer. */
function ThoughtRow({ text }: { text: string }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div>
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)]"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        Reasoning
      </button>
      {expanded && (
        <div className="ml-4 mt-1 whitespace-pre-wrap text-xs italic text-[var(--color-text-muted)]">
          {text}
        </div>
      )}
    </div>
  );
}

/** One tool call: name + status always, input/output on demand. */
function ToolRow({
  event,
}: {
  event: Extract<DelegationEvent, { type: "tool" }>;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetail = event.input !== null || event.output !== null;

  return (
    <div>
      <button
        onClick={() => hasDetail && setExpanded(!expanded)}
        disabled={!hasDetail}
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] transition-colors hover:text-[var(--color-text)] disabled:cursor-default disabled:hover:text-[var(--color-text-muted)]"
      >
        {hasDetail ? (
          expanded ? (
            <ChevronDown className="h-3 w-3" />
          ) : (
            <ChevronRight className="h-3 w-3" />
          )
        ) : (
          <span className="h-3 w-3" />
        )}
        <ToolCallStatusIcon status={event.status} />
        <span className="font-mono">{formatToolName(event.name)}</span>
      </button>
      {expanded && (
        <div className="ml-4 mt-1 space-y-1.5">
          {event.input !== null && (
            <div>
              <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                Input
              </p>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-words rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-2 font-mono text-[11px] text-[var(--color-text-muted)]">
                {JSON.stringify(event.input, null, 2)}
              </pre>
            </div>
          )}
          {event.output !== null && (
            <div>
              <p className="mb-0.5 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
                Output
              </p>
              <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words rounded border border-[var(--color-border)] bg-[var(--color-surface)] p-2 font-mono text-[11px] text-[var(--color-text-muted)]">
                {event.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * What a delegation actually did: its reasoning and every tool call, in order.
 *
 * The delegate counterpart of `SessionReviewer` — except nothing is parsed here.
 * The backend already folds the stream into exactly these three shapes and
 * patches tool entries in place, so a poll returns the live state of every call
 * and rows keyed by `id` update instead of remounting: a tool row expanded while
 * the call was `in_progress` stays expanded when it completes.
 *
 * Owns its own query so nothing polls unless this is mounted (i.e. unless a
 * sheet is open), and the poll stops the moment the task leaves `running`.
 */
export function DelegationTranscript({ taskId }: { taskId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["delegation-events", taskId],
    queryFn: () => api.getDelegationEvents(taskId),
    refetchInterval: (q) => (q.state.data?.status === "running" ? 2000 : false),
  });

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <Loader2 className="h-3 w-3 animate-spin" />
        Loading transcript…
      </div>
    );
  }

  if (error) {
    return (
      <p className="text-xs text-red-400">
        Could not load the transcript. A delegation only keeps one for as long as
        the process that ran it.
      </p>
    );
  }

  const events = data?.events ?? [];
  if (events.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)]">
        {data?.status === "running"
          ? "Waiting for the agent's first move…"
          : "(no events captured)"}
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {events.map((ev, i) => {
        if (ev.type === "thought") {
          return <ThoughtRow key={`thought-${i}`} text={ev.text} />;
        }
        if (ev.type === "tool") {
          // Keyed by the tool_call_id, which survives the server-side patch.
          return <ToolRow key={ev.id || `tool-${i}`} event={ev} />;
        }
        return (
          <div
            key={`text-${i}`}
            className="chat-markdown text-xs text-[var(--color-text)]"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{ev.text}</ReactMarkdown>
          </div>
        );
      })}
    </div>
  );
}
