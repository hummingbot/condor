import { useEffect, useRef } from "react";

/**
 * What a screen is showing, said by the screen itself.
 *
 * Any component can contribute facts for as long as it is mounted; the chat
 * collects them at send time and passes the rendered block *beside* the
 * message (never inside it), so the agent knows what the user is looking at
 * without the transcript being branded with a page they later left.
 */
export interface ViewFacts {
  /** What screen this is. "Bot detail", "DEX pool". */
  label: string;
  /** What it is about. `bot "backpack-mm-3" (id 42)` */
  subject?: string;
  /** What is actually rendered — already formatted for a human to read. */
  onScreen?: Record<string, string | number | null | undefined>;
}

// Keyed by an incrementing id so mount order is insertion order and an unmount
// removes only its own entry. The module this replaces (lib/viewContext.ts)
// was one mutable slot, and two overlapping contributors clobbered each other:
// whichever unmounted last nulled out whatever the other had set.
const registry = new Map<number, () => ViewFacts | null>();
let nextId = 0;

/** Contribute facts for as long as this component is mounted. */
export function useViewFacts(getter: () => ViewFacts | null): void {
  // The getter is held in a ref so a re-render refreshes what it says without
  // churning the registry — the entry is only ever *called* at send time,
  // which is what makes an on-screen snapshot free while idle and fresh when
  // asked.
  const ref = useRef(getter);
  useEffect(() => {
    ref.current = getter;
  });
  useEffect(() => {
    const id = nextId++;
    registry.set(id, () => ref.current());
    return () => {
      registry.delete(id);
    };
  }, []);
}

/** Every live contribution, outermost first. Never throws. */
export function collectViewFacts(): ViewFacts[] {
  const out: ViewFacts[] = [];
  for (const getter of registry.values()) {
    // A page that throws while composing its facts must degrade to no
    // context, never break sending a message.
    try {
      const facts = getter();
      if (facts && facts.label) out.push(facts);
    } catch {
      /* degrade to no context */
    }
  }
  return out;
}

export const VIEW_BLOCK_MAX_CHARS = 1200;

const HEADER =
  "[What the user is looking at right now, in the Condor dashboard. True of " +
  "this moment only — do not treat it as something the user said.]";

/**
 * The block that goes on the wire. `""` when there is nothing to say.
 *
 * `url` defaults to the browser's current path; a test passes its own.
 */
export function renderViewBlock(facts: ViewFacts[], url?: string): string {
  if (facts.length === 0) return "";
  const lines: string[] = [HEADER];
  for (const f of facts) {
    lines.push(`Screen: ${f.label}`);
    if (f.subject) lines.push(`About: ${f.subject}`);
    const shown = Object.entries(f.onScreen ?? {})
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => `${k} ${v}`);
    if (shown.length > 0) lines.push(`On screen: ${shown.join(" · ")}`);
  }
  const at =
    url ??
    (typeof window !== "undefined"
      ? window.location.pathname + window.location.search
      : "");
  if (at) lines.push(`URL: ${at}`);
  const block = lines.join("\n");
  return block.length > VIEW_BLOCK_MAX_CHARS
    ? block.slice(0, VIEW_BLOCK_MAX_CHARS - 1) + "…"
    : block;
}
