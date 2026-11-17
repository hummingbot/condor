import { Compass } from "lucide-react";
import { Link, useLocation } from "react-router-dom";

/** What an address that matches no route renders, inside the shell (FU-13). */
export function NotFound() {
  const { pathname } = useLocation();

  return (
    <div className="flex items-center justify-center min-h-[60vh]">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-8 text-center max-w-sm">
        <Compass className="h-10 w-10 mx-auto mb-3 text-[var(--color-text-muted)]" />
        <h2 className="text-lg font-semibold mb-1">Page Not Found</h2>
        <p className="text-sm text-[var(--color-text-muted)]">
          Nothing lives at <code className="break-all font-mono text-xs">{pathname}</code>.
        </p>
        <Link
          to="/"
          className="mt-4 inline-flex items-center justify-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-3 py-1.5 text-[11px] font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          Back to home
        </Link>
      </div>
    </div>
  );
}
