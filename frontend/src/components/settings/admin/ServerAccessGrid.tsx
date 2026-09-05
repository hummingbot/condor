import { Loader2 } from "lucide-react";

import type { AdminPerson } from "@/lib/admin-api";

/**
 * One person's access to every registered server (FEAT-088).
 *
 * Two states per server, *no access* and *trader*, because those are the two
 * `share_server` can express. Ownership is shown, never offered: `share_server`
 * writes `shared_with` and never `owner_id`, so a transfer would need a second
 * writer on the owner field, and the server refuses it with that reason.
 *
 * The whole grid is disabled while the person is not approved, and says why —
 * `share_server` refuses a target who is not approved, and a control that
 * silently no-ops is worse than one that explains itself. Approving happens in
 * the panel directly above, which is the point of putting them together.
 */
export function ServerAccessGrid({
  person,
  onSet,
  pendingServer,
  error,
}: {
  person: AdminPerson;
  onSet: (server: string, permission: string) => void;
  pendingServer: string | null;
  error?: string;
}) {
  const approved = person.role === "user" || person.is_admin;
  const reason = !person.known
    ? "This id holds access but has no user record. It can only be revoked."
    : !approved
      ? "Approve this person before granting server access."
      : "";

  if (person.servers.length === 0) {
    return (
      <p className="text-xs text-[var(--color-text-muted)]">
        No servers are registered yet.
      </p>
    );
  }

  return (
    <div className="space-y-1.5">
      {reason && (
        <p className="text-xs text-[var(--color-text-muted)]">{reason}</p>
      )}

      {person.servers.map((grant) => {
        const owned = grant.permission === "owner" && !grant.implicit;
        // An implicit grant is the admin role showing through; owning the
        // server is a fact about the config. Neither is a toggle.
        const locked = grant.implicit || owned || !approved || !person.known;
        const busy = pendingServer === grant.server;

        return (
          <div
            key={grant.server}
            className="flex items-center justify-between gap-3 rounded-md border border-[var(--color-border)] px-3 py-2"
          >
            <span className="truncate text-xs font-medium text-[var(--color-text)]">
              {grant.server}
            </span>

            {grant.implicit ? (
              <span className="shrink-0 text-xs text-[var(--color-text-muted)]">
                all servers · by role
              </span>
            ) : owned ? (
              <span className="shrink-0 text-xs text-[var(--color-text-muted)]">
                owner
              </span>
            ) : (
              <div className="flex shrink-0 items-center gap-1">
                {busy && (
                  <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-muted)]" />
                )}
                {(
                  [
                    ["", "No access"],
                    ["trader", "Trader"],
                  ] as const
                ).map(([value, label]) => {
                  const active = grant.permission === value;
                  return (
                    <button
                      key={value || "none"}
                      type="button"
                      onClick={() => onSet(grant.server, value)}
                      // Revoking must stay available even for an unknown id —
                      // it is the only action that row has.
                      disabled={
                        active || busy || (locked && !(value === "" && !person.known))
                      }
                      aria-pressed={active}
                      className={`rounded px-2 py-1 text-[11px] font-medium transition-colors disabled:cursor-default ${
                        active
                          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                          : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-40 disabled:hover:bg-transparent"
                      }`}
                    >
                      {label}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        );
      })}

      {error && <p className="text-xs text-[var(--color-red)]">{error}</p>}
    </div>
  );
}
