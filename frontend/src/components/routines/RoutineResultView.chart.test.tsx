/**
 * The routine chart's own shape, now that the fetching moved to the hook
 * (ARCH-319).
 *
 * What the chart owns is its 2:1 box: a rendered matplotlib image takes long
 * enough that the raw-output block below it would visibly jump if the box were
 * not reserved first. And a chart that cannot be read collapses — a routine
 * result is mostly text, and a broken-image glyph in the middle of it says
 * nothing a reader can act on.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RoutineInstance } from "@/lib/api";
import { RoutineResultView } from "./RoutineResultView";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const authFetch = vi.fn<(url: string) => Promise<Response>>();
vi.mock("@/lib/auth-token", () => ({
  authFetch: (url: string) => authFetch(url),
}));

let container: HTMLDivElement;
let root: Root;

const instance = {
  instance_id: "abc",
  routine_name: "pnl",
  config: {},
  status: "done",
  source: "web",
  created_at: 0,
  last_run_at: 1,
  last_result: "ok",
  last_duration: 1,
  run_count: 1,
  has_result: true,
  has_chart: true,
  result_text: "all good",
} satisfies RoutineInstance;

/** Only `ok` and `blob()` are ever read; jsdom's Blob and the platform
 *  `Response` do not interoperate. */
const refused = { ok: false, status: 404 } as unknown as Response;

const skeleton = () => container.querySelector(".animate-pulse");
const image = () => container.querySelector("img");

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  authFetch.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  container.remove();
});

describe("RoutineResultView's chart", () => {
  it("reserves a 2:1 box while the chart is being fetched", async () => {
    authFetch.mockReturnValueOnce(new Promise<Response>(() => {}));
    await act(async () => root.render(<RoutineResultView instance={instance} />));

    expect(skeleton()?.className).toContain("aspect-[2/1]");
    expect(image()).toBeNull();
    // The fetch is the hook's, off the one bearer-guarded route.
    expect(authFetch).toHaveBeenCalledWith("/api/v1/routines/instances/abc/image");
  });

  it("collapses to nothing when the chart cannot be read", async () => {
    authFetch.mockResolvedValueOnce(refused);
    await act(async () => root.render(<RoutineResultView instance={instance} />));
    await act(async () => {});

    expect(skeleton()).toBeNull();
    expect(image()).toBeNull();
    // ...and the rest of the result is still there.
    expect(container.textContent).toContain("Raw Output");
  });
});
