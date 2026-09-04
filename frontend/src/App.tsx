import { QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { ServerContext } from "@/hooks/useServer";
import { AuthContext, SERVER_KEY, useAuth, useAuthState } from "@/lib/auth";
import { invalidateServerScopedQueries, queryClient } from "@/lib/queryClient";
import {
  AgentRunsRedirect,
  AgentStrategyRedirect,
  AgentWorkspace,
} from "@/pages/AgentWorkspace";
import { Agents } from "@/pages/Agents";
import { BotDetail } from "@/pages/BotDetail";
import { Bots } from "@/pages/Bots";
import { CreateExecutor } from "@/pages/CreateExecutor";
import { Dex } from "@/pages/Dex";
import { Floor } from "@/pages/Floor";
import { DexPool } from "@/pages/DexPool";
import { Login } from "@/pages/Login";
import { Portfolio } from "@/pages/Portfolio";
import { Routines } from "@/pages/Routines";
import { Settings } from "@/pages/Settings";

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

/**
 * Holds the selected server for the current session.
 *
 * Mounted with a key derived from the logged-in user, so a session change tears
 * this state down and re-reads `localStorage` — which `logout` has just cleared.
 * Without the remount the selection would outlive the session (logging out and
 * back in never reloads the page) and the next user would inherit it.
 */
function ServerProvider({ children }: { children: React.ReactNode }) {
  const [server, setServer] = useState<string | null>(
    () => localStorage.getItem(SERVER_KEY),
  );
  const handleSetServer = useCallback(
    (next: string) => {
      localStorage.setItem(SERVER_KEY, next);
      setServer(next);
      // Only the two servers involved are touched, and nothing is refetched
      // here: `AppShell` renders `<Outlet key={server}>`, so the switch already
      // remounts every page against the new key. See the helper for why the
      // blanket invalidation it replaces hit the server being left.
      if (next !== server) {
        invalidateServerScopedQueries(queryClient, [server, next]);
      }
    },
    [server],
  );

  return (
    <ServerContext value={{ server, setServer: handleSetServer }}>
      {children}
    </ServerContext>
  );
}

export default function App() {
  const auth = useAuthState();

  return (
    <QueryClientProvider client={queryClient}>
      <AuthContext value={auth}>
        <ServerProvider key={auth.user?.id ?? "anon"}>
          <BrowserRouter>
            <Routes>
              <Route path="/login" element={<Login />} />
              <Route
                element={
                  <ProtectedRoute>
                    <AppShell />
                  </ProtectedRoute>
                }
              >
                <Route path="/" element={<Agents />} />
                {/* What every agent adds up to (FEAT-112). A page rather than a
                    third `?view=` on the home: `/`'s parameter already means
                    *chat or fleet*, and an aggregate chart is not a row. */}
                <Route path="/floor" element={<Floor />} />
                <Route path="/portfolio" element={<Portfolio />} />
                <Route path="/bots" element={<Bots />} />
                <Route path="/bots/:id" element={<BotDetail />} />
                <Route path="/trade" element={<CreateExecutor />} />
            <Route path="/dex" element={<Dex />} />
            <Route path="/dex/:network/:address" element={<DexPool />} />
                {/* Executors are a scope of the browser now, not a page
                    (FEAT-086). The listing this replaces was the whole
                    history, live and archived together, which is what the
                    Terminated population grouped by type is. */}
                <Route
                  path="/executors"
                  element={<Navigate to="/bots?population=terminated&group=type" replace />}
                />
                <Route path="/executors/new" element={<Navigate to="/trade" replace />} />
                <Route path="/executors/new-grid" element={<Navigate to="/trade?type=grid" replace />} />
                <Route
                  path="/archived"
                  element={<Navigate to="/bots?population=terminated&group=bot" replace />}
                />
                <Route path="/routines" element={<Routines />} />
                <Route path="/reports" element={<Navigate to="/routines?tab=reports" replace />} />
                {/* `/agents` has pointed at the home since the fleet grid
                    was deleted, and since FEAT-104 step 3 the home is a list of
                    agents again — the first time this redirect has meant what
                    it says. Still a redirect and not a second route: one
                    overview, at the address people already have. */}
                <Route path="/agents" element={<Navigate to="/" replace />} />
                {/* One agent, one screen: every section, run and tick is a query
                    parameter on this route (FEAT-103). */}
                <Route path="/agents/:slug" element={<AgentWorkspace />} />
                {/* The Lab and the strategy page are views of the workspace
                    now (FEAT-103). Redirects rather than deletions: both are in
                    notification payloads and in bookmarks, and both carry a
                    query string that has to arrive intact. */}
                <Route path="/agents/:slug/runs" element={<AgentRunsRedirect />} />
                <Route
                  path="/agents/:slug/strategies/:sslug"
                  element={<AgentStrategyRedirect />}
                />
                <Route path="/settings" element={<Settings />} />
                <Route path="/market" element={<Navigate to="/trade" replace />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </ServerProvider>
      </AuthContext>
    </QueryClientProvider>
  );
}
