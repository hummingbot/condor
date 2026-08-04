import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import type { ToolCall } from "@/hooks/useChatSocket";
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

export function ToolCallStatus({ toolCalls }: { toolCalls: ToolCall[] }) {
  const [expanded, setExpanded] = useState(false);

  if (toolCalls.length === 0) return null;

  const allDone = toolCalls.every((tc) => toolCallState(tc.status) !== "pending");

  return (
    <div className="my-1.5">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-1.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] transition-colors"
      >
        {expanded ? (
          <ChevronDown className="h-3 w-3" />
        ) : (
          <ChevronRight className="h-3 w-3" />
        )}
        {allDone ? (
          <span>Used {toolCalls.length} tool{toolCalls.length > 1 ? "s" : ""}</span>
        ) : (
          <span className="flex items-center gap-1">
            <Loader2 className="h-3 w-3 animate-spin" />
            Running tools...
          </span>
        )}
      </button>
      {expanded && (
        <div className="mt-1 ml-4 space-y-0.5">
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
