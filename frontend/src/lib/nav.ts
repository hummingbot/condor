import {
  Bot,
  Brain,
  Droplets,
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
 *
 * Fleet left it in FEAT-114, for the same reason one level up. *What is every
 * agent doing* is now the Execution panel of the chat's right rail, read owner
 * first — beside the conversation it is asked in, rather than a tab away from
 * it — so the nav loses an entry and the reader loses nothing. `/fleet`
 * redirects to that panel's own address.
 *
 * Floor left it in FEAT-116, for the reason Executors left it. *What does the
 * fleet add up to* is the report the browser behind Bots already draws — at its
 * own root, split by the level below it — so the floor is the fleet scope of
 * that browser rather than a second door onto the same records. `/floor`
 * redirects to `/bots`.
 */
export const NAV_ITEMS = [
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
 * `/` left this list for the length of FEAT-104, when it mounted two screens
 * under one path and the answer had to be read off the query string. The home
 * is the conversation again — a pathname with one body owning the viewport,
 * scrolling its own transcript — and the fleet overview it briefly shared the
 * route with is a panel of that conversation's rail now (FEAT-114).
 *
 * `/floor` was full bleed for its own two-part layout (FEAT-112) and is not a
 * route with a body any more (FEAT-116): a layout rule for it would be a rule
 * about a redirect. What it used to draw is `/bots`, which is on this list.
 */
export const FULL_BLEED_ROUTES = ["/", "/bots", "/routines"];
