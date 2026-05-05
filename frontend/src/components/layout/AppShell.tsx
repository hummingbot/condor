import { useState } from "react";
import {
  Activity,
  Bot,
  Brain,
  CandlestickChart,
  Swords,
  Zap,
  LogOut,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Sun,
  Wallet,
} from "lucide-react";
import { NavLink, Outlet, useLocation } from "react-router-dom";

import { ErrorBoundary } from "@/components/ErrorBoundary";
import { usePrefetchData } from "@/hooks/usePrefetchData";
import { useServer } from "@/hooks/useServer";
import { useTheme } from "@/hooks/useTheme";
import { useAuth } from "@/lib/auth";

import { ServerSelector } from "./ServerSelector";

const NAV_ITEMS = [
  { to: "/", icon: Wallet, label: "Portfolio" },
  { to: "/trade", icon: Swords, label: "Trade" },
  { to: "/bots", icon: Bot, label: "Bots" },
  { to: "/executors", icon: Activity, label: "Executors" },
  { to: "/agents", icon: Brain, label: "Agents" },
  { to: "/routines", icon: Zap, label: "Routines" },
  { to: "/market", icon: CandlestickChart, label: "Market" },
] as const;

export function AppShell() {
  const { user, logout } = useAuth();
  const { server } = useServer();
  const { pathname } = useLocation();
  const { theme, toggleTheme } = useTheme();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  // Prefetch core data (executors, bots) and subscribe to WS channels early
  usePrefetchData();

  const SidebarContent = ({ isMobile = false }) => (
    <>
      <div className="flex items-center justify-between border-b border-[var(--color-border)] p-4">
        <h1 className="flex items-center gap-2 text-lg font-bold tracking-tight">
          <img src="/condor_old.jpeg" alt="Condor" className="h-7 w-7 rounded-full" />
          {(!collapsed || isMobile) && "Condor"}
        </h1>
        {!isMobile && (
          <button
            onClick={() => setCollapsed(!collapsed)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            {collapsed ? <PanelLeftOpen className="h-5 w-5" /> : <PanelLeftClose className="h-4 w-4" />}
          </button>
        )}
        {isMobile && (
          <button
            onClick={() => setMobileMenuOpen(false)}
            className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
          >
            <PanelLeftClose className="h-5 w-5" />
          </button>
        )}
      </div>

      {(!collapsed || isMobile) && (
        <div className="border-b border-[var(--color-border)] p-3">
          <ServerSelector />
        </div>
      )}

      <nav className="flex-1 p-2 overflow-y-auto">
        {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
          <NavLink
            key={to}
            to={to}
            end={to === "/"}
            onClick={() => isMobile && setMobileMenuOpen(false)}
            title={collapsed && !isMobile ? label : undefined}
            className={({ isActive }) =>
              `flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors ${
                collapsed && !isMobile ? "justify-center" : ""
              } ${
                isActive
                  ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
                  : "text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-text)]"
              }`
            }
          >
            <Icon className="h-4 w-4 shrink-0" />
            {(!collapsed || isMobile) && label}
          </NavLink>
        ))}
      </nav>

      <div className="border-t border-[var(--color-border)] p-3">
        {collapsed && !isMobile ? (
          <div className="flex flex-col items-center gap-2">
            <button onClick={toggleTheme} className="p-1 text-[var(--color-text-muted)] hover:text-[var(--color-accent)]">
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
            <button onClick={logout} className="p-1 text-[var(--color-text-muted)] hover:text-[var(--color-red)]">
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        ) : (
          <div className="flex items-center justify-between text-sm">
            <span className="truncate text-[var(--color-text-muted)] font-medium mr-2">
              {user?.first_name || user?.username || "User"}
            </span>
            <div className="flex items-center gap-1">
              <button onClick={toggleTheme} className="p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-accent)]">
                {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
              </button>
              <button onClick={logout} className="p-1.5 text-[var(--color-text-muted)] hover:text-[var(--color-red)]">
                <LogOut className="h-4 w-4" />
              </button>
            </div>
          </div>
        )}
      </div>
    </>
  );

  return (
    <div className="flex h-screen overflow-hidden bg-[var(--color-bg)]">
      {/* Desktop Sidebar */}
      <aside
        className={`hidden md:flex flex-col border-r border-[var(--color-border)] bg-[var(--color-surface)] transition-all duration-200 ${
          collapsed ? "w-14" : "w-56"
        }`}
      >
        <SidebarContent />
      </aside>

      {/* Mobile Sidebar Overlay */}
      {mobileMenuOpen && (
        <div 
          className="fixed inset-0 z-50 bg-black/50 md:hidden"
          onClick={() => setMobileMenuOpen(false)}
        >
          <aside 
            className="flex h-full w-64 flex-col bg-[var(--color-surface)] shadow-xl animate-in slide-in-from-left duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            <SidebarContent isMobile />
          </aside>
        </div>
      )}

      {/* Main content area */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Mobile Header */}
        <header className="flex items-center justify-between border-b border-[var(--color-border)] bg-[var(--color-surface)] p-3 md:hidden">
          <div className="flex items-center gap-2">
            <button
              onClick={() => setMobileMenuOpen(true)}
              className="rounded p-1 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)]"
            >
              <PanelLeftOpen className="h-6 w-6" />
            </button>
            <span className="font-bold">Condor</span>
          </div>
          <div className="flex items-center gap-2">
            <ServerSelector />
          </div>
        </header>

        <main className="flex-1 overflow-auto p-4 md:p-6">
          <ErrorBoundary resetKey={pathname + server}>
            <Outlet />
          </ErrorBoundary>
        </main>
      </div>
    </div>
  );
}
