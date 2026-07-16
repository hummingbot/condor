import { useState } from "react";
import {
  Brain,
  Eye,
  Landmark,
  Moon,
  Sun,
  Wallet,
  Zap,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ChatPanel } from "@/components/chat/ChatPanel";
import { useTheme } from "@/hooks/useTheme";
import { AgentToggleButton } from "./AgentToggleButton";

const NAV_ITEMS = [
  { to: "/", icon: Wallet, label: "Positions" },
  { to: "/agents", icon: Brain, label: "Agents" },
  { to: "/routines", icon: Zap, label: "Routines" },
  { to: "/venues", icon: Landmark, label: "Venues" },
] as const;

export function AppShell() {
  const { pathname } = useLocation();
  const { theme, toggleTheme } = useTheme();
  const [chatOpen, setChatOpen] = useState(false);

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

        {/* Right: theme + chat controls */}
        <div className="ml-auto flex items-center gap-3">
          <button
            onClick={toggleTheme}
            className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-accent)]"
            title={
              theme === "dark" ? "Switch to light mode" :
              theme === "light" ? "Switch to color-blind mode" :
              "Switch to dark mode"
            }
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> :
             theme === "light" ? <Eye className="h-4 w-4" /> :
             <Moon className="h-4 w-4" />}
          </button>

          <AgentToggleButton active={chatOpen} onClick={() => setChatOpen((v) => !v)} className="ml-2" />
        </div>
      </header>

      {/* Main content */}
      <main className="relative flex-1 overflow-auto p-6">
        <ErrorBoundary resetKey={pathname}>
          <Outlet />
        </ErrorBoundary>
      </main>

      {/* Chat panel */}
      <ChatPanel isOpen={chatOpen} onToggle={setChatOpen} />
    </div>
  );
}
