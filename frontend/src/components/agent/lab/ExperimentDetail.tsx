import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { useMemo } from "react";

import { SnapshotBody } from "@/components/agent/session/Snapshot";
import { api } from "@/lib/api";
import { type ParsedSnapshot, parseSnapshot } from "@/lib/parse-agent";

/**
 * A dry run or a single tick — one file, one tick, no journal.
 *
 * It is the same document a session snapshot is (`parseSnapshot` reads both), so
 * it renders through the same body rather than through a second copy of it. The
 * one thing added is the error banner: a tick whose model call failed writes the
 * raw error as its Agent Response, and that is the only outcome a dry run has.
 */
export function ExperimentDetail({
  slug,
  sslug,
  number,
}: {
  slug: string;
  sslug: string;
  number: number;
}) {
  const { data, isLoading } = useQuery({
    queryKey: ["strategy", slug, sslug, "experiment", number],
    queryFn: () => api.getExperiment(slug, sslug, number),
    enabled: number > 0,
  });

  const content = data?.content;
  const parsed = useMemo<ParsedSnapshot | null>(
    () => (content ? parseSnapshot(content) : null),
    [content],
  );

  if (isLoading || !parsed) {
    return (
      <div className="flex h-48 items-center justify-center">
        <div className="h-5 w-5 animate-spin rounded-full border-2 border-[var(--color-border)] border-t-[var(--color-primary)]" />
      </div>
    );
  }

  const failed = isErrorResponse(parsed.agentResponse);

  return (
    <div className="space-y-4">
      {failed && (
        <div className="rounded-lg border border-red-500/40 bg-red-500/5 p-4">
          <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-red-400">
            <AlertTriangle className="h-3.5 w-3.5" />
            Agent Error
          </h3>
          <div className="whitespace-pre-wrap font-mono text-xs text-red-300">
            {parsed.agentResponse}
          </div>
        </div>
      )}
      <SnapshotBody parsed={parsed} />
    </div>
  );
}

/**
 * A tick whose model call failed writes the raw error string as its Agent
 * Response (e.g. `(error: status_code: 404, ...)`).
 */
function isErrorResponse(text: string): boolean {
  const t = text.trimStart();
  return /^\(?error\b/i.test(t) || /\berror: status_code:/i.test(t);
}
