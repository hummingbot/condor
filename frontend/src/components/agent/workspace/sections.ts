// ── The run screen's sections, as rules (FEAT-119) ──
//
// `/agents/:slug` is the run you are looking at, with its evidence behind a
// tab strip. Which tab is showing is a fact about the address, the same way
// `?strategy=` and `?run=` are — so a colleague can be sent "the fleet, on this
// run" rather than "open the page and click twice".
//
// Nothing here fetches and nothing here renders.

import { PANE_SECTION_KEY } from "@/lib/sessionState";

/**
 * The evidence beside the run, in the order the tabs draw it.
 *
 * Money was a sixth and is gone: it headlined the whole fleet's fold, which is
 * what Fleet already charts — two tabs answering one question under two names.
 * Detail went the same way (ARCH-427): its bots, controllers and executors are
 * Fleet's records narrowed to the run, its deeds and decisions are the Runs
 * tab's ticks, and the two bands only it drew — the agent's canvas and the
 * run's market chart — now head the session on Runs.
 */
export const SECTIONS = ["runs", "fleet", "playbook"] as const;

export type SectionId = (typeof SECTIONS)[number];

/**
 * Where a retired tab's address lands.
 *
 * `detail` is in `?open=` bookmarks, in the pane's `?sec=` and in a reader's
 * remembered pane tab. Its controllers and executors are Fleet's now, so that
 * is where it opens — rather than on Now, as if the address had named nothing.
 */
const RETIRED: ReadonlyMap<string, SectionId> = new Map([["detail", "fleet"]]);

function current(id: string): string {
  return RETIRED.get(id) ?? id;
}

/** Which section the page shows, in the query string. */
export const OPEN_PARAM = "open";

/**
 * `"runs.fleet"` → `["runs", "fleet"]`, or `null` when the URL names none.
 *
 * Kept as a list parser because `?open=` was a *set* of disclosures before the
 * page took the pane's tabs, and those addresses are in bookmarks and
 * notification payloads — the page reads the first one it knows.
 */
export function parseSections(raw: string | null | undefined): SectionId[] | null {
  if (raw === null || raw === undefined) return null;
  const text = raw.trim();
  if (!text) return [];
  const ids = ordered(text.split(".").map((part) => current(part.trim())));
  return ids;
}

/** In the order the screen draws them, whatever order they were clicked in. */
function ordered(ids: readonly unknown[]): SectionId[] {
  return SECTIONS.filter((id) => ids.includes(id));
}

/**
 * Where a retired `?view=` lands now.
 *
 * The redirect table in one place, because `?view=` is the compatibility
 * surface this feature spends: it is in notification payloads, in the chat's
 * route facts and in whatever anyone has bookmarked. Every value has to land
 * somewhere, so the page does nothing but call this.
 *
 * `money` lands on Fleet, which charts the same fold. `null` is a real answer
 * for the rest: `now` is the first tab, `tick` is an overlay `?tick=` opens on
 * its own, and a `?view=` naming one of the seven **Being** sections is not
 * this screen's at all any more (FEAT-118) — the page sends those to the
 * chat's panel before it ever asks this.
 */
export function sectionForView(view: string | null | undefined): SectionId | null {
  switch (view) {
    case "runs":
      return "runs";
    case "money":
    case "fleet":
      return "fleet";
    case "playbook":
      return "playbook";
    default:
      return null;
  }
}

// ── The same screen, one section at a time (the chat's side panel) ──
//
// Both hosts show one section at a time behind a tab strip — the answer stack
// is "Now", the first tab. The page is the pane made big: the same tabs, the
// same bodies, with `?open=` naming its tab where the pane has its own key.

/** The pane's tabs, in the order the page draws the same sections. */
export const PANE_SECTIONS = ["now", ...SECTIONS] as const;

export type PaneSection = (typeof PANE_SECTIONS)[number];

export function isPaneSection(value: unknown): value is PaneSection {
  return (PANE_SECTIONS as readonly unknown[]).includes(value);
}

/**
 * A stored or linked pane tab, read: the tab it names, a retired tab's
 * successor (`detail` → Fleet), else `null`.
 */
export function readPaneSection(value: unknown): PaneSection | null {
  const id = typeof value === "string" ? current(value) : value;
  return isPaneSection(id) ? id : null;
}

/**
 * The section the pane was last on, for a pane opened without one.
 *
 * Closing the pane erases its address, so without this every re-open landed on
 * Now whatever the reader had been reading — the same reason
 * `KNOWLEDGE_TAB_KEY` exists for the agent panel.
 */
export function lastPaneSection(): PaneSection {
  try {
    return readPaneSection(localStorage.getItem(PANE_SECTION_KEY)) ?? "now";
  } catch {
    return "now";
  }
}

export function rememberPaneSection(section: PaneSection): void {
  try {
    localStorage.setItem(PANE_SECTION_KEY, section);
  } catch {
    // Not remembered is still opened.
  }
}

/** The page's tab, off `?open=`: the first section it names, else Now. */
export function pageSection(raw: string | null | undefined): PaneSection {
  return parseSections(raw)?.[0] ?? "now";
}

/** The page's `?open=` for a tab — cleared for Now, the page's default. */
export function openForPaneSection(section: PaneSection): string {
  return section === "now" ? "" : section;
}
