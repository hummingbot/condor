import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, ShieldAlert, ShieldCheck } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import { ADMIN_USERS_KEY, type AdminUser, adminApi } from "@/lib/admin-api";

/**
 * Admin-only user administration (ARCH-177).
 *
 * Carries exactly one capability today: the `code_run` grant SEC-151 added. The
 * gate in `routes/code.py` is *admin or an explicit per-user grant*, and before
 * this panel the only way to set that grant was hand-editing config.yml — so
 * the escape hatch for a non-admin seat was effectively unreachable.
 *
 * Granting is behind a confirm that names what it confers, because it is not a
 * scoped permission: arbitrary Python inside the bot process is admin-equivalent
 * in practice. Revoking takes power away, so it applies directly.
 */
export function AdminSettings() {
  const qc = useQueryClient();
  const [confirmGrant, setConfirmGrant] = useState<AdminUser | null>(null);

  const { data: users = [], isLoading } = useQuery({
    queryKey: ADMIN_USERS_KEY,
    queryFn: adminApi.getUsers,
    retry: false,
  });

  const grantMut = useMutation({
    mutationFn: ({ userId, granted }: { userId: number; granted: boolean }) =>
      adminApi.setCodeRunGrant(userId, granted),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ADMIN_USERS_KEY });
      setConfirmGrant(null);
    },
  });

  const label = (u: AdminUser) => u.username || `user ${u.user_id}`;

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-12 text-[var(--color-text-muted)]">
        <Loader2 className="h-5 w-5 animate-spin" />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4">
        <h3 className="flex items-center gap-1.5 text-sm font-semibold text-[var(--color-text)]">
          <ShieldAlert className="h-3.5 w-3.5 text-[var(--color-red)]" />
          Code execution
        </h3>
        <p className="mt-1.5 text-xs leading-relaxed text-[var(--color-text-muted)]">
          The <code>code_run</code> grant lets a non-admin run arbitrary Python inside
          the bot process — every server&apos;s credentials, the dashboard&apos;s JWT
          secret and the environment. Admins already have it and cannot be granted it
          separately. Grants and revocations are recorded in the audit log and take
          effect immediately, with no restart.
        </p>
      </div>

      {users.length === 0 && (
        <p className="py-8 text-center text-sm text-[var(--color-text-muted)]">
          No users registered.
        </p>
      )}

      <div className="space-y-2">
        {users.map((u) => (
          <div
            key={u.user_id}
            className="flex items-center justify-between gap-3 rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] px-4 py-3"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-[var(--color-text)]">
                {label(u)}
              </p>
              <p className="text-xs text-[var(--color-text-muted)]">
                {u.user_id}
                {u.role ? ` · ${u.role}` : ""}
              </p>
            </div>

            {u.is_admin ? (
              <span className="flex shrink-0 items-center gap-1.5 text-xs text-[var(--color-text-muted)]">
                <ShieldCheck className="h-3.5 w-3.5" />
                Admin — always
              </span>
            ) : u.code_run ? (
              <div className="flex shrink-0 items-center gap-2">
                <span className="rounded-md bg-[var(--color-red)]/15 px-2 py-0.5 text-xs font-medium text-[var(--color-red)]">
                  code_run
                </span>
                <button
                  onClick={() => grantMut.mutate({ userId: u.user_id, granted: false })}
                  disabled={grantMut.isPending}
                  className="rounded-md px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-50"
                >
                  Revoke
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmGrant(u)}
                className="shrink-0 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              >
                Grant code_run
              </button>
            )}
          </div>
        ))}
      </div>

      {grantMut.isError && (
        <p className="text-xs text-[var(--color-red)]">
          {(grantMut.error as Error).message}
        </p>
      )}

      <ConfirmDialog
        open={confirmGrant !== null}
        title={`Grant code_run to ${confirmGrant ? label(confirmGrant) : ""}?`}
        confirmLabel="Grant"
        pendingLabel="Granting..."
        isPending={grantMut.isPending}
        isError={grantMut.isError}
        errorText={(grantMut.error as Error | null)?.message}
        onConfirm={() =>
          confirmGrant && grantMut.mutate({ userId: confirmGrant.user_id, granted: true })
        }
        onClose={() => setConfirmGrant(null)}
      >
        This lets them execute arbitrary Python in the bot process: every configured
        server&apos;s credentials, the dashboard&apos;s JWT secret and the environment
        are readable, and the process is writable. It is admin-equivalent in practice.
        Only grant it to someone you would make an admin.
      </ConfirmDialog>
    </div>
  );
}
