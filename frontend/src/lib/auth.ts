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

/** Selected Hummingbot API server. Session state: cleared on logout. */
export const SERVER_KEY = "condor_selected_server";

/**
 * Forget the outgoing user's recorded console errors when a login replaces a
 * live session.
 *
 * `logout` is the usual way a session ends, but it is not the only one:
 * `/login` stays reachable while authenticated, and a fresh `/web` link from
 * Telegram redeems its token straight into whatever tab is already open. That
 * is a session boundary with no `logout` in between, and the error ring is
 * module state that would otherwise cross it.
 *
 * Re-logging in as the same user is not a boundary — Telegram mints a new token
 * every time — and keeps its errors, which is often exactly the session someone
 * is about to file a bug report about. Unreadable stored user: clear, since the
 * point of the check is to prove the two sessions belong to one person.
 */
function dropPreviousSessionErrors(incoming: User) {
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return;
  try {
    if ((JSON.parse(raw) as User).id !== incoming.id) clearDiagnostics();
  } catch {
    clearDiagnostics();
  }
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
    dropPreviousSessionErrors(data.user);
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
    dropPreviousSessionErrors(data.user);
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
