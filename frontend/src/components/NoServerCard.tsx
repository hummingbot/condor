import { Server } from "lucide-react";
import { useNavigate } from "react-router-dom";

/** Shared empty-state card shown when no server is selected. */
export function NoServerCard({
  message = "Select a server from the sidebar to get started.",
}: {
  message?: string;
}) {
  const navigate = useNavigate();

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center max-w-sm">
        <Server className="h-10 w-10 mx-auto mb-3 text-[var(--color-text-muted)]" />
        <h2 className="text-lg font-semibold mb-1">No Server Selected</h2>
        <p className="text-sm text-[var(--color-text-muted)]">{message}</p>
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
