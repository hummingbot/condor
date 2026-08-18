/**
 * The admin-only user administration surface (`/api/v1/admin/*`, ARCH-177).
 *
 * Kept out of `lib/api.ts` on purpose. Everything in that module is callable by
 * any logged-in seat; these two calls answer 403 to everyone but an admin, and
 * a 403 here is not a failure — it is how the dashboard learns the current user
 * is not an admin and hides the panel (there is no `is_admin` claim on the
 * client today). That needs an error that carries its status code, which
 * `apiFetch` deliberately flattens into a message, so this module does its own
 * fetch over the shared `authHeaders`.
 *
 * Hiding the panel is cosmetic only. `routes/admin.py` re-reads the role from
 * the ConfigManager on every request and is the actual gate.
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

async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
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

/** A user row as the admin panel needs it — mirrors `AdminUser` in routes/admin.py. */
export interface AdminUser {
  user_id: number;
  username: string;
  role: string;
  /**
   * Whether the explicit `code_run` grant is set on this record. Admins are
   * reported separately via `is_admin`: they already pass the gate on the admin
   * arm, so they are never "granted".
   */
  code_run: boolean;
  is_admin: boolean;
}

/** React Query key for the user list — shared so the tab probe and the panel dedupe. */
export const ADMIN_USERS_KEY = ["admin-users"] as const;

export const adminApi = {
  /** Every user record with its grant. 403 for non-admins — see `isForbidden`. */
  getUsers: () => adminFetch<AdminUser[]>("/api/v1/admin/users"),

  /** Grant or revoke `code_run` for one user. Audited server-side. */
  setCodeRunGrant: (userId: number, granted: boolean) =>
    adminFetch<{ user_id: number; code_run: boolean }>(
      `/api/v1/admin/users/${userId}/code-run`,
      { method: "PUT", body: JSON.stringify({ granted }) },
    ),
};
