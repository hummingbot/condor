import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Loader2 } from "lucide-react";
import { useState } from "react";

import { ADMIN_AUDIT_KEY, type AuditEntry, adminApi } from "@/lib/admin-api";

import { timeAgo } from "./identity";

/**
 * The audit log, read in the panel that writes it (FEAT-088).
 *
 * `share_server`, `revoke_server_access`, `approve_user`, `block_user` and
 * `set_code_run_grant` have all been recording who did what to whom since they
 * were written, and nothing in the product has ever shown a line of it. This is
 * that, collapsed by default — it answers "what happened here", which is a
 * question asked after the fact, not while granting.
 */

/** How each recorded action reads as a sentence. Unknown actions print raw. */
const ACTIONS: Record<string, string> = {
  user_registered: "requested access",
  user_approved: "approved",
  user_rejected: "rejected",
  user_blocked: "blocked",
  user_unblocked: "unblocked",
  server_shared: "granted access to",
  server_access_revoked: "revoked access to",
  code_run_granted: "granted code_run to",
  code_run_revoked: "revoked code_run from",
  server_added: "added server",
  server_deleted: "deleted server",
};

function describe(entry: AuditEntry): string {
  const verb = ACTIONS[entry.action] ?? entry.action.replace(/_/g, " ");
  const target = entry.target_name || entry.target_id;
  // A server-scoped action names the server, and the person it was done to
  // lives in `details.target_user` — without that half the line reads as
  // "granted access to brigado_2" with no one in it.
  const who = entry.details?.target_user;
  const suffix = who ? ` (user ${who})` : "";
  return `${verb} ${target}${suffix}`.trim();
}

export function AuditTrail() {
  const [open, setOpen] = useState(false);

  const { data: entries = [], isLoading } = useQuery({
    queryKey: ADMIN_AUDIT_KEY,
    queryFn: () => adminApi.getAudit(50),
    retry: false,
    enabled: open,
  });

  return (
    <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-3 text-left"
      >
        {open ? (
          <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
        )}
        <span className="text-sm font-semibold text-[var(--color-text)]">
          Audit log
        </span>
        <span className="text-xs text-[var(--color-text-muted)]">
          who granted what to whom
        </span>
      </button>

      {open && (
        <div className="border-t border-[var(--color-border)] px-4 py-3">
          {isLoading ? (
            <div className="flex justify-center py-4 text-[var(--color-text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" />
            </div>
          ) : entries.length === 0 ? (
            <p className="py-2 text-xs text-[var(--color-text-muted)]">
              Nothing recorded yet.
            </p>
          ) : (
            <ul className="space-y-1.5">
              {entries.map((entry, i) => (
                <li
                  key={`${entry.timestamp}-${i}`}
                  className="flex flex-wrap items-baseline gap-x-1.5 text-xs"
                >
                  <span className="font-medium text-[var(--color-text)]">
                    {entry.actor_name || `User ${entry.actor_id ?? "?"}`}
                  </span>
                  <span className="text-[var(--color-text-muted)]">
                    {describe(entry)}
                  </span>
                  <span className="ml-auto shrink-0 text-[var(--color-text-muted)]">
                    {timeAgo(entry.timestamp)}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
