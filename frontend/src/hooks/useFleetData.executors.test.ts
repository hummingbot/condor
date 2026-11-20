/**
 * @vitest-environment jsdom
 *
 * The executor walk does not poll itself (FD-04).
 *
 * An infinite query's refetch replays every loaded page, so the old 60 s
 * `refetchInterval` cost four sequential 500-row requests a minute to refresh
 * rows the socket had already upserted. What is left on a timer is one request
 * for the newest page, folded in by id.
 */

import { InfiniteQueryObserver, QueryObserver } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api } from "@/lib/api";
import { queryClient } from "@/lib/queryClient";
import { executorsHeadQuery, executorsInfiniteQuery } from "./useFleetData";

const SERVER = "prod";

type Page = Awaited<ReturnType<typeof api.getExecutorsPage>>;
const row = (id: string, status = "running") => ({ id, status }) as Page["executors"][number];

let getPage: ReturnType<typeof vi.spyOn>;
let cleanups: (() => void)[] = [];

beforeEach(() => {
  vi.useFakeTimers();
  queryClient.clear();
  getPage = vi.spyOn(api, "getExecutorsPage");
});

afterEach(() => {
  for (const off of cleanups) off();
  cleanups = [];
  queryClient.clear();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

const ids = () =>
  queryClient
    .getQueryData<{ pages: Page[] }>(["executors-infinite", SERVER])
    ?.pages.map((p) => p.executors.map((e) => `${e.id}:${e.status}`));

describe("executors walk", () => {
  it("is not refetched on a timer", async () => {
    getPage.mockImplementation(async (_server: string, params: { cursor?: string } = {}) =>
      params.cursor
        ? { executors: [row("b")], next_cursor: null }
        : { executors: [row("a")], next_cursor: "c1" },
    );
    const observer = new InfiniteQueryObserver(queryClient, executorsInfiniteQuery(SERVER));
    cleanups.push(observer.subscribe(() => {}));
    await vi.advanceTimersByTimeAsync(0);
    await observer.fetchNextPage();
    expect(getPage).toHaveBeenCalledTimes(2);

    await vi.advanceTimersByTimeAsync(10 * 60_000);
    expect(getPage).toHaveBeenCalledTimes(2);
  });

  it("is repaired by a one-page head poll that folds in by id", async () => {
    getPage.mockImplementation(async (_server: string, params: { cursor?: string } = {}) =>
      params.cursor
        ? { executors: [row("b")], next_cursor: null }
        : { executors: [row("a")], next_cursor: "c1" },
    );
    const walk = new InfiniteQueryObserver(queryClient, executorsInfiniteQuery(SERVER));
    cleanups.push(walk.subscribe(() => {}));
    await vi.advanceTimersByTimeAsync(0);
    await walk.fetchNextPage();

    const head = new QueryObserver(queryClient, executorsHeadQuery(SERVER));
    cleanups.push(head.subscribe(() => {}));
    await vi.advanceTimersByTimeAsync(0);
    // Mounting it must not repeat the page-0 request the walk just made.
    expect(getPage).toHaveBeenCalledTimes(2);

    // `a` terminated and a new executor appeared, and no frame said so.
    getPage.mockImplementation(async () => ({
      executors: [row("n"), row("a", "terminated")],
      next_cursor: "c1",
    }));
    await vi.advanceTimersByTimeAsync(60_000);

    expect(getPage).toHaveBeenCalledTimes(3);
    expect(getPage).toHaveBeenLastCalledWith(SERVER, { limit: 500 });
    // Upserted in place; the page past the head is untouched, not refetched.
    expect(ids()).toEqual([["n:running", "a:terminated"], ["b:running"]]);
  });
});
