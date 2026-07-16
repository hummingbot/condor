// CSRF wiring (simplification plan §5.5).
//
// The backend binds to loopback only and gates unsafe methods
// (POST/PUT/PATCH/DELETE) behind a per-process token served at
// GET /api/v1/security/token. We fetch it once (same-origin), cache it,
// and attach it as `X-Condor-Token` to every unsafe request (lib/api.ts).
// WS connections don't need it — Origin/Host are enforced server-side.

let tokenPromise: Promise<string> | null = null;

/** Fetch (once) and cache the per-process CSRF token. */
export function getCsrfToken(): Promise<string> {
  if (!tokenPromise) {
    tokenPromise = fetch("/api/v1/security/token")
      .then(async (res) => {
        if (!res.ok) {
          throw new Error(`Failed to fetch CSRF token: ${res.status}`);
        }
        const data = (await res.json()) as { token: string };
        if (!data.token) throw new Error("CSRF token response missing 'token'");
        return data.token;
      })
      .catch((err) => {
        // Reset so the next unsafe request retries instead of caching failure.
        tokenPromise = null;
        throw err;
      });
  }
  return tokenPromise;
}
