import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useCallback, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { ServerContext } from "@/hooks/useServer";
import { AuthContext, useAuth, useAuthState } from "@/lib/auth";
import { AgentDetail } from "@/pages/AgentDetail";
import { Agents } from "@/pages/Agents";
import { BotDetail } from "@/pages/BotDetail";
import { Bots } from "@/pages/Bots";
import { CreateExecutor } from "@/pages/CreateExecutor";
import { Executors } from "@/pages/Executors";
import { Login } from "@/pages/Login";
import { Portfolio } from "@/pages/Portfolio";
import { Reports } from "@/pages/Reports";
import { Routines } from "@/pages/Routines";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5000,
    },
  },
});

function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  const auth = useAuthState();
  const [server, setServer] = useState<string | null>(
    () => localStorage.getItem("condor_selected_server"),
  );
  const handleSetServer = useCallback((s: string) => {
    localStorage.setItem("condor_selected_server", s);
    setServer(s);
    queryClient.invalidateQueries();
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      <AuthContext value={auth}>
        <ServerContext value={{ server, setServer: handleSetServer }}>
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
                <Route path="/reports" element={<Reports />} />
                <Route path="/agents" element={<Agents />} />
                <Route path="/agents/:slug" element={<AgentDetail />} />
                <Route path="/market" element={<Navigate to="/trade" replace />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </ServerContext>
      </AuthContext>
    </QueryClientProvider>
  );
}
