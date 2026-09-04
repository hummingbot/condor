/**
 * Snapshot bodies are fetched once per tick and shared (PERF-222).
 *
 * A session's chart markers used to be built by one batched query whose key
 * ended in the joined tick list: a session that gained a tick refetched every
 * body, nothing rendered until the slowest landed, and clicking a tick fetched
 * the very same body a second time under `SnapshotDetail`'s own key. These cases
 * pin the three properties that replaced it — per-tick caching, incremental
 * fill, and one key shared with the detail view — plus the ordering guarantee
 * the chart depends on.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act, createElement, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { SnapshotSummary } from "@/lib/api";

/** Resolvers for the in-flight `getSnapshot` calls, keyed by tick. */
const pending = new Map<number, (content: string) => void>();
const getSnapshot = vi.fn((_slug: string, _sslug: string, _n: number, tick: number) =>
  new Promise<{ content: string; tick: number }>((resolve) => {
    pending.set(tick, (content) => resolve({ content, tick }));
  }),
);

vi.mock("@/lib/api", () => ({ api: { getSnapshot: (...a: [string, string, number, number]) => getSnapshot(...a) } }));

const { snapshotQueryOptions, useSnapshotBubbles } = await import("./useSnapshotBubbles");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const body = (tick: number, calls: number) =>
  [
    `# Snapshot #${tick} — 2026-08-26 10:0${tick}:00`,
    "",
    "## Agent Response",
    `decision for tick ${tick}`,
    "",
    `## Tool Calls (${calls})`,
    "",
    ...Array.from({ length: calls }, (_, i) => `### ${i + 1}. tool_${i} (ok)\n\n**Output:**\n\`\`\`\nok\n\`\`\`\n`),
  ].join("\n");

const summary = (tick: number): SnapshotSummary => ({
  tick,
  timestamp: `2026-08-26 10:0${tick}:00`,
  file: `${tick}.md`,
});

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

const bubbles: { current: ReturnType<typeof useSnapshotBubbles> } = { current: [] };
/** The tick `SnapshotDetail` rendered without a spinner, or null while loading. */
const detail: { current: number | null } = { current: null };

function Bubbles({ summaries }: { summaries: SnapshotSummary[] }) {
  const result = useSnapshotBubbles("agent", "strat", 1, summaries);
  useEffect(() => {
    bubbles.current = result;
  });
  return null;
}

/** The production `SnapshotDetail` read of a body, reduced to its query. */
function Detail({ tick }: { tick: number }) {
  const { data, isLoading } = useQuery({ ...snapshotQueryOptions("agent", "strat", 1, tick), enabled: tick > 0 });
  useEffect(() => {
    detail.current = isLoading ? null : (data?.tick ?? null);
  });
  return null;
}

function Harness({ summaries, detailTick }: { summaries: SnapshotSummary[]; detailTick?: number }) {
  return createElement(
    QueryClientProvider,
    { client },
    createElement(Bubbles, { summaries }),
    detailTick ? createElement(Detail, { tick: detailTick }) : null,
  );
}

const render = (summaries: SnapshotSummary[], detailTick?: number) =>
  act(() => {
    root.render(createElement(Harness, { summaries, detailTick }));
  });

/**
 * Resolve one in-flight body and let react-query flush it into the bubbles.
 *
 * Polls rather than awaiting a fixed number of ticks: the resolution travels
 * through the query's promise, react-query's batched notify and a React commit,
 * and how many turns of the loop that takes is not ours to predict.
 */
async function land(tick: number, calls = 1) {
  const resolve = pending.get(tick);
  if (!resolve) throw new Error(`tick ${tick} was never requested`);
  pending.delete(tick);
  resolve(body(tick, calls));
  for (let i = 0; i < 50; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
    if (bubbles.current.some((b) => b.tick === tick && b.agentResponse !== undefined)) return;
  }
  throw new Error(`tick ${tick} never reached the bubbles`);
}

const ticksRequested = () => getSnapshot.mock.calls.map((c) => c[3]);

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  getSnapshot.mockClear();
  pending.clear();
  bubbles.current = [];
  detail.current = null;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  client.clear();
});

describe("useSnapshotBubbles", () => {
  it("issues one request per tick and fills each marker as its own body lands", async () => {
    render([summary(1), summary(2), summary(3)]);
    expect(ticksRequested().sort()).toEqual([1, 2, 3]);

    // A marker exists for every known tick from the first render — the chart is
    // never empty while bodies are in flight.
    expect(bubbles.current.map((b) => b.tick)).toEqual([1, 2, 3]);
    expect(bubbles.current.every((b) => b.agentResponse === undefined)).toBe(true);

    // The second body landing fills only its own marker: no all-or-nothing wait.
    await land(2, 3);
    expect(bubbles.current[1].agentResponse).toContain("decision for tick 2");
    expect(bubbles.current[1].toolCallCount).toBe(3);
    expect(bubbles.current[0].agentResponse).toBeUndefined();
    expect(bubbles.current[2].agentResponse).toBeUndefined();
  });

  it("keeps markers in the summaries' order as bodies land out of order", async () => {
    render([summary(1), summary(2), summary(3)]);
    await land(3);
    await land(1);
    expect(bubbles.current.map((b) => b.tick)).toEqual([1, 2, 3]);
    expect(bubbles.current[0].agentResponse).toContain("tick 1");
    expect(bubbles.current[2].agentResponse).toContain("tick 3");
  });

  it("fetches only the new body when the session gains a tick", async () => {
    render([summary(1), summary(2)]);
    await land(1);
    await land(2);
    getSnapshot.mockClear();

    // A tick added while the view is open: the two already fetched stay cached
    // and keep their previews, and only the newcomer costs a request.
    render([summary(1), summary(2), summary(3)]);
    expect(ticksRequested()).toEqual([3]);
    expect(bubbles.current.map((b) => b.tick)).toEqual([1, 2, 3]);
    expect(bubbles.current[0].agentResponse).toContain("tick 1");
    expect(bubbles.current[1].agentResponse).toContain("tick 2");

    await land(3);
    expect(bubbles.current[2].agentResponse).toContain("tick 3");
  });

  it("serves a clicked tick from the previews' cache — N requests, not N+1", async () => {
    render([summary(1), summary(2)]);
    await land(1);
    await land(2);
    expect(ticksRequested().sort()).toEqual([1, 2]);
    getSnapshot.mockClear();

    render([summary(1), summary(2)], 2);
    expect(getSnapshot).not.toHaveBeenCalled();
    // Rendered straight from cache: no spinner, the body is already there.
    expect(detail.current).toBe(2);
  });
});
