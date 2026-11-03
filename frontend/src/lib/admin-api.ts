/**
 * The admin-only user administration surface (`/api/v1/admin/*`, ARCH-177).
 *
 * Kept out of `lib/api.ts` on purpose. Everything in that module is callable by
 * any logged-in seat; every call here answers 403 to everyone but an admin, and
 * a 403 here is not a failure — it is how the dashboard learns the current user
 * is not an admin and hides the panel (there is no `is_admin` claim on the
 * client today). That needs an error that carries its status code, which
 * `apiFetch` deliberately flattens into a message, so this module does its own
 * fetch over the shared `authHeaders`.
 *
 * Hiding the panel is cosmetic only. `routes/admin.py` re-reads the role from
 * the ConfigManager on every request and is the actual gate. Nothing the panel
 * refuses to show is a permission — the refusals that matter all come back from
 * the server as a 409 with a stated reason, and the panel renders that reason
 * rather than trying to anticipate it.
 */

import { authHeaders } from "./auth-token";

/** An error from an admin route, with the HTTP status preserved. */
export class AdminApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
  }
}

/** True when the failure was the admin gate refusing a non-admin. */
export function isForbidden(error: unknown): boolean {
  return error instanceof AdminApiError && error.status === 403;
}

/**
 * Fetch an admin route, preserving the status code on failure.
 *
 * Exported because `lib/updates-api.ts` needs the identical contract — admin
 * routes, and a 403 that must survive as a status rather than be flattened
 * into a message. A second copy would be a second place for the auth header
 * and the error shape to drift.
 */
export async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...authHeaders(),
      ...(init?.headers as Record<string, string>),
    },
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new AdminApiError(res.status, err.detail || `Request failed: ${res.status}`);
  }
  return res.json();
}

/** One person's access to one server — mirrors `ServerGrant` in routes/admin.py. */
export interface ServerGrant {
  server: string;
  /** "" (no access) | "trader" | "owner" */
  permission: string;
  /**
   * True when the access follows from the admin role rather than from a grant.
   * The server short-circuits every admin to `owner`, so rendering this as a
   * toggle would promise a revoke that cannot happen.
   */
  implicit: boolean;
}

/** A person as the admin panel needs them — mirrors `AdminPerson` in routes/admin.py. */
export interface AdminPerson {
  user_id: number;
  /** Resolved server-side by the same ladder `identity.ts` applies. */
  display_name: string;
  username: string;
  first_name: string;
  last_name: string;
  /** "admin" | "user" | "pending" | "blocked", or "" for an unknown id. */
  role: string;
  is_admin: boolean;
  /**
   * Whether the explicit `code_run` grant is set on this record. Admins are
   * reported separately via `is_admin`: they already pass the gate on the admin
   * arm, so they are never "granted".
   */
  code_run: boolean;
  created_at: number;
  approved_at: number;
  approved_by: number | null;
  last_seen: number;
  servers: ServerGrant[];
  /**
   * False for an id that holds a grant on some server but has no user record at
   * all. That access is live and, before this panel, was invisible and
   * unrevokable — so the row exists precisely to offer the revoke.
   */
  known: boolean;
}

/** One audit-log line, with both parties named — mirrors `AuditEntry`. */
export interface AuditEntry {
  timestamp: number;
  action: string;
  actor_id: number | null;
  actor_name: string;
  target_type: string;
  target_id: string;
  target_name: string;
  details: Record<string, unknown> | null;
}

/** Where a person's role can be sent. `rejected` deletes the registration. */
export type RoleTarget = "user" | "pending" | "blocked" | "rejected";

/** React Query key for the people list — shared so the tab probe and the panel dedupe. */
export const ADMIN_PEOPLE_KEY = ["admin-people"] as const;

/** React Query key for the audit log, which mutations elsewhere invalidate. */
export const ADMIN_AUDIT_KEY = ["admin-audit"] as const;

export const adminApi = {
  /** Everyone, with role and server access. 403 for non-admins — see `isForbidden`. */
  getPeople: () => adminFetch<AdminPerson[]>("/api/v1/admin/people"),

  /**
   * Move one person to a destination state. The server owns the table of legal
   * transitions and answers with the person as they now are, so the client
   * never renders a change it merely predicted.
   */
  setRole: (userId: number, role: RoleTarget) =>
    adminFetch<AdminPerson>(`/api/v1/admin/people/${userId}/role`, {
      method: "POST",
      body: JSON.stringify({ role }),
    }),

  /** Grant (`"trader"`) or revoke (`""`) one person's access to one server. */
  setServerAccess: (userId: number, server: string, permission: string) =>
    adminFetch<AdminPerson>(
      `/api/v1/admin/people/${userId}/servers/${encodeURIComponent(server)}`,
      { method: "PUT", body: JSON.stringify({ permission }) },
    ),

  /** Grant or revoke `code_run` for one user. Audited server-side. */
  setCodeRunGrant: (userId: number, granted: boolean) =>
    adminFetch<AdminPerson>(`/api/v1/admin/people/${userId}/code-run`, {
      method: "PUT",
      body: JSON.stringify({ granted }),
    }),

  /** Ask Telegram what the records with no stored name are called. */
  refreshNames: () =>
    adminFetch<{ checked: number; resolved: number; failed: number }>(
      "/api/v1/admin/people/refresh-names",
      { method: "POST" },
    ),

  /** The audit log these mutations have been writing all along. */
  getAudit: (limit = 50) =>
    adminFetch<AuditEntry[]>(`/api/v1/admin/audit?limit=${limit}`),
};
