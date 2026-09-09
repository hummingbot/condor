/**
 * Guards the credential-mutation invalidation set through the real component
 * (CORR-353), not just the shared helper in isolation.
 *
 * `addMut` and `deleteMut`'s `onSuccess` used to invalidate only
 * `["settings-credentials", server]`. This seeds `["venues", server]` and
 * `["connected-exchanges", server]` the way `usePrefetchData` warms them on
 * load, then asserts a successful `addCredential` / `deleteCredential`
 * reaches all three:
 *
 * - `settings-credentials` has an active observer in this harness (the tab's
 *   own `useQuery`), so `invalidateQueries` refetches it immediately — the
 *   assertion there is an extra GET, the same shape as the existing refetch
 *   tests below.
 * - `venues` and `connected-exchanges` have no mounted observer here (nothing
 *   on the Settings page reads them), so invalidation only marks them stale
 *   for a later fetch — the assertion there is `isInvalidated`, exactly as
 *   the item's Notes describe.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { venuesQueryKey } from "@/components/market/useVenues";
import { ServerContext } from "@/hooks/useServer";
import { ApiKeysSettings } from "./ApiKeysSettings";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SERVER = "prod";
let container: HTMLDivElement;
let root: Root;
let requested: string[] = [];
/** Toggled per test: whether GET credentials reports binance already added. */
let hasBinanceCredential = false;

const CONNECTORS = { connectors: [{ name: "binance", type: "spot" }] };
const CONFIG_MAP = { config_map: { api_key: { type: "str", required: true } } };

function respond(path: string, init?: RequestInit): unknown {
  if (path.endsWith("/api/v1/servers")) {
    return [{ name: SERVER, host: "h", port: 8000, online: true, permission: "owner" }];
  }
  if (path.includes("/settings/credentials") && init?.method === "POST") return { added: true };
  if (path.includes("/settings/credentials") && init?.method === "DELETE") return { deleted: true };
  if (path.includes("/settings/credentials")) {
    return {
      credentials: hasBinanceCredential
        ? [{ connector_name: "binance", connector_type: "spot" }]
        : [],
    };
  }
  if (path.includes("/config-map")) return CONFIG_MAP;
  if (path.includes("/settings/connectors")) return CONNECTORS;
  if (path.includes("/gateway/wallets")) return { wallets: [] };
  if (path.includes("/gateway/status")) return { running: false };
  return {};
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  requested = [];
  hasBinanceCredential = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      requested.push(`${init?.method ?? "GET"} ${path}`);
      return new Response(JSON.stringify(respond(path, init)), {
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

async function flush() {
  for (let i = 0; i < 25; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const credentialGetCalls = () =>
  requested.filter((u) => u.startsWith("GET") && u.includes("/settings/credentials")).length;

const isInvalidated = (qc: QueryClient, key: readonly unknown[]) =>
  qc.getQueryState(key as unknown[])?.isInvalidated === true;

function clickContaining(text: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(text),
  );
  if (!found) throw new Error(`no button containing "${text}"`);
  return found as HTMLButtonElement;
}

function button(label: string): HTMLButtonElement {
  const all = [...container.querySelectorAll("button")];
  const found = all.find(
    (b) => b.getAttribute("aria-label") === label || b.textContent?.trim() === label,
  );
  if (!found) {
    const have = all.map((b) => b.getAttribute("aria-label") ?? b.textContent?.trim()).join(" | ");
    throw new Error(`no button "${label}" — have: ${have}`);
  }
  return found as HTMLButtonElement;
}

/** Mounts the tab and warms `venues`/`connected-exchanges` the way usePrefetchData does. */
async function mountKeysTab() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  for (const key of [venuesQueryKey(SERVER), ["connected-exchanges", SERVER]]) {
    qc.setQueryData(key, []);
  }
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider value={{ server: SERVER, setServer: () => {} }}>
          <ApiKeysSettings />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  await flush();
  return qc;
}

describe("ApiKeysSettings credential mutations invalidate the full set", () => {
  it("reaches settings-credentials, venues and connected-exchanges after addCredential", async () => {
    const qc = await mountKeysTab();
    const callsBeforeAdd = credentialGetCalls();

    await act(async () => button("Add API Key").click());
    await act(async () => clickContaining("spot").click());
    await flush();
    await act(async () => clickContaining("binance").click());
    await flush();
    await act(async () => button("Add Credential").click());
    await flush();

    expect(requested.some((u) => u.startsWith("POST") && u.includes("/settings/credentials"))).toBe(
      true,
    );
    // settings-credentials has an active observer here, so invalidation shows
    // up as a refetch rather than a lingering `isInvalidated` flag.
    expect(credentialGetCalls()).toBeGreaterThan(callsBeforeAdd);
    // venues and connected-exchanges have no observer mounted on this page —
    // invalidation only marks them stale for later.
    expect(isInvalidated(qc, venuesQueryKey(SERVER))).toBe(true);
    expect(isInvalidated(qc, ["connected-exchanges", SERVER])).toBe(true);
  });

  it("reaches settings-credentials, venues and connected-exchanges after deleteCredential", async () => {
    hasBinanceCredential = true;
    const qc = await mountKeysTab();
    const callsBeforeDelete = credentialGetCalls();

    await act(async () => button("Delete credential").click());
    await act(async () => button("Confirm delete").click());
    await flush();

    expect(
      requested.some((u) => u.startsWith("DELETE") && u.includes("/settings/credentials/binance")),
    ).toBe(true);
    expect(credentialGetCalls()).toBeGreaterThan(callsBeforeDelete);
    expect(isInvalidated(qc, venuesQueryKey(SERVER))).toBe(true);
    expect(isInvalidated(qc, ["connected-exchanges", SERVER])).toBe(true);
  });
});
