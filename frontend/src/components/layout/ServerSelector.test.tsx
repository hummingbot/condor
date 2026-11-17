/**
 * The header's server selection keeps up with the server list (FU-09).
 *
 * Auto-select used to run only when nothing was selected, so a persisted name
 * that had since been deleted, renamed or unshared stayed selected: the header
 * said "No server" while every page kept querying the dead name.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import { api, type ServerInfo } from "@/lib/api";
import { ServerSelector } from "./ServerSelector";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const srv = (name: string, over: Partial<ServerInfo> = {}): ServerInfo => ({
  name,
  host: "h",
  port: 8000,
  online: true,
  permission: "owner",
  is_default: false,
  ...over,
});

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;
let selected: string | null;
let select: (s: string | null) => void;
let getServers: ReturnType<typeof vi.spyOn>;

function Harness({ initial }: { initial: string | null }) {
  const [server, setServer] = useState<string | null>(initial);
  selected = server;
  select = setServer;
  return (
    <ServerContext.Provider value={{ server, setServer }}>
      <ServerSelector />
    </ServerContext.Provider>
  );
}

async function settle() {
  await act(async () => {
    for (let i = 0; i < 3; i++) await new Promise((r) => setTimeout(r, 0));
  });
}

async function mount(initial: string | null) {
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <Harness initial={initial} />
      </QueryClientProvider>,
    );
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getServers = vi.spyOn(api, "getServers");
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

describe("ServerSelector", () => {
  it("selects the default online server when nothing is saved", async () => {
    getServers.mockResolvedValue([srv("a"), srv("b", { is_default: true })]);
    await mount(null);
    expect(selected).toBe("b");
  });

  it("falls back when the saved server no longer exists", async () => {
    getServers.mockResolvedValue([srv("a"), srv("b", { is_default: true })]);
    await mount("deleted-one");
    expect(selected).toBe("b");
  });

  it("clears the selection when the saved server is gone and none is online", async () => {
    getServers.mockResolvedValue([srv("a", { online: false })]);
    await mount("deleted-one");
    expect(selected).toBeNull();
    expect(container.textContent).toContain("No server");
  });

  it("keeps a selected server that is merely offline, and says so", async () => {
    getServers.mockResolvedValue([srv("a", { online: false }), srv("b")]);
    await mount("a");
    expect(selected).toBe("a");
    expect(container.querySelector("button")?.title).toBe("a is offline");
    expect(container.querySelector('[aria-label="Offline"]')).not.toBeNull();
  });

  it("does not throw out a server selected after the cached list was fetched", async () => {
    getServers.mockResolvedValue([srv("a")]);
    await mount("a");

    // Just added elsewhere: the cached list has not heard of it yet.
    await act(async () => select("fresh"));
    await settle();
    expect(selected).toBe("fresh");

    // A list fetched after the selection that still lacks it is the real thing.
    await act(async () => {
      await client.refetchQueries({ queryKey: ["servers"] });
    });
    await settle();
    expect(selected).toBe("a");
  });

  it("leaves the selection alone when the list cannot be loaded", async () => {
    getServers.mockRejectedValue(new Error("down"));
    await mount("a");
    expect(selected).toBe("a");
  });
});
