/**
 * Botcamp Hub as one destination (FEAT-153): Pull agents is public, the
 * Botcamp section (connect + upload) is admin-only and gated on being
 * signed in to Botcamp (PR #111's device flow), and withdraw finally has a
 * dashboard control.
 *
 * The thing worth testing is not that the sections render — it is that the
 * admin-only section mounts no admin-only query for a non-admin seat, that an
 * unreachable hub reads differently from an empty one, that Upload yours
 * stays hidden until this install is linked, and that publish and withdraw
 * actually hit the routes the contract defines.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { AgentSummary, HubDeviceState, HubReceipt } from "@/lib/api";
import { AgentHub } from "./AgentHub";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let requests: { url: string; method: string }[] = [];
let isAdmin: boolean;
let submissionReceipt: HubReceipt | null;
let bundleText: string;
let hubDeviceState: HubDeviceState;

const NONE_DEVICE: HubDeviceState = {
  status: "none",
  user_code: "",
  verification_uri: "",
  verification_uri_complete: "",
  expires_at: 0,
  profile_handle: "",
  scope: [],
};

function agent(over: Partial<AgentSummary>): AgentSummary {
  return {
    slug: "x",
    name: "X",
    description: "",
    when_to_consult: "",
    agent_key: "claude_acp",
    strategy_count: 0,
    strategies: [],
    status: "idle",
    session_count: 0,
    experiment_count: 0,
    tick_count: 0,
    latest_session_pnl: 0,
    total_pnl: 0,
    total_volume: 0,
    open_positions: 0,
    instances: [],
    ...over,
  };
}

const PUBLISHED: HubReceipt = {
  submission_id: "sub_1",
  status: "published",
  agent_uid: "a".repeat(32),
  slug: "river_scalper",
  version: 2,
  digest: "d".repeat(64),
  bytes: 256,
  submitted_at: "2026-10-20T10:00:00Z",
  decided_at: "2026-10-20T11:00:00Z",
  reasons: [],
  listing_id: "lst_8f3a9c2e1b",
  badge: "validated",
  published_at: "2026-10-20T11:00:00Z",
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function router(url: string, init?: RequestInit): Response | Promise<Response> {
  const method = init?.method ?? "GET";
  requests.push({ url, method });

  if (url.endsWith("/api/v1/admin/people")) {
    return isAdmin ? json([]) : json({ detail: "admin only" }, 403);
  }
  if (method === "GET" && url.includes("/api/v1/hub/agents?")) {
    return json({ listings: [], trust: "Provenance, not a signature." });
  }
  if (method === "GET" && url === "/api/v1/agents") {
    return json([
      agent({ slug: "river_scalper", name: "River Scalper" }),
      agent({ slug: "condor", name: "Condor" }),
    ]);
  }
  if (method === "GET" && url === "/api/v1/settings/hub-device") {
    return json(hubDeviceState);
  }
  if (method === "POST" && url === "/api/v1/settings/hub-device/start") {
    return json({
      user_code: "ABC-123",
      verification_uri: "https://botcamp.xyz/connect",
      verification_uri_complete: "https://botcamp.xyz/connect?code=ABC-123",
      expires_in: 600,
      interval: 5,
    });
  }
  if (url.endsWith("/submission")) {
    return json({ receipt: submissionReceipt });
  }
  if (url.includes("/bundle")) {
    return new Response(bundleText);
  }
  if (method === "POST" && url.endsWith("/publish")) {
    submissionReceipt = { ...PUBLISHED, status: "pending", listing_id: "", badge: "" };
    return json({ published: false, receipt: submissionReceipt });
  }
  if (method === "DELETE" && url.includes("/api/v1/hub/agents/")) {
    submissionReceipt = null;
    return json({ unpublished: true });
  }
  throw new Error(`unexpected request: ${method} ${url}`);
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  requests = [];
  isAdmin = false;
  submissionReceipt = null;
  bundleText = JSON.stringify({ version: 1, files: [{ path: "AGENT.md" }] });
  hubDeviceState = { ...NONE_DEVICE };

  vi.stubGlobal("fetch", vi.fn(router));
  vi.spyOn(window, "open").mockImplementation(() => null);

  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function render() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={["/hub"]}>
          <AgentHub />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await waitFor(() => requests.some((r) => r.url.endsWith("/admin/people")));
  await settle();
}

async function settle() {
  for (let i = 0; i < 8; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

async function waitFor(done: () => boolean) {
  for (let i = 0; i < 80 && !done(); i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  if (!done()) throw new Error("condition never became true");
}

function heading(text: string): Element | null {
  return (
    [...container.querySelectorAll("h2, h3")].find((h) => h.textContent === text) ??
    null
  );
}

function buttonByText(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(label),
  );
  if (!found) throw new Error(`no button matching text ${label}`);
  return found as HTMLButtonElement;
}

function buttonByAria(label: string): HTMLButtonElement {
  const found = container.querySelector(`button[aria-label="${label}"]`);
  if (!found) throw new Error(`no button matching aria-label ${label}`);
  return found as HTMLButtonElement;
}

async function click(button: HTMLButtonElement) {
  await act(async () => button.click());
}

/** Waits for the picker to load, then chooses one of this install's agents. */
async function chooseAgent(slug: string) {
  await waitFor(
    () => container.querySelector("select[aria-label='Choose an agent to publish']") !== null,
  );
  const select = container.querySelector(
    "select[aria-label='Choose an agent to publish']",
  ) as HTMLSelectElement;
  await act(async () => {
    select.value = slug;
    select.dispatchEvent(new Event("change", { bubbles: true }));
  });
  await waitFor(() => container.querySelector(`[data-upload-chosen='${slug}']`) !== null);
}

