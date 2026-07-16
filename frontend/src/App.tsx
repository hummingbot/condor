import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/components/layout/AppShell";
import { AgentDetail } from "@/pages/AgentDetail";
import { Agents } from "@/pages/Agents";
import { Positions } from "@/pages/Positions";
import { Routines } from "@/pages/Routines";
import { Venues } from "@/pages/Venues";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 5000,
    },
  },
});

// No auth: the API is loopback-only (§5.5) — the app loads straight into
// the dashboard. Unsafe requests carry the CSRF token (lib/csrf.ts).
export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<Positions />} />
            <Route path="/agents" element={<Agents />} />
            <Route path="/agents/:slug" element={<AgentDetail />} />
            <Route path="/routines" element={<Routines />} />
            <Route path="/reports" element={<Navigate to="/routines" replace />} />
            <Route path="/venues" element={<Venues />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </QueryClientProvider>
  );
}
