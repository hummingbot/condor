import { AlertTriangle, Loader2 } from "lucide-react";

import type { AdminPerson, RoleTarget } from "@/lib/admin-api";
import { formatRelativeTime } from "@/lib/formatters";

import { formatDate } from "./identity";
import { ServerAccessGrid } from "./ServerAccessGrid";

/**
 * One person, expanded in place: who they are, what they may be, what they reach.
 *
 * Expanded in place rather than in a modal because the two decisions an admin
 * makes about a newcomer — approve them, and say what they should reach — are
 * one decision taken twice, and a modal makes the second one a separate trip.
 *
 * Nothing here predicts a transition. Every control sends a destination state
 * and the parent re-renders from whatever the server answered, so the refusal
 * table in `routes/admin.py` is what the panel ends up displaying — including
 * the refusals this UI does not anticipate.
 */

const ACCESS_STATES: { target: RoleTarget; label: string }[] = [
  { target: "pending", label: "Pending" },
  { target: "user", label: "Approved" },
  { target: "blocked", label: "Blocked" },
];

export function PersonDetail({
  person,
  onSetRole,
  onSetServerAccess,
  onSetCodeRun,
  rolePending,
  pendingServer,
  roleError,
  accessError,
  codeRunError,
}: {
  person: AdminPerson;
  onSetRole: (role: RoleTarget) => void;
  onSetServerAccess: (server: string, permission: string) => void;
  onSetCodeRun: (granted: boolean) => void;
  rolePending: boolean;
  pendingServer: string | null;
  roleError?: string;
  accessError?: string;
  codeRunError?: string;
}) {
  const currentState: RoleTarget | null = person.is_admin
    ? null
    : person.role === "user"
      ? "user"
      : person.role === "blocked"
        ? "blocked"
        : person.role === "pending"
          ? "pending"
          : null;

  return (
    <div className="space-y-4 border-t border-[var(--color-border)] bg-[var(--color-bg)]/40 px-4 py-4">
      <p className="text-xs text-[var(--color-text-muted)]">
        {person.created_at
          ? `Requested ${formatDate(person.created_at)}`
          : "No registration on record"}
        {person.approved_at ? ` · approved ${formatDate(person.approved_at)}` : ""}
        {person.known
          ? ` · last seen ${formatRelativeTime(person.last_seen || null, "never")}`
          : ""}
      </p>

      {!person.known && (
        <div className="flex items-start gap-2 rounded-md border border-[var(--color-amber,var(--color-border))] bg-[var(--color-surface-hover)] p-3">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-400" />
          <p className="text-xs leading-relaxed text-[var(--color-text-muted)]">
            This id holds access to a server but has no user record — the
            registration it was granted to is gone. The access is still live. The
            only thing to do with it is take it away.
          </p>
        </div>
      )}

      {/* Access */}
      {person.known && (
        <section>
          <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            Access
          </h4>
          {person.is_admin ? (
            <p className="text-xs text-[var(--color-text-muted)]">
              Admin — set by <code>ADMIN_USER_ID</code>, not from here.
            </p>
          ) : (
            <div className="flex flex-wrap items-center gap-1">
              {ACCESS_STATES.map(({ target, label }) => (
                <button
                  key={target}
                  type="button"
                  onClick={() => onSetRole(target)}
                  disabled={currentState === target || rolePending}
                  aria-pressed={currentState === target}
                  className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors disabled:cursor-default ${
                    currentState === target
                      ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-40"
                  }`}
                >
                  {label}
                </button>
              ))}
              {person.role === "pending" && (
                <button
                  type="button"
                  onClick={() => onSetRole("rejected")}
                  disabled={rolePending}
                  className="rounded-md px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-red)] disabled:opacity-40"
                >
                  Reject
                </button>
              )}
              {rolePending && (
                <Loader2 className="h-3 w-3 animate-spin text-[var(--color-text-muted)]" />
              )}
            </div>
          )}
          {roleError && (
            <p className="mt-1.5 text-xs text-[var(--color-red)]">{roleError}</p>
          )}
        </section>
      )}

      {/* Servers */}
      <section>
        <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
          Servers
        </h4>
        <ServerAccessGrid
          person={person}
          onSet={onSetServerAccess}
          pendingServer={pendingServer}
          error={accessError}
        />
      </section>

      {/* Capabilities */}
      {person.known && !person.is_admin && (
        <section>
          <h4 className="mb-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--color-text-muted)]">
            Capabilities
          </h4>
          <label className="flex cursor-pointer items-start gap-2">
            <input
              type="checkbox"
              checked={person.code_run}
              onChange={(e) => onSetCodeRun(e.target.checked)}
              className="mt-0.5 h-3.5 w-3.5 shrink-0 accent-[var(--color-red)]"
            />
            <span className="text-xs leading-relaxed text-[var(--color-text-muted)]">
              <code className="text-[var(--color-text)]">code_run</code> — arbitrary
              Python in the bot process: every server&apos;s credentials, the
              dashboard&apos;s JWT secret and the environment. Admin-equivalent in
              practice.
            </span>
          </label>
          {codeRunError && (
            <p className="mt-1.5 text-xs text-[var(--color-red)]">{codeRunError}</p>
          )}
        </section>
      )}
    </div>
  );
}
