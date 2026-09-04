// ── Moving the workspace's URL, as rules (FEAT-117) ──
//
// `views.ts` is the *reading* half of the workspace's grammar; this is the
// writing half. It was nine inline `setParams({…})` calls in
// `pages/AgentWorkspace.tsx`, each restating the same two cascades — moving the
// strategy drops the run and the tick, moving the run drops the tick — and each
// one a place the next caller could forget them.
//
// It becomes a module because the workspace now has two hosts (FEAT-117): the
// page at `/agents/:slug` and the chat's pane at `/?panel=agent`. Both spend the
// same four parameters on their own query string, so the cascades have to be
// written once or they drift apart the first time one host grows a control the
// other does not have.
//
// Nothing here fetches and nothing here renders.

import { useCallback, useMemo } from "react";

import {
  DEFAULT_VIEW,
  parseWorkspace,
  type WorkspaceUrl,
  type WorkspaceViewId,
} from "@/components/agent/workspace/views";

/** The four keys the workspace spends, wherever it is hosted. */
export const WORKSPACE_PARAMS = ["view", "strategy", "run", "tick"] as const;

/**
 * The legacy synonym for `?view=`, read by {@link parseWorkspace} and never
 * written — one grammar goes out, so any write also retires it.
 */
const LEGACY_VIEW_PARAM = "tab";

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
  view?: WorkspaceViewId | null;
  strategy?: string | null;
  run?: string | null;
  tick?: number | null;
}

/**
 * The query string with this move applied, leaving every other parameter alone.
 *
 * Left alone is the point: on the page the rest of the string is nothing, but
 * in the pane it is `?panel=`, `?who=` and `?desk=` — the home's own state,
 * which a workspace control has no business touching.
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

  if ("view" in patch) write("view", patch.view);
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

  // `view=now` is the default, so it is never spelled out: the shortest URL
  // that lands somewhere is the one people paste.
  if (next.get("view") === DEFAULT_VIEW) next.delete("view");
  next.delete(LEGACY_VIEW_PARAM);
  return next;
}

/**
 * Whether this move is a history step or a correction of the current one.
 *
 * Reading down the sections is not nine entries to press Back through, so a
 * move that only changes the section replaces. A scope, a run or a tick pushes,
 * which is what makes Back go one level shallower from any depth without ever
 * leaving the agent.
 */
export function patchReplaces(patch: WorkspaceUrlPatch): boolean {
  const keys = Object.keys(patch);
  return keys.length === 1 && keys[0] === "view";
}

/**
 * Just the workspace's four keys, for a host handing its state to another one.
 *
 * The pane's full-screen control is the only caller: `/agents/:slug` wants the
 * view, the scope, the run and the tick and none of the home's `?panel=`.
 * Copied off the raw string rather than rebuilt from {@link WorkspaceUrl},
 * because `?run=` has two spellings that both parse (`s:3` and the Lab's `s3`)
 * and the trip through a page should hand back the one that was given.
 */
export function workspaceSearch(params: URLSearchParams): URLSearchParams {
  const next = new URLSearchParams();
  for (const key of WORKSPACE_PARAMS) {
    const value = params.get(key);
    if (value) next.set(key, value);
  }
  return next;
}

/** Strip every trace of a workspace from a query string. */
export function clearWorkspaceSearch(
  params: URLSearchParams,
): URLSearchParams {
  const next = new URLSearchParams(params);
  for (const key of WORKSPACE_PARAMS) next.delete(key);
  next.delete(LEGACY_VIEW_PARAM);
  return next;
}

/**
 * Everything a host must supply for the body to read and move its own URL.
 *
 * One interface and one implementation for both hosts, rather than a
 * `pageAdapter` and a `paneAdapter`: the page and the pane read the same four
 * keys off the same kind of search string and both must leave the rest of it
 * alone, so two functions would have had one body and would have been the
 * drift this module exists to prevent. What differs between the hosts is the
 * *search string they are handed*, and that is the argument.
 */
export interface WorkspaceUrlAdapter {
  /** What the URL says — {@link parseWorkspace}, memoized. */
  url: WorkspaceUrl;
  /** Move it. See {@link applyWorkspacePatch}. */
  set: (patch: WorkspaceUrlPatch) => void;
}

/**
 * Bind the grammar to a host's `useSearchParams`.
 *
 * The page passes the router's pair for `/agents/:slug`; the pane passes the
 * router's pair for `/`. Neither one knows which it is, which is the whole
 * reason `AgentWorkspaceBody` can be hosted by both.
 */
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
