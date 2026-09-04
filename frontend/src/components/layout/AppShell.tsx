import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import {
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

import { ChatBubble } from "@/components/chat/ChatBubble";
import { ConnectKeysOverlay } from "@/components/ConnectKeysOverlay";
import { ErrorBoundary } from "@/components/ErrorBoundary";
import { RelaunchBanner } from "@/components/RelaunchBanner";
import { ReportIssueDialog } from "@/components/ReportIssueDialog";
import { TelemetryConsentBanner } from "@/components/TelemetryConsentBanner";
import { ChatProvider } from "@/hooks/useChat";
import { useCredentials } from "@/hooks/useCredentials";
import { usePrefetchData } from "@/hooks/usePrefetchData";
import { useServer } from "@/hooks/useServer";
import { useTheme } from "@/hooks/useTheme";
import { FULL_BLEED_HOME_VIEWS, homePath, homeView } from "@/lib/homeView";
import { routeFacts } from "@/lib/pageFacts";
import { useViewFacts } from "@/lib/viewFacts";
import { CurrencySelector } from "./CurrencySelector";
import { NotificationBell } from "./NotificationBell";
import { ServerSelector } from "./ServerSelector";

/**
 * The main nav.
 *
 * Routines left it in FEAT-077 and came back in FEAT-091: the library still
 * opens beside the conversation that wants it and from an agent's own page, but
 * plenty of work starts at the library rather than at a chat, and for that the
 * nav was the only door. `/routines` is now the same full-screen browser those
 * two surfaces open, at full width.
 *
 * Executors left it in FEAT-086, for a different reason: they are not a
 * separate thing to look at. A controller is a bag of executors, so both are
 * scopes of the one browser behind Bots, and two doors to one report was the
 * problem that feature exists to fix.
 */
const NAV_ITEMS = [
  { to: "/", icon: Brain, label: "Agents" },
  { to: "/portfolio", icon: Wallet, label: "Portfolio" },
  { to: "/trade", icon: Swords, label: "Trade" },
  { to: "/dex", icon: Droplets, label: "DEX" },
  { to: "/bots", icon: Bot, label: "Bots" },
  { to: "/routines", icon: Zap, label: "Routines" },
  { to: "/settings", icon: Settings, label: "Settings" },
] as const;

/**
 * Routes that lay themselves out edge to edge and scroll inside their own panes,
 * so `main` gives them no padding and no scrollbar of its own.
 *
 * `/bots` joined the chat workspace when the controller browser became the page
 * (FEAT-084): a scope sidebar and a report column, both screen-tall, have
 * nothing to do with `main`'s 24px. `/routines` is the same shape, for the same
 * reason (FEAT-091).
 *
 * `/` left this list in FEAT-104. It is not one route with one body any more:
 * it mounts a conversation and a fleet overview, and which of them is on screen
 * is a query parameter — so the answer comes from `FULL_BLEED_HOME_VIEWS`,
 * which happens to be *both* of them, rather than from the pathname.
 */
const FULL_BLEED_ROUTES = ["/bots", "/routines"];

/**
 * Full-bleed routes that carry a parameter, so an exact match cannot find them.
 *
 * The Lab (`/agents/:slug/runs`, FEAT-099) was the first: a rail, a tick spine
 * and a body, all screen-tall, under a slug the shell cannot enumerate. The
 * agent workspace absorbed it (FEAT-103) and is the same shape in every view —
 * a header, a loop bar, a spine and a body, each scrolling on its own — so the
 * Lab's pattern is replaced by an exact match on the one route. Kept as a
 * separate pattern list rather than turned into a `startsWith` over the array
 * above, because `/` is in that array and prefixes everything.
 */
const FULL_BLEED_PATTERNS = [/^\/agents\/[^/]+$/];

/**
 * The shell owns the chat state.
 *
 * There used to be two surfaces rendering a conversation — an overlay panel
 * docked to the right of every page, and the workspace at `/` — which meant
 * two doors to one thing. The panel is gone; the provider stays here so the
 * socket outlives navigation between pages and the workspace.
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
  const { pathname, search } = useLocation();
  const { theme, toggleTheme } = useTheme();
  const navigate = useNavigate();
  const { hasKeys, isLoading: keysLoading } = useCredentials();
  const [reportOpen, setReportOpen] = useState(false);

  // Which of the home's two views is on screen, or `null` off the home route
  // (FEAT-104). The three special cases below used to be three readings of the
  // pathname; `/` now mounts a conversation *and* a fleet overview, so they are
  // three readings of the view — which is what lets step 3 flip the default in
  // one revertible commit without touching this file.
  const homeVersion = pathname === "/" ? homeView(search) : null;

  // Both views of the home take the full height and own their own scrolling,
  // so the shell drops `main`'s padding for them. `/agents/:slug` is an
  // ordinary padded page, deliberately not matched here.
  const isFullBleed =
    (homeVersion !== null && FULL_BLEED_HOME_VIEWS.includes(homeVersion)) ||
    FULL_BLEED_ROUTES.includes(pathname) ||
    FULL_BLEED_PATTERNS.some((re) => re.test(pathname));

  // The home is exempt in *both* its views (FEAT-104 step 3). The exemption
  // used to be the chat's alone, on the grounds that it is the one surface that
  // can talk a new user through connecting keys. Flipping the default moved the
  // front door: an install with no keys would otherwise meet a blocking overlay
  // before it ever met the product, over a page that needs no keys either — the
  // overview reads agents, loops and journals, none of which are an exchange —
  // and with the chat that would explain it hidden one click behind the block.
  // The route is the unit here, not the view, so the exemption cannot come
  // undone the next time the default moves.
  const exemptRoutes = ["/routines", "/settings"];
  const showKeysOverlay =
    server && !keysLoading && !hasKeys && homeVersion === null &&
    !exemptRoutes.some((r) => pathname.startsWith(r));

  // ⌘K used to toggle the overlay panel. It now goes to the chat, so the
  // reflex still lands somewhere sensible instead of silently doing nothing.
  //
  // Asked for by view rather than spelled as a path (FEAT-104): this resolved
  // to a bare `/` while the chat was the home and resolves to `/?view=chat` now
  // that the overview is, and step 3 needed no edit here to make that true. The
  // reflex is a keystroke to a *conversation*, whatever the home is.
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        navigate(homePath("chat"));
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  // Prefetch core data (executors, bots) and subscribe to WS channels early
  usePrefetchData();

  // The route baseline for the chat's page context (FEAT-059): every page
  // gets a label and a URL-derived subject through the same seam richer
  // contributors use, so the chat never has to know about the router.
  // The client is handed over so the table can also read what each page is
  // rendering (FEAT-060) — at send time, out of the cache the page fetched
  // through, rather than from eight components each pushing their state here.
  const queryClient = useQueryClient();
  useViewFacts(() => routeFacts(pathname, search, queryClient));

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
            {/* Inside the ChatProvider on purpose: the bell reads the same
                react-query cache the chat socket pushes live notifications
                into, and the provider opens that socket on every route — not
                only on the chat workspace — which is what makes the bell
                update without a reload wherever the user is (FEAT-048). */}
            <NotificationBell />

            <button
              onClick={() => setReportOpen(true)}
              className="rounded p-1.5 text-[var(--color-text-muted)] hover:bg-[var(--color-surface-hover)] hover:text-[var(--color-accent)]"
              title="Report an issue"
            >
              <Bug className="h-4 w-4" />
            </button>

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

      {/* An update landed but this process is still the old one, so the bundle
          in the browser is ahead of the API answering it. Above `main` for the
          same reason the consent strip is: every page needs it, and the chat
          workspace owns its own scrolling. Renders nothing the rest of the
          time, which is nearly always. */}
      <RelaunchBanner />

      {/* Asks once, for an install that has never answered (FEAT-023). Renders
          nothing for everyone else, including on the chat workspace, which owns
          its own scrolling — hence outside `main` rather than inside it. */}
      <TelemetryConsentBanner />

      {/* Main content */}
      <main
        className={`relative flex-1 ${
          isFullBleed ? "overflow-hidden" : "overflow-auto p-6"
        }`}
      >
        <ErrorBoundary resetKey={pathname + server}>
          <Outlet key={server} />
        </ErrorBoundary>
        {showKeysOverlay && <ConnectKeysOverlay />}
      </main>

      <ReportIssueDialog open={reportOpen} onClose={() => setReportOpen(false)} />

      {/* The quick chat on every page but `/` (FEAT-059). Inside the
          provider, outside `main`, so it neither scrolls with the page nor
          unmounts on navigation. */}
      <ChatBubble />
    </div>
  );
}
