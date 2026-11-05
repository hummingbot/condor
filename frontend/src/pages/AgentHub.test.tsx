/**
 * Botcamp Hub as one destination (FEAT-153): Pull agents is gated on this
 * install being signed in to Botcamp (botcamp-site PR #137's connect-code
 * flow) for every seat, admin or not, and the Botcamp section itself (the
 * connected profile + upload) is admin-only, with withdraw finally having a
 * dashboard control.
 *
 * The thing worth testing is not that the sections render — it is that the
 * admin-only section mounts no admin-only query for a non-admin seat, that
 * Pull agents shows a sign-in prompt instead of the lookup form until this
 * install is linked (for every seat, not just admins), that there is exactly
 * one "Sign in to Botcamp" control on the page rather than the two the first
 * cut shipped, that an unreachable hub reads differently from an empty one,
 * that Upload yours stays hidden until this install is linked, and that
 * publish and withdraw actually hit the routes the contract defines.
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
  profile_handle: "",
  scope: [],
  connect_url: "https://botcamp.xyz/connect",
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
  if (method === "GET" && url === "/api/v1/agents") {
    return json([
      agent({ slug: "river_scalper", name: "River Scalper" }),
      agent({ slug: "condor", name: "Condor" }),
    ]);
  }
  if (method === "GET" && url === "/api/v1/settings/hub-device") {
    return json(hubDeviceState);
  }
  if (method === "GET" && url === "/api/v1/settings/hub-device/linked") {
    return json({ linked: hubDeviceState.status === "linked" });
  }
  if (method === "POST" && url === "/api/v1/settings/hub-device/connect") {
    // The real backend stores the token the moment the redeem succeeds, so
    // the next `GET /hub-device` already reads back "linked" — simulate
    // that server-side sequencing here rather than tracking it separately.
    hubDeviceState = {
      ...hubDeviceState,
      status: "linked",
      profile_handle: "@fede",
      scope: ["agents:read", "agents:publish"],
    };
    return json(hubDeviceState);
  }
  if (method === "GET" && url.startsWith("/api/v1/hub/catalogue")) {
    return json({ listings: [], page: 1, trust: "" });
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

/** One tab of the hub's tablist, by its label. */
function tab(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button[role='tab']")].find((b) =>
    b.textContent?.includes(label),
  );
  if (!found) throw new Error(`no tab matching text ${label}`);
  return found as HTMLButtonElement;
}

/** The panel of one tab — `pull` or `publish` — or null when it is not open. */
function panel(id: "pull" | "publish"): Element | null {
  return container.querySelector(`#hub-panel-${id}`);
}

