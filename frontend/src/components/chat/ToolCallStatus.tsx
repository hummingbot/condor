import { useState } from "react";
import { Check, ChevronDown, ChevronRight, Loader2, X } from "lucide-react";
import type { ToolCall } from "@/hooks/useChatSocket";

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case "completed":
      return <Check className="h-3 w-3 text-green-500" />;
    case "failed":
      return <X className="h-3 w-3 text-red-500" />;
    default:
      return <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-muted)]" />;
  }
}

function formatToolName(title: string): string {
  // Strip MCP prefixes like mcp__mcp-hummingbot__get_portfolio
  const name = title.includes("__") ? title.split("__").pop()! : title;
  return name.replace(/_/g, " ");
}

export function ToolCallStatus({ toolCalls }: { toolCalls: ToolCall[] }) {
  const [expanded, setExpanded] = useState(false);

  if (toolCalls.length === 0) return null;

  const allDone = toolCalls.every(
    (tc) => tc.status === "completed" || tc.status === "failed",
  );

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
              <StatusIcon status={tc.status} />
              <span className="font-mono">{formatToolName(tc.title)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
