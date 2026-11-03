import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";

import { TOKEN_KEY, authHeaders } from "./auth-token";
import { clearDiagnostics } from "./diagnostics";
import { queryClient } from "./queryClient";
import { clearSessionState } from "./sessionState";

export interface User {
  id: number;
  username: string;
  first_name: string;
  role: string;
}

export interface AuthState {
  user: User | null;
  token: string | null;
  isAuthenticated: boolean;
  loginWithToken: (loginToken: string) => Promise<void>;
  /** Local mode (FEAT-049): claim the local admin's session. 404s elsewhere. */
  loginLocal: () => Promise<void>;
  logout: () => void;
}

const USER_KEY = "condor_user";

/** Selected Hummingbot API server. Session state: cleared on every session boundary. */
export const SERVER_KEY = "condor_selected_server";

/**
 * Drop the outgoing user's session state when a login replaces a live session.
 *
 * `logout` is the usual way a session ends, but it is not the only one:
 * `/login` stays reachable while authenticated, and a fresh `/web` link from
 * Telegram redeems its token straight into whatever tab is already open. That
 * is a session boundary with no `logout` in between, and everything `logout`
 * drops would otherwise cross it: the React Query cache (every cached response
 * belongs to the session that fetched it — portfolio, bots, API keys,
 * conversations), the console-error ring, the selected server, which the next
 * user may not even have access to, and the stored form state — order sizes,
 * leverage, routine configs — that would otherwise pre-fill the incoming user's
 * panels with the outgoing user's trading (see lib/sessionState.ts). One
 * function and one predicate for the four of them, so they cannot drift apart.
 *
 * Re-logging in as the same user is not a boundary — Telegram mints a new token
 * every time — and keeps its state: the errors are often exactly the session
 * someone is about to file a bug report about, and re-fetching an unchanged
 * cache would cost them a full reload for no security gain. Unreadable stored
 * user: clear, since the point of the check is to prove the two sessions belong
 * to one person.
 *
 * Runs before `setUser`, so `ServerProvider` — keyed by the user id in
 * `App.tsx` — remounts into the cleared `localStorage` rather than ahead of it.
 */
function resetForNewUser(incoming: User) {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return;
  try {
    if ((JSON.parse(raw) as User).id === incoming.id) return;
  } catch {
    // Cannot prove the two sessions are one person: treat it as a boundary.
  }
  localStorage.removeItem(SERVER_KEY);
  queryClient.clear();
  clearDiagnostics();
  clearSessionState();
}

export const AuthContext = createContext<AuthState>({
  user: null,
  token: null,
  isAuthenticated: false,
  loginWithToken: async () => {},
  loginLocal: async () => {},
  logout: () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

export function useAuthState(): AuthState {
  const [token, setToken] = useState<string | null>(
    () => localStorage.getItem(TOKEN_KEY),
  );
  const [user, setUser] = useState<User | null>(() => {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  });

  const loginWithToken = useCallback(async (loginToken: string) => {
    const res = await fetch("/api/v1/auth/token-login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: loginToken }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Login failed");
    }
    const data = await res.json();
    resetForNewUser(data.user);
    localStorage.setItem(TOKEN_KEY, data.token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
    setToken(data.token);
    setUser(data.user);
  }, []);

  // Local mode: the dashboard runs on this machine with no login at all, so
  // the session is claimed rather than presented. Stores exactly what
  // loginWithToken stores — downstream there is no such thing as a "local"
  // session, only a session.
  const loginLocal = useCallback(async () => {
    const res = await fetch("/api/v1/auth/local-login", { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "Local login failed");
    }
    const data = await res.json();
    resetForNewUser(data.user);
    localStorage.setItem(TOKEN_KEY, data.token);
    localStorage.setItem(USER_KEY, JSON.stringify(data.user));
    setToken(data.token);
    setUser(data.user);
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem(SERVER_KEY);
    // Every cached response belongs to the session that fetched it. Logging out
    // is a pure client-side transition (no page reload), so without this the
    // next user to log in renders the previous user's portfolio, bots, API keys
    // and conversations straight from the cache.
    queryClient.clear();
    // The console-error ring is session state for the same reason the cache is,
    // and it leaves the machine: it is quoted verbatim into the "Report an
    // issue" block and into the crash report ErrorBoundary files. Reached both
    // from the Settings button and from the token check below, so an expired
    // session drops its errors too.
    clearDiagnostics();
    // Order sizes, leverage, saved routine configs and starred pairs: what the
    // outgoing user typed, not how this device renders. Device preferences —
    // theme, display currency, panel layout — deliberately survive.
    clearSessionState();
    setToken(null);
    setUser(null);
  }, []);

  // Validate token on mount
  useEffect(() => {
    if (!token) return;
    fetch("/api/v1/auth/me", {
      headers: authHeaders(),
    }).then((res) => {
      if (!res.ok) {
        logout();
      }
    }).catch(() => {
      // server not available, keep token
    });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return {
    user,
    token,
    isAuthenticated: !!token && !!user,
    loginWithToken,
    loginLocal,
    logout,
  };
}
