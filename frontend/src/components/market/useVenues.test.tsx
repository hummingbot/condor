/**
 * `useVenues` is the one declaration of the `["venues", server]` query
 * (ARCH-355) — CreateExecutor (Trade) and DexPool both call it instead of each
 * hand-copying `queryKey`/`queryFn`/`staleTime`. The point of extracting it is
 * that two callers on the same server share one cached request rather than
 * two independent ones that could drift apart; this proves that cache sharing
 * directly, standing in for the two pages with two hook consumers under one
 * `QueryClient`.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { VenueTraits } from "@/lib/connector-capabilities";
import { useVenues } from "./useVenues";

const VENUES: VenueTraits[] = [
  { name: "binance", hummingbotMarketData: true, clmmLp: false, credentialed: true },
];

const getVenues = vi.fn(async (server: string) => {
  void server;
  return VENUES;
});

vi.mock("@/lib/api", () => ({
  api: {
    getVenues: (server: string) => getVenues(server),
  },
}));

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

/** Stands in for a page that reads venues, e.g. CreateExecutor or DexPool. */
function Consumer({ label }: { label: string }) {
  const { venues, isPending } = useVenues("srv-1");
  return (
    <div data-consumer={label}>
      {isPending ? "pending" : venues.map((v) => v.name).join(",")}
    </div>
  );
}

async function renderBothPages() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Consumer label="trade" />
        <Consumer label="dex-pool" />
      </QueryClientProvider>,
    );
  });
  // react-query resolves on a later macrotask than the render that asked.
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.clearAllMocks();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("useVenues shares one cached request across callers", () => {
  it("fetches venues once for two consumers on the same server", async () => {
    await renderBothPages();

    const trade = container.querySelector('[data-consumer="trade"]');
    const dexPool = container.querySelector('[data-consumer="dex-pool"]');
    expect(trade?.textContent).toBe("binance");
    expect(dexPool?.textContent).toBe("binance");

    // The cache-hit assertion: two mounted consumers, one network request.
    expect(getVenues).toHaveBeenCalledTimes(1);
    expect(getVenues).toHaveBeenCalledWith("srv-1");
  });
});
