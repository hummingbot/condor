import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import type { Delegation } from "@/lib/api";

/**
 * A delegation's outcome and nothing else: its result as the narrative markdown
 * it is, or its error as the raw text it is.
 *
 * Its one consumer is `DelegationSheet`, which already renders the ask above it
 * and the status and elapsed time in its own subtitle — so this renders neither,
 * and gives the body the full height of the sheet's scrolling container.
 */
export function DelegationDetail({ delegation: d }: { delegation: Delegation }) {
  const body = (d.status === "error" ? d.error : d.result)?.trim();

  return (
    <div>
      <p className="mb-1 text-[10px] uppercase tracking-wider text-[var(--color-text-muted)]">
        {d.status === "error" ? "Error" : "Result"}
      </p>
      {d.status === "error" ? (
        // Errors are raw (stack traces / plain messages) — keep monospace.
        <pre className="whitespace-pre-wrap break-words font-mono text-xs text-red-300">
          {body || "(no output)"}
        </pre>
      ) : body ? (
        // A delegation result is the agent's narrative final answer — render markdown.
        <div className="chat-markdown text-xs text-[var(--color-text)]">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{body}</ReactMarkdown>
        </div>
      ) : (
        <p className="text-xs text-[var(--color-text-muted)]">
          {d.status === "running" ? "Running…" : "(no output)"}
        </p>
      )}
    </div>
  );
}
