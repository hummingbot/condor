/**
 * ARCH-311: which executor rows carry a USD rate is a fact of the type.
 *
 * The backend has two executor wire models (condor/web/models.py): the live
 * `ExecutorInfo`, served by `/executors`, `/executors/page` and the WS frames,
 * which has no `usd_rate` at all; and `NormalizedExecutor`, served only by the
 * archived routes, which carries `usd_rate: float = 1.0`. The frontend briefly
 * collapsed the two, declaring `usd_rate?: number` on the shared
 * `ExecutorInfo` — a field every live consumer would read as `undefined`. The
 * live surfaces that hedged with `?? 1` would then have rendered a BRL or EUR
 * figure behind a `$` with no type error, which is precisely the drift the
 * "mirror the backend models" convention exists to prevent.
 *
 * The assertions below are compile-time, not runtime: `tsc -b` (which `npm run
 * build` runs over `src`, tests included) fails if a `@ts-expect-error` stops
 * being an error. So this file fails the build the moment the split is undone
 * in either direction.
 */

import { describe, expect, it } from "vitest";

import type { ArchivedExecutor, ExecutorInfo, PaginatedExecutors } from "./api";

describe("the executor wire types mirror the backend's split", () => {
  it("does not offer usd_rate on a live executor row", () => {
    const live = {} as ExecutorInfo;
    // @ts-expect-error live rows (`/executors`, WS frames) carry no USD rate.
    const rate: number | undefined = live.usd_rate;
    expect(rate).toBeUndefined();
  });

  it("requires usd_rate on an archived executor row", () => {
    // @ts-expect-error the archived rate is required, never optional: the
    // backend defaults it to 1.0, so a row always arrives with a number.
    const missing: ArchivedExecutor = {} as Omit<ArchivedExecutor, "usd_rate">;
    expect(missing.usd_rate).toBeUndefined();
  });

  it("types an archived page as archived rows", () => {
    const page = { executors: [] } as unknown as PaginatedExecutors;
    // Compiles only because the page's rows are `ArchivedExecutor`, which is
    // what `/archived/executors` actually returns.
    const rates: number[] = page.executors.map((ex) => ex.usd_rate);
    expect(rates).toEqual([]);
  });

  it("still accepts an archived row wherever a live row is wanted", () => {
    const archived = { usd_rate: 0.18 } as ArchivedExecutor;
    const asLive: ExecutorInfo = archived;
    expect(asLive).toBe(archived);
  });
});
