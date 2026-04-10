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
import { CreateGridExecutor } from "@/pages/CreateGridExecutor";
import { Executors } from "@/pages/Executors";
import { Login } from "@/pages/Login";
import { Positions } from "@/pages/Positions";
import { MarketData } from "@/pages/MarketData";
import { Portfolio } from "@/pages/Portfolio";

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
                <Route path="/executors" element={<Executors />} />
                <Route path="/executors/new-grid" element={<CreateGridExecutor />} />
                <Route path="/positions" element={<Positions />} />
                <Route path="/agents" element={<Agents />} />
                <Route path="/agents/:slug" element={<AgentDetail />} />
                <Route path="/market" element={<MarketData />} />
              </Route>
            </Routes>
          </BrowserRouter>
        </ServerContext>
      </AuthContext>
    </QueryClientProvider>
  );
}
