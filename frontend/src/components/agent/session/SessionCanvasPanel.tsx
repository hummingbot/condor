import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight } from "lucide-react";
import { useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { api } from "@/lib/api";

// ── Session Canvas ──
//
// The agent's own thesis, written every tick and — until now — readable only
// inside the live report. The numbers say what happened; this says what the
// agent believed while it happened.

export function SessionCanvasPanel({ slug, sslug, sessionNum }: { slug: string; sslug: string; sessionNum: number }) {
  const [expanded, setExpanded] = useState(true);
  const { data } = useQuery({
    queryKey: ["strategy", slug, sslug, "session", sessionNum, "canvas"],
    queryFn: () => api.getSessionCanvas(slug, sslug, sessionNum),
    refetchInterval: 30000,
  });

  const sections = useMemo(() => {
    if (!data) return [];
    return data.section_order
      .map((key) => ({ key, title: data.section_titles[key] ?? key, body: data.sections[key] ?? "" }))
      .filter((s) => s.body.trim().length > 0);
  }, [data]);

  if (sections.length === 0) return null;

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-[var(--color-surface-hover)]"
      >
        <div className="flex items-center gap-2">
          <h3 className="text-xs font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
            Narrative — the agent's own words
          </h3>
          {data && data.last_revised_tick > 0 && (
            <span className="text-[10px] text-[var(--color-text-muted)]/70">
              last revised at tick #{data.last_revised_tick} · unverified
            </span>
          )}
        </div>
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        )}
      </button>
      {expanded && (
        <div className="space-y-4 border-t border-[var(--color-border)] p-4">
          {sections.map((s) => (
            <div key={s.key}>
              <h4 className="mb-1 text-[10px] font-bold uppercase tracking-widest text-[var(--color-text-muted)]">
                {s.title}
              </h4>
              <div className="chat-markdown text-sm leading-relaxed text-[var(--color-text)]">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{s.body}</ReactMarkdown>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
