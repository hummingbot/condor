import { useState } from "react";
import {
  Activity,
  Bot,
  CandlestickChart,
  LogOut,
  PanelLeftClose,
  PanelLeftOpen,
  Wallet,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import { useServer } from "@/hooks/useServer";
import { useAuth } from "@/lib/auth";

import { ServerSelector } from "./ServerSelector";

const NAV_ITEMS = [
  { to: "/", icon: Wallet, label: "Portfolio" },
  { to: "/bots", icon: Bot, label: "Bots" },
  { to: "/executors", icon: Activity, label: "Executors" },
  { to: "/market", icon: CandlestickChart, label: "Market" },
] as const;

export function AppShell() {
  const { user, logout } = useAuth();
  const { server } = useServer();
  const { pathname } = useLocation();
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex h-screen">
      {/* Sidebar */}
      <aside
        className={`flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all duration-200 ${
          collapsed ? "w-14" : "w-56"
        }`}
      >
        <div className="flex items-center border-b border-[var(--color-border)] p-4">
          {collapsed ? (
            <img src="/condor.jpeg" alt="Condor" className="h-7 w-7 rounded-full" />
          ) : (
            <h1 className="flex flex-1 items-center gap-2 text-lg font-bold tracking-tight">
              <img src="/condor.jpeg" alt="Condor" className="h-7 w-7 rounded-full" />
              Condor
            </h1>
          )}
        </div>

        {!collapsed && (
          <div className="border-b border-[var(--color-border)] p-3">
            <ServerSelector />
          </div>
        )}

        <nav className="flex-1 p-2">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              title={collapsed ? label : undefined}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                  collapsed ? "justify-center" : ""
                } ${
                  isActive
                    ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                    : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                }`
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              {!collapsed && label}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-[var(--color-border)] p-3">
          {collapsed ? (
            <div className="flex flex-col items-center gap-2">
              <button
                onClick={logout}
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-red)]"
                title="Logout"
              >
                <LogOut className="h-4 w-4" />
              </button>
              <button
                onClick={() => setCollapsed(false)}
                className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                title="Expand sidebar"
              >
                <PanelLeftOpen className="h-4 w-4" />
              </button>
            </div>
          ) : (
            <div className="flex items-center justify-between text-sm">
              <span className="truncate text-[var(--color-text-muted)]">
                {user?.first_name || user?.username || "User"}
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setCollapsed(true)}
                  className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
                  title="Collapse sidebar"
                >
                  <PanelLeftClose className="h-4 w-4" />
                </button>
                <button
                  onClick={logout}
                  className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-red)]"
                  title="Logout"
                >
                  <LogOut className="h-4 w-4" />
                </button>
              </div>
            </div>
          )}
        </div>
      </aside>

      {/* Main content */}
      <main className="flex-1 overflow-auto p-6">
        <ErrorBoundary resetKey={pathname + server}>
          <Outlet />
        </ErrorBoundary>
      </main>
    </div>
  );
}