it("links back to Agents", async () => {
  await render();
  const link = container.querySelector("a[href='/']");
  expect(link?.textContent).toContain("Back to Agents");
});

it("a non-admin sees only Pull agents, with no admin-only request fired", async () => {
  isAdmin = false;
  await render();

  expect(heading("Pull agents")).not.toBeNull();
  expect(heading("Botcamp")).toBeNull();
  expect(heading("Upload yours")).toBeNull();
  // The empty catalogue reads as "nothing published", not as an outage —
  // the contrast the next test's 502 is there to prove.
  expect(container.textContent).toContain("nothing matching");

  expect(requests.some((r) => r.url === "/api/v1/agents")).toBe(false);
  expect(requests.some((r) => r.url === "/api/v1/settings/hub-device")).toBe(false);
});

it("an unreachable hub reads differently from an empty catalogue, for every seat", async () => {
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.endsWith("/api/v1/admin/people")) return json({ detail: "no" }, 403);
      if (url.includes("/api/v1/hub/agents?")) {
        return json({ detail: "the hub is down for maintenance" }, 502);
      }
      // Already recorded above; avoid double-counting in `router`.
      requests.pop();
      return router(url, init);
    }),
  );
  await render();

  const error = container.querySelector("[data-hub-error]");
  expect(error?.textContent).toContain("maintenance");
  expect(container.textContent).not.toContain("nothing matching");
});

it("an admin sees the Botcamp sign-in step, with Upload yours hidden until linked", async () => {
  isAdmin = true;
  await render();

  expect(heading("Botcamp")).not.toBeNull();
  expect(heading("Upload yours")).toBeNull();

  await click(buttonByText("Sign in to Botcamp"));
  await waitFor(() => container.querySelector("[data-hub-device-status='pending']") !== null);

  expect(container.querySelector("[data-hub-device-status='pending']")?.textContent).toContain(
    "ABC-123",
  );
  expect(window.open).toHaveBeenCalledWith(
    "https://botcamp.xyz/connect?code=ABC-123",
    "_blank",
    "noopener,noreferrer",
  );
  expect(heading("Upload yours")).toBeNull();
});

it("reveals Upload yours once linked, with the profile handle shown", async () => {
  isAdmin = true;
  hubDeviceState = {
    ...NONE_DEVICE,
    status: "linked",
    profile_handle: "@fede",
    scope: ["agents:read", "agents:publish"],
  };
  await render();

  expect(
    container.querySelector("[data-hub-device-status='linked']")?.textContent,
  ).toContain("@fede");
  expect(heading("Upload yours")).not.toBeNull();
});

it("shows a denied device login", async () => {
  isAdmin = true;
  hubDeviceState = { ...NONE_DEVICE, status: "denied" };
  await render();
  expect(container.querySelector("[data-hub-device-status='denied']")).not.toBeNull();
  expect(container.textContent).toContain("denied on Botcamp");
});

it("shows an expired device login", async () => {
  isAdmin = true;
  hubDeviceState = { ...NONE_DEVICE, status: "expired" };
  await render();
  expect(container.querySelector("[data-hub-device-status='expired']")).not.toBeNull();
  expect(container.textContent).toContain("expired before it was approved");
});

it("an admin can choose a local agent and publish it, once linked", async () => {
  isAdmin = true;
  hubDeviceState = { ...NONE_DEVICE, status: "linked", profile_handle: "@fede" };
  await render();
  await chooseAgent("river_scalper");

  await click(buttonByText("Publish"));
  await click(buttonByText("Publish")); // the preview's own confirm button

  await waitFor(() => requests.some((r) => r.method === "POST" && r.url.endsWith("/publish")));
  expect(requests.some((r) => r.method === "POST" && r.url.endsWith("/publish"))).toBe(true);
});

it("withdraws a published listing through the admin-only DELETE route", async () => {
  isAdmin = true;
  hubDeviceState = { ...NONE_DEVICE, status: "linked", profile_handle: "@fede" };
  submissionReceipt = PUBLISHED;
  await render();
  await chooseAgent("river_scalper");
  await waitFor(() => container.querySelector("[data-publish-status='published']") !== null);
  await waitFor(() => container.querySelector(`button[aria-label^='Withdraw']`) !== null);

  const trigger = "Withdraw river_scalper from the hub";
  expect(() => buttonByAria(trigger)).not.toThrow();

  await click(buttonByAria(trigger));
  await click(
    buttonByAria("Confirm withdraw — does not recall anybody's download"),
  );

  await waitFor(() =>
    requests.some((r) => r.method === "DELETE" && r.url.endsWith("/lst_8f3a9c2e1b")),
  );
  await waitFor(() => container.querySelector("[data-publish-status='published']") === null);
  expect(() => buttonByAria(trigger)).toThrow();
});
