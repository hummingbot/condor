// ── Moving the run screen's URL, as rules (FEAT-117) ──
//
// `views.ts` is the *reading* half of the screen's grammar; this is the writing
// half. It was nine inline `setParams({…})` calls in
// `pages/AgentWorkspace.tsx`, each restating the same two cascades — moving the
// strategy drops the run and the tick, moving the run drops the tick — and each
// one a place the next caller could forget them.
//
// It stays a module now that there is one host again (FEAT-119), for the reason
// it became one: the cascades are the rules, they are asserted in this file's
// own tests, and inlining them back into JSX is how a control grown next year
// forgets one. What the host supplies is a search string, which is also what
// lets the screen be rendered in a test with no route around it.
//
// Nothing here fetches and nothing here renders.

import { useCallback, useMemo } from "react";

import { OPEN_PARAM } from "@/components/agent/workspace/sections";
import {
  parseWorkspace,
  type WorkspaceUrl,
} from "@/components/agent/workspace/views";

/** The four keys the screen spends. */
export const WORKSPACE_PARAMS = [
  "strategy",
  "run",
  "tick",
  OPEN_PARAM,
] as const;

/**
 * The two words the retired `?view=` grammar spelled a section with.
 *
 * Never written, and cleared by any write: the page answers them once with a
 * redirect (`sectionForView`) and nothing in the app produces one any more, so
 * a move that carried one along would put a dead parameter back on a live URL.
 */
const LEGACY_VIEW_PARAMS = ["view", "tab"] as const;

/**
 * A move, as the caller means it: only the keys it names are its business.
 *
 * A key present with `null` clears it; a key absent is *not* a request to keep
 * it — the cascades below decide that. This is the difference that makes the
 * module worth having: `{ strategy: "brl_mm" }` is "scope to this loop", and
 * every caller that meant it also meant "and stop pointing at the old loop's
 * run", which is exactly what four of the nine call sites spelled out by hand
 * and two forgot to.
 */
export interface WorkspaceUrlPatch {
  strategy?: string | null;
  run?: string | null;
  tick?: number | null;
  /** The disclosures, as `sections.ts` serializes them; `null` closes all. */
  open?: string | null;
}

/**
 * The query string with this move applied, leaving every other parameter alone.
 *
 * Left alone is the point: `?fscope=` is the fleet browser's and `?population=`
 * is `/bots`' filter, both of them spent on this same string by a disclosure
 * that has no idea a loop bar exists — and a scope change that wiped them would
 * reset a reader's fleet from three bands away.
 *
 * Two cascades, and both are "the reader moved up a level, so what was selected
 * below is not selected any more":
 *
 * - naming a strategy drops the run and the tick, because a `?run=` from
 *   another loop is not a run of this one;
 * - naming a run drops the tick, because tick 40 of the run you just left is
 *   not tick 40 of the one you just picked.
 *
 * An explicit key always wins over the cascade that would have cleared it, so
 * "this strategy, and this run of it" is still one call.
 */
export function applyWorkspacePatch(
  params: URLSearchParams,
  patch: WorkspaceUrlPatch,
): URLSearchParams {
  const next = new URLSearchParams(params);

  const write = (key: string, value: string | number | null | undefined) => {
    if (value === null || value === undefined || value === "") next.delete(key);
    else next.set(key, String(value));
  };

  if ("open" in patch) write(OPEN_PARAM, patch.open);
  if ("strategy" in patch) {
    write("strategy", patch.strategy);
    if (!("run" in patch)) next.delete("run");
    if (!("tick" in patch)) next.delete("tick");
  }
  if ("run" in patch) {
    write("run", patch.run);
    if (!("tick" in patch)) next.delete("tick");
  }
  if ("tick" in patch) write("tick", patch.tick);

  for (const key of LEGACY_VIEW_PARAMS) next.delete(key);
  return next;
}

/**
 * Whether this move is a history step or a correction of the current one.
 *
 * Reading down a page is not five entries to press Back through, so a move that
 * only opens or shuts a disclosure replaces. A scope, a run or a tick pushes,
 * which is what makes Back go one level shallower from any depth without ever
 * leaving the agent — and, for a tick, what makes Back the way out of the
 * overlay as well as the close button.
 */
export function patchReplaces(patch: WorkspaceUrlPatch): boolean {
  const keys = Object.keys(patch);
  return keys.length === 1 && keys[0] === "open";
}

/**
 * Everything a host must supply for the screen to read and move its own URL.
 *
 * An interface rather than a `useSearchParams()` inside the screen, which is
 * what let the chat's pane host the same component for two features (FEAT-117)
 * and is what still lets the screen be rendered in a test with a plain
 * `MemoryRouter` and no `/agents/:slug` route around it.
 */
export interface WorkspaceUrlAdapter {
  /** What the URL says — {@link parseWorkspace}, memoized. */
  url: WorkspaceUrl;
  /** Move it. See {@link applyWorkspacePatch}. */
  set: (patch: WorkspaceUrlPatch) => void;
}

/** Bind the grammar to a host's `useSearchParams`. */
export function useWorkspaceUrl(
  params: URLSearchParams,
  setParams: (
    next: URLSearchParams,
    options?: { replace?: boolean },
  ) => void,
): WorkspaceUrlAdapter {
  const url = useMemo(() => parseWorkspace(params), [params]);
  const set = useCallback(
    (patch: WorkspaceUrlPatch) =>
      setParams(applyWorkspacePatch(params, patch), {
        replace: patchReplaces(patch),
      }),
    [params, setParams],
  );
  return useMemo(() => ({ url, set }), [url, set]);
}
