// ── The run screen's disclosures, as rules (FEAT-119) ──
//
// `/agents/:slug` is one screen now: the run you are looking at, read top to
// bottom, with the evidence behind five disclosures under it. Which of them are
// open is a fact about the address, the same way `?strategy=` and `?run=` are —
// so a colleague can be sent "the money and the fleet, on this run" rather than
// "open the page and click twice".
//
// The grammar is not invented here. `accountPanels.ts` already spends
// `?desk=portfolio.execution` on the chat's account panels — a `.`-joined list
// of section ids, unknown ids dropped, repeats collapsed, nothing named at all
// falling back to what this browser recorded — and copying those rules id for
// id is what keeps a second grammar from describing the same kind of thing.
//
// Nothing here fetches and nothing here renders.

import { useCallback, useEffect, useRef, useState } from "react";

import { AGENT_SECTIONS_KEY } from "@/lib/sessionState";

/** The evidence under the answer stack, in the order the screen draws it. */
export const SECTIONS = ["runs", "detail", "money", "fleet", "playbook"] as const;

export type SectionId = (typeof SECTIONS)[number];

/** Which disclosures are open, in the query string. */
export const OPEN_PARAM = "open";

/**
 * `"runs.money"` → `["runs", "money"]`, or `null` when the URL names none.
 *
 * `null` and `[]` are different answers and the difference is load-bearing:
 * nothing named at all falls back to what this browser had open, where an
 * explicit empty value is a reader who closed everything.
 */
export function parseSections(raw: string | null | undefined): SectionId[] | null {
  if (raw === null || raw === undefined) return null;
  const text = raw.trim();
  if (!text) return [];
  const ids = ordered(text.split(".").map((part) => part.trim()));
  return ids;
}

/** In the order the screen draws them, whatever order they were clicked in. */
function ordered(ids: readonly unknown[]): SectionId[] {
  return SECTIONS.filter((id) => ids.includes(id));
}

/** `["runs","money"]` → `"runs.money"`; `""` for none, which clears the key. */
export function serializeSections(ids: readonly SectionId[]): string {
  return ordered(ids).join(".");
}

/**
 * Where a retired `?view=` lands now.
 *
 * The redirect table in one place, because `?view=` is the compatibility
 * surface this feature spends: it is in notification payloads, in the chat's
 * route facts and in whatever anyone has bookmarked. Every value has to land
 * somewhere, so the page does nothing but call this.
 *
 * `null` is a real answer for three of them. `now` was the answer stack, which
 * is the screen itself; `tick` was a body and is an overlay `?tick=` opens on
 * its own; a `?view=` naming one of the seven **Being** sections is not this
 * screen's at all any more (FEAT-118) — the page sends those to the chat's
 * panel before it ever asks this.
 */
export function sectionForView(view: string | null | undefined): SectionId | null {
  switch (view) {
    case "runs":
      return "runs";
    case "money":
      return "money";
    case "fleet":
      return "fleet";
    case "playbook":
      return "playbook";
    default:
      return null;
  }
}

function readSections(): SectionId[] {
  try {
    const raw = JSON.parse(localStorage.getItem(AGENT_SECTIONS_KEY) || "[]");
    if (!Array.isArray(raw)) return [];
    return ordered(raw);
  } catch {
    // Unreadable storage is a browser that has never opened a disclosure, not
    // an error to surface: the answer stack is on screen either way.
    return [];
  }
}

function writeSections(ids: readonly SectionId[]): void {
  try {
    localStorage.setItem(AGENT_SECTIONS_KEY, JSON.stringify(ordered(ids)));
  } catch {
    // Storage disabled or full: the disclosure still opens, it is just not
    // remembered. Losing a preference must not lose the click.
  }
}

/**
 * Which disclosures are open, and the toggle that moves them.
 *
 * `useAccountPanels`' rules, with one difference: the toggles write the URL as
 * well as the storage. The desk is a pane the reader is *in*, and a parameter
 * per click there would be a history stack nobody can press Back through; a
 * disclosure on a page is a thing you send someone, so `?open=` follows the
 * reader rather than only carrying them in. The write replaces rather than
 * pushes — reading down a page is not five entries to press Back through — and
 * that is decided by `patchReplaces`, one module over.
 *
 * The URL wins on arrival and on a *changed* `?open=`; storage wins when the
 * URL says nothing. Guarded on the raw value so the effect fires once per
 * distinct parameter and never argues with a toggle that just wrote it.
 */
export function useSections(
  raw: string | null,
  setRaw: (next: string) => void,
): { open: SectionId[]; toggle: (id: SectionId) => void } {
  const [open, setOpen] = useState<SectionId[]>(
    () => parseSections(raw) ?? readSections(),
  );

  const applied = useRef(raw);
  useEffect(() => {
    if (applied.current === raw) return;
    applied.current = raw;
    const wanted = parseSections(raw);
    if (!wanted) return;
    setOpen(wanted);
    writeSections(wanted);
  }, [raw]);

  // Off `open` rather than out of a `setOpen` updater: writing storage and the
  // URL is a side effect, and an updater is a function React is free to call
  // twice.
  const toggle = useCallback(
    (id: SectionId) => {
      const next = open.includes(id)
        ? open.filter((s) => s !== id)
        : ordered([...open, id]);
      setOpen(next);
      writeSections(next);
      const serialized = serializeSections(next);
      // The effect above must not read our own write back as an arrival.
      applied.current = serialized || null;
      setRaw(serialized);
    },
    [open, setRaw],
  );

  return { open, toggle };
}
