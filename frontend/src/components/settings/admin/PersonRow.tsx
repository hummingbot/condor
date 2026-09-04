import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, ChevronDown, ChevronRight, Copy } from "lucide-react";
import { useState } from "react";

import { ConfirmDialog } from "@/components/agent/ConfirmDialog";
import {
  ADMIN_AUDIT_KEY,
  ADMIN_PEOPLE_KEY,
  type AdminPerson,
  type RoleTarget,
  adminApi,
} from "@/lib/admin-api";
import { copyText } from "@/lib/clipboard";

import { displayName, handle, initials, roleLabel } from "./identity";
import { PersonDetail } from "./PersonDetail";

/**
 * One person in the list, expanding in place into their detail panel.
 *
 * The mutations live here rather than in the list so that a pending grant on
 * one person does not grey out the controls on another. Every one of them
 * invalidates the people list and the audit log and re-renders from the
 * server's answer — the client never writes the row it hoped for. That is what
 * makes the server's refusal table the thing the user actually sees.
 *
 * Blocking is the one action behind a confirm: it is the only move that takes
 * away someone's access to everything at once and cannot be undone in a single
 * step (unblocking returns them to *pending*, not to approved). `code_run`
 * keeps its own confirm for the reason it always had. Revoking a server applies
 * directly — taking access away is the safe direction.
 */
export function PersonRow({
  person,
  expanded,
  onToggle,
}: {
  person: AdminPerson;
  expanded: boolean;
  onToggle: () => void;
}) {
  const qc = useQueryClient();
  const [copied, setCopied] = useState(false);
  const [confirmBlock, setConfirmBlock] = useState(false);
  const [confirmCodeRun, setConfirmCodeRun] = useState(false);
  const [pendingServer, setPendingServer] = useState<string | null>(null);

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ADMIN_PEOPLE_KEY });
    qc.invalidateQueries({ queryKey: ADMIN_AUDIT_KEY });
  };

  const roleMut = useMutation({
    mutationFn: (role: RoleTarget) => adminApi.setRole(person.user_id, role),
    onSuccess: () => {
      refresh();
      setConfirmBlock(false);
    },
  });

  const accessMut = useMutation({
    mutationFn: ({ server, permission }: { server: string; permission: string }) =>
      adminApi.setServerAccess(person.user_id, server, permission),
    onSettled: () => {
      setPendingServer(null);
      refresh();
    },
  });

  const codeRunMut = useMutation({
    mutationFn: (granted: boolean) => adminApi.setCodeRunGrant(person.user_id, granted),
    onSuccess: () => {
      refresh();
      setConfirmCodeRun(false);
    },
  });

  const setRole = (role: RoleTarget) => {
    if (role === "blocked") {
      setConfirmBlock(true);
      return;
    }
    roleMut.mutate(role);
  };

  const setCodeRun = (granted: boolean) => {
    if (granted) {
      setConfirmCodeRun(true);
      return;
    }
    codeRunMut.mutate(false);
  };

  const copyId = async () => {
    if (await copyText(String(person.user_id))) {
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    }
  };

  const name = displayName(person);
  const reach = person.is_admin
    ? "all servers"
    : person.servers
        .filter((g) => g.permission)
        .map((g) => g.server)
        .join(", ");

  return (
    <div className="overflow-hidden rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)]">
      <div className="flex items-center gap-3 px-4 py-3">
        <span
          className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
            person.known
              ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
              : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
          }`}
          aria-hidden
        >
          {initials(person)}
        </span>

        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          className="min-w-0 flex-1 text-left"
        >
          <span className="flex items-center gap-2">
            <span className="truncate text-sm font-medium text-[var(--color-text)]">
              {name}
            </span>
            <span className="shrink-0 rounded bg-[var(--color-surface-hover)] px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              {roleLabel(person)}
            </span>
            {!person.known && (
              <AlertTriangle
                className="h-3.5 w-3.5 shrink-0 text-amber-400"
                aria-label="Holds access with no user record"
              />
            )}
          </span>
          <span className="mt-0.5 block truncate text-xs text-[var(--color-text-muted)]">
            {[handle(person), reach].filter(Boolean).join(" · ") || "no access"}
          </span>
        </button>

        {/* The id is always present and always copyable: it is the one thing
            that identifies this person across Telegram, config.yml and here. */}
        <button
          type="button"
          onClick={copyId}
          title="Copy user id"
          aria-label={`Copy user id ${person.user_id}`}
          className="flex shrink-0 items-center gap-1 rounded px-1.5 py-1 font-mono text-[11px] text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          {person.user_id}
          {copied ? (
            <Check className="h-3 w-3 text-[var(--color-green,currentColor)]" />
          ) : (
            <Copy className="h-3 w-3" />
          )}
        </button>

        {person.role === "pending" && (
          <button
            type="button"
            onClick={() => roleMut.mutate("user")}
            disabled={roleMut.isPending}
            className="shrink-0 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)] disabled:opacity-50"
          >
            Approve
          </button>
        )}

        <button
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          aria-label={expanded ? `Collapse ${name}` : `Expand ${name}`}
          className="shrink-0 rounded p-1 text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
        >
          {expanded ? (
            <ChevronDown className="h-4 w-4" />
          ) : (
            <ChevronRight className="h-4 w-4" />
          )}
        </button>
      </div>

      {expanded && (
        <PersonDetail
          person={person}
          onSetRole={setRole}
          onSetServerAccess={(server, permission) => {
            setPendingServer(server);
            accessMut.mutate({ server, permission });
          }}
          onSetCodeRun={setCodeRun}
          rolePending={roleMut.isPending}
          pendingServer={pendingServer}
          roleError={(roleMut.error as Error | null)?.message}
          accessError={(accessMut.error as Error | null)?.message}
          codeRunError={(codeRunMut.error as Error | null)?.message}
        />
      )}

      <ConfirmDialog
        open={confirmBlock}
        title={`Block ${name}?`}
        confirmLabel="Block"
        pendingLabel="Blocking..."
        isPending={roleMut.isPending}
        isError={roleMut.isError}
        errorText={(roleMut.error as Error | null)?.message}
        onConfirm={() => roleMut.mutate("blocked")}
        onClose={() => setConfirmBlock(false)}
      >
        They lose access to the bot and the dashboard immediately. Their server
        grants are kept, but unblocking returns them to <em>pending</em> — you will
        have to approve them again.
      </ConfirmDialog>

      <ConfirmDialog
        open={confirmCodeRun}
        title={`Grant code_run to ${name}?`}
        confirmLabel="Grant"
        pendingLabel="Granting..."
        isPending={codeRunMut.isPending}
        isError={codeRunMut.isError}
        errorText={(codeRunMut.error as Error | null)?.message}
        onConfirm={() => codeRunMut.mutate(true)}
        onClose={() => setConfirmCodeRun(false)}
      >
        This lets them execute arbitrary Python in the bot process: every configured
        server&apos;s credentials, the dashboard&apos;s JWT secret and the environment
        are readable, and the process is writable. It is admin-equivalent in practice.
        Only grant it to someone you would make an admin.
      </ConfirmDialog>
    </div>
  );
}
