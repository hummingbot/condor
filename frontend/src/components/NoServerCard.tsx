import { useQuery } from "@tanstack/react-query";
import { Server } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { api } from "@/lib/api";

/**
 * Shared empty-state card shown when no server is selected.
 *
 * Two different situations reach it and they need different instructions
 * (FU-09): servers exist and none is selected — pick one, from the menu in the
 * top bar — or there is no server at all, where "select a server" points at a
 * list with nothing in it and the thing to do is add one. Callers still phrase
 * their message around the sidebar the selector used to live in, so that part
 * is corrected here rather than in each of them.
 */
export function NoServerCard({
  message = "Select a server from the menu in the top bar to get started.",
}: {
  message?: string;
}) {
  const navigate = useNavigate();
  // The same entry the header's selector polls, so this adds no request.
  const { data: servers } = useQuery({ queryKey: ["servers"], queryFn: api.getServers });
  const none = servers !== undefined && servers.length === 0;

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center max-w-sm">
        <Server className="h-10 w-10 mx-auto mb-3 text-[var(--color-text-muted)]" />
        <h2 className="text-lg font-semibold mb-1">{none ? "No Server Yet" : "No Server Selected"}</h2>
        <p className="text-sm text-[var(--color-text-muted)]">
          {none
            ? "Add a Hummingbot API server in Settings to get started."
            : message.replace("from the sidebar", "from the menu in the top bar")}
        </p>
        <button
          onClick={() => navigate("/settings?tab=servers")}
          className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          <Server className="h-3 w-3" />
          Go to Settings → Servers
        </button>
      </div>
    </div>
  );
}
