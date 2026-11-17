/**
 * The no-server card says what to do, for the situation the user is in (FU-09).
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, type ServerInfo } from "@/lib/api";
import { NoServerCard } from "./NoServerCard";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

async function mount(servers: ServerInfo[], message?: string) {
  vi.spyOn(api, "getServers").mockResolvedValue(servers);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <NoServerCard message={message} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await act(async () => {
    for (let i = 0; i < 3; i++) await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.restoreAllMocks();
});

const SERVER = { name: "prod", host: "h", port: 8000, online: true } as ServerInfo;

describe("NoServerCard", () => {
  it("points at the top bar, not a sidebar that does not exist", async () => {
    await mount([SERVER], "Select a server from the sidebar to view routines.");
    expect(container.textContent).toContain(
      "Select a server from the menu in the top bar to view routines.",
    );
    expect(container.textContent).not.toContain("sidebar");
  });

  it("says to add a server when there is none to select", async () => {
    await mount([], "Select a server from the sidebar to view routines.");
    expect(container.textContent).toContain("No Server Yet");
    expect(container.textContent).toContain("Add a Hummingbot API server");
    expect(container.textContent).not.toContain("Select a server");
  });
});
