/**
 * The three answers the permission hook may give, and what each one licenses
 * (SEC-230).
 *
 * Every owner-gated control in the dashboard — the gateway container buttons,
 * credential add/delete, and now DexPool's token "Replace" — decides whether to
 * offer itself from this one boolean, so the interesting cases are not "does it
 * read the field" but the two edges: a *trader* must be refused, and an
 * *unresolved* server list must not be. The client's job is to predict the
 * backend's 403, never to invent one; if the servers query has not answered yet
 * the hook has to degrade to the pre-fix behaviour and let the backend rule.
 *
 * Only `api.getServers` is stubbed — the hook's own cache wiring (it shares the
 * `["servers"]` query with the selector) is the part under test.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ServerInfo } from "@/lib/api";

const getServers = vi.fn();

vi.mock("@/lib/api", () => ({ api: { getServers: () => getServers() } }));
vi.mock("@/hooks/useServer", () => ({ useServer: () => ({ server: "shared" }) }));

const { OWNER_ONLY_HINT, useServerPermission } = await import("./useServerPermission");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const server = (permission: string): ServerInfo =>
  ({ name: "shared", permission }) as ServerInfo;

const holder: { current: ReturnType<typeof useServerPermission> | null } = {
  current: null,
};

function Harness() {
  const state = useServerPermission();
  useEffect(() => {
    holder.current = state;
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;

/**
 * Renders the harness and gives the servers query a bounded chance to settle.
 * `until` is what the caller is waiting for; the unresolved case passes a
 * predicate that is never true and simply spends the budget, which is the
 * point of that case.
 */
async function mount(until: (s: ReturnType<typeof useServerPermission>) => boolean) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Harness />
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 20 && !(holder.current && until(holder.current)); i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 5));
    });
  }
  return holder.current!;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  holder.current = null;
  getServers.mockReset();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("useServerPermission", () => {
  it("refuses owner-only controls to a trader on a shared server", async () => {
    getServers.mockResolvedValue([server("trader")]);

    const state = await mount((s) => s.permission !== null);

    expect(state.permission).toBe("trader");
    expect(state.isOwner).toBe(false);
  });

  it("offers them to the owner", async () => {
    getServers.mockResolvedValue([server("owner")]);

    const state = await mount((s) => s.permission !== null);

    expect(state.permission).toBe("owner");
    expect(state.isOwner).toBe(true);
  });

  it("stays out of the way while the server list is unresolved", async () => {
    // Never settles: the first paint, and any failed load, must not manufacture
    // a refusal the backend did not give.
    getServers.mockReturnValue(new Promise(() => {}));

    const state = await mount(() => false);

    expect(state.permission).toBeNull();
    expect(state.isOwner).toBe(true);
  });

  it("explains itself in terms of the access the user actually has", () => {
    expect(OWNER_ONLY_HINT).toContain("owner");
    expect(OWNER_ONLY_HINT).toContain("trader");
  });
});