/** The same lookup as `buttonByText`, but scoped to one tab's panel. */
function buttonInPanel(id: "pull" | "publish", label: string): HTMLButtonElement {
  const open = panel(id);
  if (!open) throw new Error(`the ${id} panel is not open`);
  const found = [...open.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(label),
  );
  if (!found) throw new Error(`no button matching text ${label} in the ${id} panel`);
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
  if (!panel("publish")) await click(tab("Publish yours"));
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

it("carries no back link — it sits inside the Agents workspace, whose rail is the way back", async () => {
  await render();
  expect(container.querySelector("a[href='/']")).toBeNull();
  expect(container.textContent).not.toContain("Back to Agents");
});

it("a non-admin sees a sign-in prompt in Pull agents when this install is not linked", async () => {
  isAdmin = false;
  await render();

  expect(panel("pull")).not.toBeNull();
  // Publishing is a tab that says why it cannot be used, not a tab that is
  // missing: "where do I publish mine?" has an answer before sign-in.
  expect(tab("Publish yours").disabled).toBe(true);
  expect(tab("Publish yours").title).toContain("Only an admin");
  expect(panel("publish")).toBeNull();
  expect(heading("Upload yours")).toBeNull();
  expect(container.textContent).toContain("Sign in to Botcamp to look up or pull agents");
  // No sign-in affordance for a non-admin — there is no Botcamp section on
  // this page for them to reach it from.
  expect(container.textContent).toContain("Ask an admin to connect this install");
  expect(container.querySelector("input[aria-label='Botcamp agent slug']")).toBeNull();

  expect(requests.some((r) => r.url === "/api/v1/agents")).toBe(false);
  expect(requests.some((r) => r.url.includes("/api/v1/hub/agents/"))).toBe(false);
});

it("offers an admin exactly one Sign in to Botcamp control, in the Pull gate", async () => {
  isAdmin = true;
  await render();

  // The regression this page shipped with: the Pull gate's modal trigger and
  // the Botcamp section's inline flow were two controls for one install-wide
  // link. The Botcamp section does not exist until this install is linked.
  const signIns = [...container.querySelectorAll("a, button")].filter((el) =>
    el.textContent?.includes("Sign in to Botcamp"),
  );
  expect(signIns).toHaveLength(1);
  expect(tab("Publish yours").disabled).toBe(true);
  expect(tab("Publish yours").title).toContain("Sign in to Botcamp");
  expect(heading("Upload yours")).toBeNull();
  // No modal either — the flow runs where the gate is.
  expect(container.querySelector(".fixed.inset-0")).toBeNull();
});

it("shows the sign-in link GET /hub-device builds, never anything from the hub", async () => {
  // `connect_url` is always set on the read, linked or not (SEC-819): the
  // link renders straight off it, with no "start" call and no local state
  // to rebuild after a page refresh.
  isAdmin = true;
  hubDeviceState = { ...NONE_DEVICE, connect_url: "https://botcamp.xyz/connect" };
  await render();

  const link = [...container.querySelectorAll("a")].find((a) =>
    a.textContent?.includes("Sign in to Botcamp"),
  ) as HTMLAnchorElement;
  expect(link.getAttribute("href")).toBe("https://botcamp.xyz/connect");
});

it("a non-admin sees the lookup form in Pull agents once this install is linked", async () => {
  isAdmin = false;
  hubDeviceState = { ...NONE_DEVICE, status: "linked", profile_handle: "@fede" };
  await render();

  expect(panel("pull")).not.toBeNull();
  // The catalogue is there for every signed-in seat, admin or not; the
  // by-slug lookup is folded away behind its own control.
  expect(container.querySelector("input[aria-label='Search Botcamp agents']")).not.toBeNull();
  expect(container.textContent).toContain("Look up a private agent by slug");
  // Still no publishing for a non-admin seat, linked or not.
  expect(tab("Publish yours").disabled).toBe(true);

  expect(requests.some((r) => r.url === "/api/v1/agents")).toBe(false);
});

it("an unreachable hub reads differently from a not-found slug, for every seat", async () => {
  hubDeviceState = { ...NONE_DEVICE, status: "linked", profile_handle: "@fede" };
  vi.stubGlobal(
    "fetch",
    vi.fn((url: string, init?: RequestInit) => {
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.endsWith("/api/v1/admin/people")) return json({ detail: "no" }, 403);
      if (url.includes("/api/v1/hub/agents/")) {
        return json({ detail: "the hub is down for maintenance" }, 502);
      }
      // Already recorded above; avoid double-counting in `router`.
      requests.pop();
      return router(url, init);
    }),
  );
  await render();

  await click(buttonByText("Look up a private agent by slug"));
  const input = container.querySelector(
    "input[aria-label='Botcamp agent slug']",
  ) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(input, "river_scalper");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    input.closest("form")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  });
  await waitFor(() => container.querySelector("[data-hub-error]") !== null);

  const error = container.querySelector("[data-hub-error]");
  expect(error?.textContent).toContain("maintenance");
});

it("connects inline in the gate by pasting a code, with Upload yours hidden until linked", async () => {
  isAdmin = true;
  await render();

  expect(heading("Upload yours")).toBeNull();

  const open = panel("pull");
  const input = open!.querySelector(
    "input[aria-label='Botcamp connect code']",
  ) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(input, "WXYZ-WXYZ-WXYZ");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await click(buttonInPanel("pull", "Connect"));

  await waitFor(() =>
    requests.some(
      (r) => r.method === "POST" && r.url === "/api/v1/settings/hub-device/connect",
    ),
  );
  await settle();

  expect(heading("Upload yours")).toBeNull(); // still the Pull tab, not Publish
  const chip = container.querySelector("[data-hub-device-status='linked']");
  expect(chip?.textContent).toContain("@fede");
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

  // The connected profile is install-wide, so it sits in the page header
  // above both tabs rather than inside either one.
  const chip = container.querySelector("[data-hub-device-status='linked']");
  expect(chip?.textContent).toContain("@fede");
  expect(chip?.closest("header")).not.toBeNull();

  expect(tab("Publish yours").disabled).toBe(false);
  await click(tab("Publish yours"));
  expect(heading("Upload yours")).not.toBeNull();
});

it("an admin can choose a local agent and publish it, once linked", async () => {
  isAdmin = true;
  hubDeviceState = { ...NONE_DEVICE, status: "linked", profile_handle: "@fede" };
  await render();
  await chooseAgent("river_scalper");

  // Scoped to the panel: the tablist's own "Publish yours" comes first in
  // document order, and a bare text match would press the tab.
  await click(buttonInPanel("publish", "Publish"));
  await click(buttonInPanel("publish", "Publish")); // the preview's own confirm

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
