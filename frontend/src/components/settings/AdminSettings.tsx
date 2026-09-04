import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Loader2, RefreshCw } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { ADMIN_PEOPLE_KEY, adminApi } from "@/lib/admin-api";

import { AuditTrail } from "./admin/AuditTrail";
import { ConnectedPeopleList } from "./admin/PeopleList";

/**
 * Admin-only people administration (ARCH-177, FEAT-088).
 *
 * The tab answers two questions from one screen: *who are these people* and
 * *what can each of them reach*. Before FEAT-088 it answered neither — it
 * listed bare user ids carrying a single capability checkbox, and server
 * access, the thing an admin actually has to decide when someone joins, had no
 * web surface at all.
 *
 * This file is only the shell. The list fetches itself, each row owns its own
 * mutations, and nothing here holds state that a re-read from the server would
 * contradict.
 */
export function AdminSettings() {
  const qc = useQueryClient();
  const [params] = useSearchParams();

  // Deep link from a server card's "Shared with" line: open that person.
  const requested = Number(params.get("user"));
  const initialUserId = Number.isFinite(requested) && requested > 0 ? requested : undefined;

  const refreshMut = useMutation({
    mutationFn: adminApi.refreshNames,
    onSuccess: () => qc.invalidateQueries({ queryKey: ADMIN_PEOPLE_KEY }),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-2xl text-xs leading-relaxed text-[var(--color-text-muted)]">
          Everyone who has ever contacted the bot, and the servers each of them can
          reach. Approving, blocking and every grant here is recorded in the audit
          log and takes effect immediately, with no restart.
        </p>
        <button
          type="button"
          onClick={() => refreshMut.mutate()}
          disabled={refreshMut.isPending}
          title="Ask Telegram what the records with no stored name are called"
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-50"
        >
          {refreshMut.isPending ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          Refresh names
        </button>
      </div>

      {refreshMut.isSuccess && (
        <p className="text-xs text-[var(--color-text-muted)]">
          Named {refreshMut.data.resolved} of {refreshMut.data.checked} records
          {refreshMut.data.failed > 0
            ? ` · ${refreshMut.data.failed} did not answer (they may have blocked the bot)`
            : ""}
          .
        </p>
      )}
      {refreshMut.isError && (
        <p className="text-xs text-[var(--color-red)]">
          {(refreshMut.error as Error).message}
        </p>
      )}

      <ConnectedPeopleList initialUserId={initialUserId} />

      <AuditTrail />
    </div>
  );
}
