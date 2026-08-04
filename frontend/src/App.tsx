import { QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { ServerContext } from "@/hooks/useServer";
import { AuthContext, SERVER_KEY, useAuth, useAuthState } from "@/lib/auth";
import { queryClient } from "@/lib/queryClient";
import { AgentDetail } from "@/pages/AgentDetail";
import { Agents } from "@/pages/Agents";
import { BotDetail } from "@/pages/BotDetail";
import { Bots } from "@/pages/Bots";
import { CreateExecutor } from "@/pages/CreateExecutor";
import { Executors } from "@/pages/Executors";
import { Login } from "@/pages/Login";
import { Portfolio } from "@/pages/Portfolio";
import { Routines } from "@/pages/Routines";
import { Settings } from "@/pages/Settings";
import { StrategyDetail } from "@/pages/StrategyDetail";

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
  const handleSetServer = useCallback((s: string) => {
    localStorage.setItem(SERVER_KEY, s);
    setServer(s);
    queryClient.invalidateQueries();
  }, []);

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
                <Route path="/" element={<Portfolio />} />
                <Route path="/bots" element={<Bots />} />
                <Route path="/bots/:id" element={<BotDetail />} />
                <Route path="/trade" element={<CreateExecutor />} />
                <Route path="/executors" element={<Executors />} />
                <Route path="/executors/new" element={<Navigate to="/trade" replace />} />
                <Route path="/executors/new-grid" element={<Navigate to="/trade?type=grid" replace />} />
                <Route path="/backtest" element={<Navigate to="/bots?tab=backtest" replace />} />
                <Route path="/archived" element={<Navigate to="/bots?tab=archived" replace />} />
                <Route path="/routines" element={<Routines />} />
                <Route path="/reports" element={<Navigate to="/routines?tab=reports" replace />} />
                <Route path="/agents" element={<Agents />} />
                <Route path="/agents/:slug" element={<AgentDetail />} />
                <Route path="/agents/:slug/strategies/:sslug" element={<StrategyDetail />} />
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
