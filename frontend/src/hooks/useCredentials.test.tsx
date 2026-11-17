/**
 * "No keys" versus "could not ask" (FU-02).
 *
 * The credentials route raises when the Hummingbot API is unreachable. The
 * hook used to fold that into an empty list, so `AppShell` covered the
 * portfolio, trade, DEX and bots pages with "Connect an Exchange or Wallet"
 * for a user who has keys. `isError` is what lets the shell tell them apart.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import { api } from "@/lib/api";
import { credentialsQuery } from "@/lib/queryClient";
import { useCredentials } from "./useCredentials";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let seen: ReturnType<typeof useCredentials>;

function Probe() {
  seen = useCredentials();
  return null;
}

async function mount() {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ServerContext.Provider value={{ server: "prod", setServer: () => {} }}>
          <Probe />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    // react-query hands results to observers on a macrotask.
    for (let i = 0; i < 3; i++) await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  vi.spyOn(api, "getGatewayWallets").mockRejectedValue(new Error("Gateway not running"));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("useCredentials", () => {
  it("reports an error, not an empty account, when the credentials request fails", async () => {
    vi.spyOn(api, "getCredentials").mockRejectedValue(
      Object.assign(new Error("Failed to fetch credentials"), { status: 502 }),
    );
    await mount();

    expect(seen.isLoading).toBe(false);
    expect(seen.isError).toBe(true);
    expect(seen.hasKeys).toBe(false);
  });

  it("reports no keys, and no error, when the server answered with none", async () => {
    vi.spyOn(api, "getCredentials").mockResolvedValue({ credentials: [] } as never);
    await mount();

    expect(seen.isLoading).toBe(false);
    expect(seen.isError).toBe(false);
    expect(seen.hasKeys).toBe(false);
  });

  it("keeps the last known keys when a refetch fails", async () => {
    client.setQueryData(credentialsQuery("prod").queryKey, {
      credentials: [{ connector: "binance" }],
    });
    vi.spyOn(api, "getCredentials").mockRejectedValue(new Error("boom"));
    await mount();
    await act(async () => {
      await client.refetchQueries({ queryKey: credentialsQuery("prod").queryKey });
    });

    expect(seen.hasKeys).toBe(true);
    expect(seen.isError).toBe(false);
  });
});
