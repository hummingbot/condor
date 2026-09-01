/**
 * The `localStorage` half of a session boundary.
 *
 * `logout` and `resetForNewUser` (lib/auth.ts) already drop the token, the
 * stored user, the selected server, the React Query cache and the console-error
 * ring. They dropped nothing else, and logging out is a pure client-side
 * transition with no page reload — so everything else this app writes to
 * `localStorage` was inherited live by whoever logged in next. On a shared
 * browser that meant the incoming user's `/trade` panel opened pre-filled with
 * the previous user's size, side and leverage (one click from a real order),
 * and their routine panels pre-filled with the previous user's bot and server
 * names, which are then POSTed under the new identity (SEC-231).
 *
 * ── The split, because every new key has to be sorted into it ──
 *
 * CLEARED — anything the *user* typed or picked while working: order and
 * executor defaults, the last market, saved routine configs, the pairs and
 * pools they starred or recently entered. It describes a person's trading, not
 * this browser, and it is actionable the moment the next session renders it.
 *
 * KEPT — how this *device* renders the app: `condor_theme`,
 * `condor_display_currency`, `condor.dock.open`, `condor_bubble_open`,
 * `condor_sheet_zen`, `condor_trade_bottom_pane`, `routines_view_mode`,
 * `condor.dex.network`, `condor.dex.depth-collapsed`, and the one-time hints
 * (`condor.market.browse-hint`) that record how far this browser has been
 * onboarded. Wiping these would flip
 * the theme out from under someone logging out on their own laptop, and none of
 * them says anything about the outgoing session.
 *
 * The rule: if reading it back tells you what the previous user was *doing*,
 * clear it; if it only tells you how this screen is set up, keep it.
 *
 * This module is the single definition site for the cleared key names — the
 * writers import them from here — so a key can never be persisted under a name
 * the boundary does not know about.
 */

import { ROUTINE_CONFIG_KEY_PREFIX } from "@/lib/routineUtils";

/** Executor panel defaults: the shape of order each panel last submitted. */
export const ORDER_DEFAULTS_KEY = "condor_order_defaults";
export const POSITION_DEFAULTS_KEY = "condor_position_defaults";
export const DCA_DEFAULTS_KEY = "condor_dca_defaults";
export const LP_DEFAULTS_KEY = "condor_lp_defaults";
export const GRID_STORAGE_KEY = "condor_grid_defaults";

/** The connector + pair the user last traded, offered as the next default. */
export const LAST_MARKET_KEY = "condor_last_market";

/** Starred markets and pools. */
export const MARKET_FAVORITES_KEY = "condor_market_favorites";
export const DEX_FAVORITES_KEY = "condor_dex_favorites";

/** Recently-entered DEX pairs, suffixed with the connector. */
export const DEX_PAIRS_KEY_PREFIX = "condor_dex_pairs:";

const SESSION_KEYS = [
  ORDER_DEFAULTS_KEY,
  POSITION_DEFAULTS_KEY,
  DCA_DEFAULTS_KEY,
  LP_DEFAULTS_KEY,
  GRID_STORAGE_KEY,
  LAST_MARKET_KEY,
  MARKET_FAVORITES_KEY,
  DEX_FAVORITES_KEY,
];

/** Key families written one entry per routine / per connector. */
const SESSION_KEY_PREFIXES = [ROUTINE_CONFIG_KEY_PREFIX, DEX_PAIRS_KEY_PREFIX];

/**
 * Drop the outgoing session's stored form state, keeping device preferences.
 *
 * Called from both `logout` and `resetForNewUser` so the two paths cannot
 * drift, which is the same reason those two keep their other drops together.
 */
export function clearSessionState() {
  try {
    for (const key of SESSION_KEYS) localStorage.removeItem(key);
    // Collect first: removing while iterating `key(i)` re-indexes the store and
    // would skip every other match.
    const prefixed: string[] = [];
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && SESSION_KEY_PREFIXES.some((p) => key.startsWith(p))) {
        prefixed.push(key);
      }
    }
    for (const key of prefixed) localStorage.removeItem(key);
  } catch {
    // Storage disabled or full: a session boundary must not throw on its way out.
  }
}
