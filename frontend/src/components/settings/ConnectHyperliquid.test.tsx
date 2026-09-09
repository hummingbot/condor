/**
 * CORR-354: ConnectHyperliquid used to invalidate the credential-derived caches
 * only from the parent's `onDone`, which only fires when the user clicks
 * "Link code" or "Skip" on the post-save referral prompt. A user who saves
 * credentials and then navigates away (the common case — the account usually
 * has no referrer yet, so the prompt shows) left the Keys list, venues and
 * connected-exchanges stale.
 *
 * This exercises the connect flow directly (not through ApiKeysSettings) and
 * asserts the shared `invalidateCredentialQueries` helper runs right after the
 * saves settle — before the referral prompt is dismissed, and even when it
 * never is.
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

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SERVER = "prod";

const addCredential = vi.fn<(server: string, data: unknown) => Promise<{ added: boolean }>>();
vi.mock("@/lib/api", () => ({
  api: { addCredential: (server: string, data: unknown) => addCredential(server, data) },
}));

const discoverWallets = vi.fn();
const connectWallet = vi.fn();
vi.mock("@/lib/wallet/evm", async () => {
  const actual = await vi.importActual<typeof import("@/lib/wallet/evm")>("@/lib/wallet/evm");
  return {
    ...actual,
    discoverWallets: () => discoverWallets(),
    connectWallet: (...a: unknown[]) => connectWallet(...a),
  };
});

const connectHyperliquid = vi.fn();
const hasHyperliquidReferrer = vi.fn();
vi.mock("@/lib/wallet/hyperliquid", async () => {
  const actual =
    await vi.importActual<typeof import("@/lib/wallet/hyperliquid")>("@/lib/wallet/hyperliquid");
  return {
    ...actual,
    connectHyperliquid: (...a: unknown[]) => connectHyperliquid(...a),
    hasHyperliquidReferrer: (...a: unknown[]) => hasHyperliquidReferrer(...a),
  };
});

const { ConnectHyperliquid } = await import("./ConnectHyperliquid");

const CONNECTION = {
  mainAddress: "0xmain",
  agentAddress: "0xagent",
  agentPrivateKey: "0xkey",
  validUntil: Date.now() + 1000,
};

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  addCredential.mockReset();
  discoverWallets.mockReset().mockResolvedValue([
    { uuid: "w1", name: "Rabby", provider: {} },
  ]);
  connectWallet.mockReset().mockResolvedValue("0xmain");
  connectHyperliquid.mockReset().mockResolvedValue(CONNECTION);
  // No referrer yet — the "available" referral card renders and stays until
  // the user clicks through it (or never does, which is exactly this bug).
  hasHyperliquidReferrer.mockReset().mockResolvedValue(false);
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

const isInvalidated = (qc: QueryClient, key: readonly unknown[]) =>
  qc.getQueryState(key as unknown[])?.isInvalidated === true;

async function mountAndConnect(qc: QueryClient) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ConnectHyperliquid server={SERVER} onBack={() => {}} onDone={() => {}} />
      </QueryClientProvider>,
    );
  });
  await flush();
  const button = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("Rabby"),
  );
  if (!button) throw new Error("wallet button not found");
  await act(async () => button.click());
  await flush();
}

function newClient() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  for (const key of [venuesQueryKey(SERVER), ["connected-exchanges", SERVER]]) {
    qc.setQueryData(key, []);
  }
  return qc;
}

describe("ConnectHyperliquid invalidates at the save, not at the referral dismissal", () => {
  it("invalidates once both connectors save, before the referral prompt is touched", async () => {
    addCredential.mockResolvedValue({ added: true });
    const qc = newClient();

    await mountAndConnect(qc);

    expect(addCredential).toHaveBeenCalledTimes(2);
    expect(isInvalidated(qc, venuesQueryKey(SERVER))).toBe(true);
    expect(isInvalidated(qc, ["connected-exchanges", SERVER])).toBe(true);
    // The referral card is up (no click on "Link code" / "Skip" happened) —
    // invalidation ran regardless.
    expect(container.textContent).toContain("Link code");
  });

  it("still invalidates when only one of the two connectors saves", async () => {
    addCredential.mockImplementation(async (_server, data: unknown) => {
      const { connector_name } = data as { connector_name: string };
      if (connector_name === "hyperliquid_perpetual") throw new Error("boom");
      return { added: true };
    });
    const qc = newClient();

    await mountAndConnect(qc);

    expect(isInvalidated(qc, venuesQueryKey(SERVER))).toBe(true);
    expect(isInvalidated(qc, ["connected-exchanges", SERVER])).toBe(true);
  });

  it("does not invalidate when both connectors fail to save", async () => {
    addCredential.mockRejectedValue(new Error("boom"));
    const qc = newClient();

    await mountAndConnect(qc);

    expect(isInvalidated(qc, venuesQueryKey(SERVER))).toBe(false);
    expect(isInvalidated(qc, ["connected-exchanges", SERVER])).toBe(false);
  });
});
