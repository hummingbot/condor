/**
 * CORR-812: an install that downloaded a hub listing has no way to learn the
 * hub later pulled it or changed its badge. This component's whole job is to
 * say nothing for an agent never installed from the hub, say nothing while a
 * confirmed check still reads ordinary, and tell the two warning states
 * (delisted, badge changed) apart from "could not reach Botcamp right now" —
 * the last one must never read as a moderation claim.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { HubAgentStatus } from "@/lib/api";
import { HubInstallStatus } from "./AgentsSettings";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let requests: { url: string; method: string }[] = [];
let status: HubAgentStatus;

function json(payload: unknown, httpStatus = 200) {
  return new Response(JSON.stringify(payload), {
    status: httpStatus,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  requests = [];
  status = { tracked: false };

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      requests.push({ url, method });
      if (url.endsWith("/hub-status")) return json(status);
      throw new Error(`unexpected request: ${method} ${url}`);
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

async function render() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <HubInstallStatus slug="river_scalper" />
      </QueryClientProvider>,
    );
  });
  await waitFor(() => requests.some((r) => r.url.endsWith("/hub-status")));
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function waitFor(done: () => boolean) {
  for (let i = 0; i < 50 && !done(); i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  if (!done()) throw new Error("condition never became true");
}

it("shows nothing for an agent never installed from the hub", async () => {
  status = { tracked: false };
  await render();

  expect(container.querySelector("[data-hub-status]")).toBeNull();
});

it("shows nothing once a confirmed check still reads ordinary", async () => {
  status = {
    tracked: true,
    listing_id: "lst_1",
    badge: "validated",
    status: "ok",
    checked_at: "2026-10-21T12:00:00Z",
    live_check_failed: false,
  };
  await render();

  expect(container.querySelector("[data-hub-status]")).toBeNull();
});

it("warns when the hub confirmed the listing was delisted", async () => {
  status = {
    tracked: true,
    listing_id: "lst_1",
    badge: "validated",
    status: "delisted",
    checked_at: "2026-10-21T12:00:00Z",
    live_check_failed: false,
  };
  await render();

  const shown = container.querySelector("[data-hub-status='delisted']");
  expect(shown).not.toBeNull();
  expect(shown?.textContent).toContain("withdrew this listing");
  expect(shown?.textContent).not.toContain("deleted");
});

it("warns when the badge changed since install, and names the new one", async () => {
  status = {
    tracked: true,
    listing_id: "lst_1",
    badge: "community",
    status: "badge_changed",
    checked_at: "2026-10-21T12:00:00Z",
    live_check_failed: false,
  };
  await render();

  const shown = container.querySelector("[data-hub-status='badge_changed']");
  expect(shown?.textContent).toContain("community");
});

it("reports an unreachable hub without claiming moderation occurred", async () => {
  status = {
    tracked: true,
    listing_id: "lst_1",
    badge: "validated",
    status: "ok",
    checked_at: "2026-10-18T09:00:00Z",
    live_check_failed: true,
  };
  await render();

  const shown = container.querySelector("[data-hub-status='unknown']");
  expect(shown?.textContent).toContain("Could not reach Botcamp");
  expect(shown?.textContent).not.toContain("delisted");
  expect(shown?.textContent).not.toContain("withdrew");
});
