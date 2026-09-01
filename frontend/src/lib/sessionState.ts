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
 * `condor_display_currency`, `condor_bubble_open`, `condor_sheet_zen`,
 * `condor_trade_bottom_pane`, `routines_view_mode`, `condor.dex.network`,
 * `condor.dex.depth-collapsed`, `condor.pnl.hidden-series`,
 * `condor.bots.positions-open`, the workspace's own geometry
 * (`condor.dock.open`, `condor.dock.width`, `condor_pane_frac`,
 * `condor.chat.rail.open`), and the one-time hints
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

// ── KEPT keys ──
//
// Declared here and deliberately absent from SESSION_KEYS below. They are named
// in this module all the same, because it is the single definition site for
// every key name this app persists: a key that only ever appeared at its writer
// would be one the boundary above could not be reasoned about, and sorting it
// into CLEARED or KEPT is the decision this file exists to record.

/**
 * Which PnL series the charts draw, as a JSON array of series keys to *hide*
 * (FEAT-085). Empty or absent means draw everything, so a browser that has
 * never touched the legend and one whose storage is unreadable behave alike.
 *
 * KEPT: it says how this screen is set up, not what the last user was trading.
 * Every PnL chart in the app reads it, so it is a device preference rather than
 * a per-chart one — see the store in lib/pnl-chart.
 */
export const PNL_HIDDEN_SERIES_KEY = "condor.pnl.hidden-series";

/**
 * Whether the controller browser's positions band is open (FEAT-085).
 *
 * KEPT, and for the same reason `condor.dex.depth-collapsed` is: a disclosure
 * that is open or shut is a fact about this window, and the rows behind it are
 * re-fetched under whoever is logged in now.
 */
export const POSITIONS_BAND_KEY = "condor.bots.positions-open";

/**
 * How many pixels wide the reader dragged the context dock (ARCH-291).
 *
 * KEPT, beside the `condor.dock.open` that decides whether the column is there
 * at all: a column's width is a fact about this window, not about the
 * conversation that happened to be open in it, and the rows inside it are
 * re-fetched under whoever is logged in now.
 */
export const DOCK_WIDTH_KEY = "condor.dock.width";

/**
 * Where the reader put the split between the transcript and the workspace
 * pane, as a fraction of the row (ARCH-273, ARCH-291).
 *
 * KEPT, for the same reason the dock's width is: it says how this screen is
 * divided, and the report that lands in the pane is re-fetched under the
 * incoming identity like everything else on it.
 */
export const PANE_FRAC_KEY = "condor_pane_frac";

/**
 * Whether the conversation rail is a column or a strip of icons (ARCH-291).
 *
 * KEPT: a disclosure that is open or shut is a fact about this window, and the
 * conversations behind it are listed for whoever is logged in now. Only the
 * reader's own toggle is ever written here — the pane borrows the rail without
 * recording a preference — so what survives a logout is something they chose.
 */
export const CHAT_RAIL_OPEN_KEY = "condor.chat.rail.open";

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
