// Single source of truth for the JWT auth surface: storage key, header builder
// and a low-level authenticated fetch. Anything that talks to the API should go
// through `apiFetch` (lib/api.ts) for JSON; use `authFetch` for FormData uploads
// or binary/blob responses where forcing `Content-Type: application/json` is wrong.

export const TOKEN_KEY = "condor_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}

/** Authorization header for the current JWT, or `{}` if not logged in. */
export function authHeaders(): Record<string, string> {
  const token = getToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Low-level fetch that injects the auth header without forcing a Content-Type.
 * Use for FormData uploads (transcribe) or blob responses (authenticated images).
 */
export function authFetch(path: string, init?: RequestInit): Promise<Response> {
  return fetch(path, {
    ...init,
    headers: { ...authHeaders(), ...(init?.headers as Record<string, string>) },
  });
}
