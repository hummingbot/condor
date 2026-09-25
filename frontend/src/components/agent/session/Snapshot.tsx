import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Wrench } from "lucide-react";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { snapshotQueryOptions } from "@/hooks/useSnapshotBubbles";
import { type ParsedSnapshot, type ToolCall, parseSnapshot } from "@/lib/parse-agent";
import { toolCallState } from "@/lib/formatters";

// ── Snapshot Detail ──
//
// One tick: what it saw, what it decided, what it called. This used to be
// private behind `SessionSnapshots`, a picker column plus this detail, reachable
// only three clicks deep inside an overlay with no URL. The Lab's tick spine is
// that picker, so the pair is gone and this is exported (FEAT-099).

export function SnapshotDetail({ slug, sslug, sessionNum, tick }: { slug: string; sslug: string; sessionNum: number; tick: number }) {
  // Same options the marker previews use, so a tick already previewed renders
  // straight from cache — no second request, no spinner.
  const { data, isLoading } = useQuery({
    ...snapshotQueryOptions(slug, sslug, sessionNum, tick),
    enabled: tick > 0,
  });

  const parsed = useMemo<ParsedSnapshot | null>(() => {
    if (!data?.content) return null;
    return parseSnapshot(data.content);
  }, [data?.content]);

  if (isLoading) {
    return (
      <div className="flex h-48 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  if (!parsed) {
    return <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">Select a snapshot to view details.</p>;
  }

  return <SnapshotBody parsed={parsed} />;
}

/**
 * The rendering half of a snapshot, over an already-parsed one.
 *
 * Split out because a dry run is the *same document* under another name: an
 * `experiment_N.md` is one tick, parsed by the same `parseSnapshot`, and the
 * Lab renders it here rather than keeping a second copy of this layout the way
 * `SessionReviewer` did.
 */
export function SnapshotBody({ parsed }: { parsed: ParsedSnapshot }) {
  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-lg font-bold text-[var(--color-text)]">#{parsed.tick}</span>
        <span className="text-sm text-[var(--color-text-muted)]">{parsed.timestamp}</span>
      </div>

      {/* System Prompt */}
      {parsed.systemPrompt && (
        <SystemPromptCard prompt={parsed.systemPrompt} charCount={parsed.systemPromptLength} />
      )}

      {/* Agent Response.
          Through the chat's own markdown renderer (FEAT-103), not printed as
          plain text. What the model writes here *is* markdown — the parser's
          own comment two files over says so — so `**bold**` and `| tables |`
          were reaching the reader as literal characters, and streamed chunks
          read glued together. It is the text the whole workspace exists to
          surface, and `chat-markdown` is already the house style for it. */}
      {parsed.agentResponse && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-5">
          <h4 className="mb-3 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Agent Response</h4>
          <div
            data-agent-response
            className="chat-markdown text-sm leading-relaxed text-[var(--color-text)]"
          >
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {parsed.agentResponse}
            </ReactMarkdown>
          </div>
        </div>
      )}

      {/* Tool Calls */}
      {parsed.toolCalls.length > 0 && (
        <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
          <h4 className="mb-3 flex items-center gap-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            <Wrench className="h-3 w-3" /> Tool Calls ({parsed.toolCalls.length})
          </h4>
          <div className="flex flex-wrap gap-2">
            {parsed.toolCalls.map((tc) => (
              <ToolCallChip key={tc.number} tc={tc} />
            ))}
          </div>
        </div>
      )}

      {/* Risk + Executor side by side */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {parsed.riskState && (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Risk State</h4>
            <div className="space-y-1 font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {parsed.riskState.split("\n").map((line, i) => {
                const isBlocked = line.includes("BLOCKED");
                const isActiveLine = line.includes("ACTIVE");
                return (
                  <div key={i} className={isBlocked ? "text-red-400" : isActiveLine ? "text-emerald-400" : ""}>
                    {line.replace(/^- /, "")}
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {parsed.executorState && (
          <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
            <h4 className="mb-2 text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">Executor State</h4>
            <pre className="whitespace-pre-wrap font-mono text-xs leading-relaxed text-[var(--color-text-muted)]">
              {parsed.executorState}
            </pre>
          </div>
        )}
      </div>

      {/* Stats Footer */}
      {parsed.stats.duration > 0 && (
        <div className="flex flex-wrap gap-4 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-3 font-mono text-xs text-[var(--color-text-muted)]">
          <span>Duration: <strong className="text-[var(--color-text)]">{parsed.stats.duration.toFixed(1)}s</strong></span>
        </div>
      )}
    </div>
  );
}

// ── Tool Call Chip ──

export function ToolCallChip({ tc }: { tc: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = tc.input || tc.output;
  const state = toolCallState(tc.status);
  const dotColor =
    state === "ok"
      ? "bg-[var(--color-green)]"
      : state === "error"
        ? "bg-[var(--color-red)]"
        : "bg-[var(--color-text-muted)]";

  const shortName = tc.name.replace(/^mcp__\w+__/, "");

  if (!hasDetails) {
    return (
      <div className="flex items-center gap-1.5 rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)] px-2.5 py-1.5">
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
        <span className="font-mono text-[11px] text-[var(--color-text)]">{shortName}</span>
      </div>
    );
  }

  return (
    <div className="w-full rounded-md border border-[var(--color-border)]/50 bg-[var(--color-bg)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between px-2.5 py-1.5 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-1.5">
          <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotColor}`} />
          <span className="font-mono text-[11px] text-[var(--color-text)]">{shortName}</span>
        </div>
        {expanded ? <ChevronDown className="h-3 w-3 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3 w-3 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && (
        <div className="space-y-2 border-t border-[var(--color-border)]/30 p-3">
          {tc.input && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Input</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                {tc.input}
              </pre>
            </div>
          )}
          {tc.output && (
            <div>
              <span className="mb-1 block text-[10px] font-bold uppercase tracking-wider text-[var(--color-text-muted)]">Output</span>
              <pre className="max-h-40 overflow-auto rounded-md bg-[var(--color-surface)] p-2 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
                {tc.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── System Prompt Card ──

export function SystemPromptCard({ prompt, charCount }: { prompt: string; charCount: number }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex w-full items-center justify-between p-4 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-2">
          <h4 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">System Prompt</h4>
          <span className="text-[10px] text-[var(--color-text-muted)]">({charCount.toLocaleString()} chars)</span>
        </div>
        {expanded ? <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" /> : <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />}
      </button>
      {expanded && (
        <pre className="max-h-96 overflow-auto border-t border-[var(--color-border)] p-4 font-mono text-[11px] leading-relaxed text-[var(--color-text-muted)]">
          {prompt}
        </pre>
      )}
    </div>
  );
}
