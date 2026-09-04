import {
  Activity,
  Bot,
  Brain,
  Droplets,
  LayoutDashboard,
  Settings,
  Swords,
  Wallet,
  Zap,
} from "lucide-react";


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
export const NAV_ITEMS = [
  { to: "/", icon: Brain, label: "Agents" },
  { to: "/fleet", icon: Activity, label: "Fleet" },
  { to: "/floor", icon: LayoutDashboard, label: "Floor" },
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
 * `/` left this list for the length of FEAT-104, when it mounted two screens
 * under one path and the answer had to be read off the query string. The
 * overview is `/fleet` now and the home is the conversation again, so both are
 * back to being what they always were: a pathname with one body, each owning
 * the viewport. The chat scrolls its transcript; the fleet is a screen-tall
 * list that scrolls itself.
 *
 * `/floor` is full bleed for the same reason (FEAT-112): a sticky fleet strip
 * over a body that scrolls under it is a two-part layout, and `main`'s 24px and
 * its own scrollbar would give the page a second one.
 */
export const FULL_BLEED_ROUTES = ["/", "/bots", "/routines", "/floor", "/fleet"];
