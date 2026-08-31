/**
 * The admin-only update surface (`/api/v1/updates/*`, FEAT-071).
 *
 * Mirrors `lib/admin-api.ts` and reuses its fetch wrapper: these routes answer
 * 403 to every non-admin seat, and a 403 is not a failure here either — it is
 * how the dashboard learns what it is allowed to show. Hiding the tab is
 * cosmetic; `routes/updates.py` re-reads the role on every request.
 *
 * The types below are the engine's `to_wire()` shapes (`condor/updates/`), not
 * a second model of what an update is. If a field is missing here, it is
 * missing in the engine, and that is where it should be added.
 */

import { adminFetch } from "./admin-api";

/** One answer to "what version is this", for one way of being versioned. */
export interface Facet {
  kind: "repo" | "image";
  current: string;
  available: string | null;
  behind: number;
  up_to_date: boolean;
  /** Commit subjects, for the `<details>` under a behind-count. */
  detail: string[];
  error: string | null;
}

/**
 * Everything one component's card needs.
 *
 * `facets` is keyed by kind: Condor has only `repo`; hummingbot-api has both,
 * and `mode` says which one actually decides its version — an install running
 * the published image is not updated by pulling its repo.
 */
export interface ComponentStatus {
  key: string;
  name: string;
  facets: Partial<Record<"repo" | "image", Facet>>;
  mode: "image" | "source" | null;
  up_to_date: boolean;
}

/** Something that stops the update, with the files behind it and the ways out. */
export interface Block {
  component: string;
  code: string;
  message: string;
  paths: string[];
  /** Which buttons this blocker earns — typically `["stash", "discard"]`. */
  resolutions: string[];
}

/** A consequence worth knowing about. Never a reason to refuse. */
export interface UpdateWarning {
  component: string;
  code: string;
  message: string;
}

export interface Preflight {
  components: string[];
  blocks: Block[];
  warnings: UpdateWarning[];
  /** The ordered plan, as labels. */
  steps: string[];
  ok: boolean;
}

export type StepState = "pending" | "running" | "ok" | "failed" | "skipped";

export interface Step {
  key: string;
  label: string;
  state: StepState;
  started: number | null;
  ended: number | null;
  /** Last few lines of command output — shown on failure. */
  output_tail: string;
}

export type RunState = "running" | "restarting" | "succeeded" | "failed";

export interface Run {
  id: string;
  started: number;
  actor: { user_id: number | null; chat_id: unknown };
  components: string[];
  steps: Step[];
  state: RunState;
  from_commit: string | null;
  target_commit: string | null;
  error: string | null;
  ended: number | null;
  /** Done was pressed on this run. Journaled, so it survives a reload. */
  acknowledged: boolean;
}

/** True while the run has not been judged yet — the poll keeps going. */
export function isLive(run: Run | null | undefined): boolean {
  return run?.state === "running" || run?.state === "restarting";
}

export const UPDATES_STATUS_KEY = ["updates-status"] as const;
export const UPDATES_RUN_KEY = ["updates-run"] as const;

export const updatesApi = {
  /** What each component is running and what is available. 60s server-side cache. */
  getStatus: () =>
    adminFetch<{ components: ComponentStatus[] }>("/api/v1/updates"),

  /** The same, past the cache. */
  check: () =>
    adminFetch<{ components: ComponentStatus[] }>("/api/v1/updates/check", {
      method: "POST",
    }),

  /** Blockers, warnings and the plan, without starting anything. */
  preflight: (components: string[]) =>
    adminFetch<Preflight>("/api/v1/updates/preflight", {
      method: "POST",
      body: JSON.stringify({ components }),
    }),

  /**
   * Clear a blocker with one of the resolutions it offered.
   *
   * No path list by design: the server recomputes what conflicts at press
   * time, because this screen may be minutes old and discarding a path that
   * has since stopped conflicting would destroy unwarned work.
   */
  resolve: (component: string, action: string) =>
    adminFetch<{ ok: boolean; message: string }>("/api/v1/updates/resolve", {
      method: "POST",
      body: JSON.stringify({ component, action }),
    }),

  /**
   * Done: stop showing this finished run.
   *
   * A round trip rather than local state. The panel is a view over the
   * engine's journal, so a dismissal the browser kept came back on the next
   * reload — and on the relaunch the run itself asked for.
   */
  dismiss: (runId: string) =>
    adminFetch<{ run: Run | null }>("/api/v1/updates/dismiss", {
      method: "POST",
      body: JSON.stringify({ run_id: runId }),
    }),

  /** Trigger the update. Answers immediately with the run to watch. */
  start: (components: string[]) =>
    adminFetch<{ run_id: string; state: RunState }>("/api/v1/updates/start", {
      method: "POST",
      body: JSON.stringify({ components }),
    }),

  /** The run in flight, the last one, or `null`. Polled while a run is live. */
  getRun: () => adminFetch<{ run: Run | null }>("/api/v1/updates/run"),
};
