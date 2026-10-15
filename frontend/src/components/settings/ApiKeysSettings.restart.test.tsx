/**
 * A credential write that needs a restart says so (CORR-732).
 *
 * hummingbot-api answers `/accounts/add-credential` with `restart_required`
 * when the stored key only reaches already-running bots after a restart.
 * Condor returned the upstream body verbatim under `result` and `api.ts` typed
 * it away as `{ added: boolean }`, so the flag arrived and nothing read it: the
 * add flow closed, the key appeared in the list, and the operator had no way to
 * learn the running bots were still on the old one — the credential-side twin
 * of a config write reporting `updated` without reaching a live session.
 *
 * The assertions are on rendered text rather than on the mutation, because the
 * whole defect was that the flag was present and invisible.
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

/** What the next credential write answers with. */
let writeAnswer: Record<string, unknown> = { added: true, restart_required: false };
/** The credentials the list reports, so a delete has something to act on. */
let credentials: { connector_name: string; connector_type: string }[] = [];

const CONFIG_MAP = {
  config_map: {
    api_key: { type: "str", required: true, prompt: "Enter your API key" },
  },
};

function respond(path: string, method: string): unknown {
  if (path.includes("/settings/credentials")) {
    return method === "GET" ? { credentials } : writeAnswer;
  }
  if (path.endsWith("/api/v1/servers")) {
    return [{ name: "prod", host: "h", port: 8000, online: true, permission: "owner" }];
  }
  if (path.includes("/config-map")) return CONFIG_MAP;
  if (path.includes("/settings/connectors")) {
    return { connectors: [{ name: "binance", type: "spot" }] };
  }
  if (path.includes("/gateway/wallets")) return { wallets: [] };
  if (path.includes("/gateway/status")) return { running: false };
  return {};
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  writeAnswer = { added: true, restart_required: false };
  credentials = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (path: string, init?: RequestInit) => {
      return new Response(JSON.stringify(respond(path, init?.method ?? "GET")), {
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

/** The icon-only controls (InlineConfirm's trash/confirm) carry no text. */
function iconButton(label: string): HTMLButtonElement {
  const found = container.querySelector(`button[aria-label="${label}"]`);
  if (!found) throw new Error(`no button labelled "${label}"`);
  return found as HTMLButtonElement;
}

async function flush() {
  for (let i = 0; i < 25; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function mount() {
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
}

/** Walk the add flow to the end and save one credential. */
async function addBinance() {
  await act(async () => button("Add API Key").click());
  await act(async () => button("spot").click());
  await flush();
  await act(async () => button("binance").click());
  await flush();
  await act(async () => button("Add Credential").click());
  await flush();
}

describe("a credential write that needs a restart", () => {
  it("tells the operator the running bots are still on the old key", async () => {
    writeAnswer = { added: true, restart_required: true };
    await mount();

    await addBinance();

    expect(container.textContent).toContain("Saved binance.");
    expect(container.textContent).toContain("Restart the running bots to apply it");
  });

  it("says nothing when the server does not ask for one", async () => {
    writeAnswer = { added: true, restart_required: false };
    await mount();

    await addBinance();

    // Back on the list, with no banner invented for a write that fully applied.
    expect(container.textContent).toContain("Add API Key");
    expect(container.textContent).not.toContain("Restart the running bots");
  });

  it("reports it on a delete too, naming the connector removed", async () => {
    credentials = [{ connector_name: "binance", connector_type: "spot" }];
    writeAnswer = { deleted: true, restart_required: true };
    await mount();

    await act(async () => iconButton("Delete credential").click());
    await flush();
    await act(async () => iconButton("Confirm delete").click());
    await flush();

    expect(container.textContent).toContain("Deleted binance.");
    expect(container.textContent).toContain("Restart the running bots to apply it");
  });
});
