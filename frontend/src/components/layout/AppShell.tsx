import { useState } from "react";
import {
  Activity,
  Bot,
  Brain,
  MessageSquare,
  Moon,
  Settings,
  Sun,
  Swords,
  Wallet,
  Zap,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { ConnectKeysOverlay } from "@/components/ConnectKeysOverlay";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { useCredentials } from "@/hooks/useCredentials";
import { usePrefetchData } from "@/hooks/usePrefetchData";
import { useServer } from "@/hooks/useServer";
import { useTheme } from "@/hooks/useTheme";
import { ServerSelector } from "./ServerSelector";

const NAV_ITEMS = [
  { to: "/", icon: Wallet, label: "Portfolio" },
  { to: "/trade", icon: Swords, label: "Trade" },
  { to: "/bots", icon: Bot, label: "Bots" },
  { to: "/executors", icon: Activity, label: "Executors" },
  { to: "/agents", icon: Brain, label: "Agents" },
  { to: "/routines", icon: Zap, label: "Routines" },
] as const;

export function AppShell() {
  const { server } = useServer();
  const { pathname } = useLocation();
  const { theme, toggleTheme } = useTheme();
  const [chatOpen, setChatOpen] = useState(false);
  const { hasKeys, isLoading: keysLoading } = useCredentials();

  const exemptRoutes = ["/routines", "/settings"];
  const showKeysOverlay = server && !keysLoading && !hasKeys && !exemptRoutes.some((r) => pathname.startsWith(r));

  // Prefetch core data (executors, bots) and subscribe to WS channels early
  usePrefetchData();

  return (
    <div className="flex h-screen flex-col">
      {/* Top bar */}
      <header className="flex h-12 shrink-0 items-center border-b border-[var(--color-border)] bg-[var(--color-surface)] px-4">
        {/* Left: logo + nav */}
        <div className="flex items-center gap-6">
          <NavLink to="/" className="flex items-center gap-2 font-bold tracking-tight">
            <img src="/condor_old.jpeg" alt="Condor" className="h-6 w-6 rounded-full" />
            <span className="text-sm">Condor</span>
          </NavLink>

          <nav className="flex items-center">
            {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) =>
                  `flex items-center gap-1.5 px-3 py-1.5 text-sm rounded-md transition-colors ${
                    isActive
                      ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                      : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  }`
                }
              >
                <Icon className="h-3.5 w-3.5 shrink-0" />
                {label}
              </NavLink>
            ))}
          </nav>
        </div>

        {/* Right: server selector + controls */}
        <div className="ml-auto flex items-center gap-3">
          <ServerSelector />

          <div className="flex items-center gap-1">
            <NavLink
              to="/settings"
              className={({ isActive }) =>
                `rounded p-1.5 transition-colors ${
                  isActive
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-accent)]"
                }`
              }
              title="Settings"
            >
              <Settings className="h-4 w-4" />
            </NavLink>

            <button
              onClick={toggleTheme}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-accent)]"
              title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>

          </div>

          <button
            onClick={() => setChatOpen((v) => !v)}
            className={`ml-2 flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium transition-all ${
              chatOpen
                ? "bg-amber-500 text-black shadow-sm shadow-amber-500/25"
                : "bg-amber-500/15 text-amber-500 hover:bg-amber-500/25 border border-amber-500/30"
            }`}
            title="Agent (⌘K)"
          >
            <MessageSquare className="h-3.5 w-3.5" />
            <span>Agent</span>
          </button>
        </div>
      </header>

      {/* Main content */}
      <main className="relative flex-1 overflow-auto p-6">
        <ErrorBoundary resetKey={pathname + server}>
          <Outlet />
        </ErrorBoundary>
        {showKeysOverlay && <ConnectKeysOverlay />}
      </main>

      {/* Chat panel */}
      <ChatPanel isOpen={chatOpen} onToggle={setChatOpen} />
    </div>
  );
}
