import { useEffect, useState } from "react";
import {
  Activity,
  Bot,
  Brain,
  Bug,
  Droplets,
  Eye,
  Moon,
  Settings,
  Sun,
  Swords,
  Wallet,
  Zap,
} from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";

import { ConnectKeysOverlay } from "@/components/ConnectKeysOverlay";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { ReportIssueDialog } from "@/components/ReportIssueDialog";
import { ChatProvider } from "@/hooks/useChat";
import { useCredentials } from "@/hooks/useCredentials";
import { usePrefetchData } from "@/hooks/usePrefetchData";
import { useServer } from "@/hooks/useServer";
import { useTheme } from "@/hooks/useTheme";
import { CurrencySelector } from "./CurrencySelector";
import { ServerSelector } from "./ServerSelector";

const NAV_ITEMS = [
  { to: "/", icon: Brain, label: "Agents" },
  { to: "/portfolio", icon: Wallet, label: "Portfolio" },
  { to: "/trade", icon: Swords, label: "Trade" },
  { to: "/dex", icon: Droplets, label: "DEX" },
  { to: "/bots", icon: Bot, label: "Bots" },
  { to: "/executors", icon: Activity, label: "Executors" },
  { to: "/routines", icon: Zap, label: "Routines" },
] as const;

/**
 * The shell owns the chat state.
 *
 * There used to be two surfaces rendering a conversation — an overlay panel
 * docked to the right of every page, and the workspace at `/agents` — which
 * meant two doors to one thing. The panel is gone; the provider stays here so
 * the socket outlives navigation between pages and `/agents`.
 */
export function AppShell() {
  return (
    <ChatProvider>
      <AppShellBody />
    </ChatProvider>
  );
}

function AppShellBody() {
  const { server } = useServer();
  const { pathname } = useLocation();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const { hasKeys, isLoading: keysLoading } = useCredentials();
  const [reportOpen, setReportOpen] = useState(false);

  // The chat workspace takes the full height and owns its own scrolling, so
  // the shell drops `main`'s padding for it. It lives at `/` — the entry point
  // — while `/agents/:slug` is an ordinary padded page, deliberately not
  // matched here.
  const isChatWorkspace = pathname === "/";

  // The chat is the landing page and needs no exchange keys, so the blocking
  // overlay would otherwise be the first thing every unconfigured user hits —
  // on the one surface that can talk them through connecting.
  const exemptRoutes = ["/routines", "/settings"];
  const showKeysOverlay =
    server && !keysLoading && !hasKeys && !isChatWorkspace &&
    !exemptRoutes.some((r) => pathname.startsWith(r));

  // ⌘K used to toggle the overlay panel. It now goes to the chat, so the
  // reflex still lands somewhere sensible instead of silently doing nothing.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        navigate("/");
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

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
          <CurrencySelector />

          <div className="flex items-center gap-1">
            <button
              onClick={() => setReportOpen(true)}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-accent)]"
              title="Report an issue"
            >
              <Bug className="h-4 w-4" />
            </button>

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

          </div>
        </div>
      </header>

      {/* Main content */}
      <main
        className={`relative flex-1 ${
          isChatWorkspace ? "overflow-hidden" : "overflow-auto p-6"
        }`}
      >
        <ErrorBoundary resetKey={pathname + server}>
          <Outlet key={server} />
        </ErrorBoundary>
        {showKeysOverlay && <ConnectKeysOverlay />}
      </main>

      <ReportIssueDialog open={reportOpen} onClose={() => setReportOpen(false)} />
    </div>
  );
}
