/**
 * How many config-map requests the Keys tab actually puts on the wire (PERF-349).
 *
 * `/settings/connectors/{name}/config-map` is the one settings route with no
 * cache behind it (condor/web/routes/settings.py:659) — every call is a fresh
 * Condor→Hummingbot round trip. The exchange grid used to prefetch one for
 * *every* connector the moment the list resolved, so picking "spot" fired
 * 30-40 uncached requests, all but one of which nobody would ever read, and
 * the browser's 6-per-origin limit queued the one that mattered behind them.
 *
 * These tests count requests rather than inspecting hooks, because the whole
 * defect lived in the traffic: a prefetch that never resolves and a prefetch
 * that is never issued look identical from inside the component. `fetch` is
 * therefore the only stub — the real `api` layer builds the URLs, so the
 * assertions are about literal paths.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import { ApiKeysSettings } from "./ApiKeysSettings";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let requested: string[] = [];

/** Enough connectors that a per-connector prefetch is unmistakable in the count. */
const SPOT = ["binance", "kucoin", "okx", "gate_io", "kraken", "mexc"];

const CONFIG_MAP = {
  config_map: {
    api_key: { type: "str", required: true, prompt: "Enter your API key" },
    api_secret: { type: "str", required: true, prompt: "Enter your API secret" },
  },
};

function respond(path: string): unknown {
  if (path.endsWith("/api/v1/servers")) {
    return [{ name: "prod", host: "h", port: 8000, online: true, permission: "owner" }];
  }
  if (path.includes("/settings/credentials")) return { credentials: [] };
  if (path.includes("/config-map")) return CONFIG_MAP;
  if (path.includes("/settings/connectors")) {
    return { connectors: SPOT.map((name) => ({ name, type: "spot" })) };
  }
  if (path.includes("/gateway/wallets")) return { wallets: [] };
  if (path.includes("/gateway/status")) return { running: false };
  return {};
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  requested = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string) => {
      requested.push(path);
      return new Response(JSON.stringify(respond(path)), {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

/** Every config-map request issued so far, as the connector names they name. */
function configMapCalls(): string[] {
  return requested
    .filter((u) => u.includes("/config-map"))
    .map((u) => u.match(/connectors\/([^/]+)\/config-map/)![1]);
}

async function flush() {
  for (let i = 0; i < 25; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** The type cards carry a description under their label, so match on the prefix. */
function button(label: string): HTMLButtonElement {
  const all = [...container.querySelectorAll("button")];
  const found =
    all.find((b) => b.textContent?.trim() === label) ??
    all.find((b) => b.textContent?.trim().startsWith(label));
  if (!found) {
    const have = all.map((b) => b.textContent?.trim()).join(" | ");
    throw new Error(`no button "${label}" — have: ${have}`);
  }
  return found as HTMLButtonElement;
}

/** Mount the tab and walk it to the spot exchange grid. */
async function openSpotGrid() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider value={{ server: "prod", setServer: () => {} }}>
          <ApiKeysSettings />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  await flush();
  await act(async () => button("Add API Key").click());
  await act(async () => button("spot").click());
  await flush();
}

describe("ApiKeysSettings config-map traffic", () => {
  it("loads the spot grid without fetching a single config map", async () => {
    await openSpotGrid();

    // The list itself is fetched exactly once...
    expect(requested.filter((u) => u.includes("&type=spot"))).toHaveLength(1);
    // ...and nothing is speculatively pulled for the six connectors in it.
    expect(configMapCalls()).toEqual([]);
  });

  it("warms exactly the hovered connector, and only once", async () => {
    await openSpotGrid();

    await act(async () => {
      button("kucoin").dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    await flush();

    expect(configMapCalls()).toEqual(["kucoin"]);

    // A second hover inside the 30-minute staleTime is served from cache.
    await act(async () => {
      button("kucoin").dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    await flush();

    expect(configMapCalls()).toEqual(["kucoin"]);
  });

  it("renders the hovered connector's fields with no spinner on click", async () => {
    await openSpotGrid();

    await act(async () => {
      button("okx").dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    });
    await flush();
    await act(async () => button("okx").click());

    // No flush between the click and the assertion: the fields are there on the
    // very first render because the hover already filled the cache.
    expect(container.textContent).toContain("api_key");
    expect(container.textContent).not.toContain("Loading fields...");
    expect(configMapCalls()).toEqual(["okx"]);
  });

  it("still loads fields for a connector clicked without ever being hovered", async () => {
    await openSpotGrid();

    await act(async () => button("kraken").click());
    expect(container.textContent).toContain("Loading fields...");

    await flush();

    expect(container.textContent).toContain("api_secret");
    expect(configMapCalls()).toEqual(["kraken"]);
  });
});
