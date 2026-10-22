/**
 * Looking up one Botcamp agent by slug, and installing it, is the import
 * review with a label on it (FEAT-146).
 *
 * Botcamp (PR #111) ships no browse/search — only a lookup by a slug the
 * operator must already have — so the thing worth testing here is the
 * lookup itself: a free agent resolves with no token, a distinct server
 * sentence survives for "not found" vs "needs a membership" vs "sign in",
 * and the review dialog reused from the file path still names every
 * executable before the write. That dialog is reused rather than
 * reimplemented deliberately: the review *is* the sandbox, so a second
 * review screen would be a second chance to omit the executables list or the
 * muted-on-arrival sentence — and the screen that got it wrong would be the
 * one the operator actually used.
 *
 * These tests assert on what crosses the wire rather than on the arguments
 * handed to `api.*`: a Review that quietly posted to the install endpoint
 * would read identically at the call site, and the promise here is about
 * what the server is asked to do.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { AgentImportPlan, AgentUpdatePlan, HubListing } from "@/lib/api";
import { HubBrowser } from "./HubBrowser";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const TRUST =
  "Only an approved version is ever listed here, but that is a gate on the " +
  "catalogue, not a security boundary. Whatever you install arrives muted " +
  "and unstarted, and the review on install is the gate.";

const LISTING: HubListing = {
  slug: "river_scalper",
  name: "River Scalper",
  description: "grids a pool",
  latest: 3,
  versions: [
    {
      version: 3,
      sha256: "d".repeat(64),
      size: 4096,
      file_count: 3,
      source_ref: "",
      created_at: "2026-10-18T09:00:00Z",
      manifest: {},
    },
  ],
};

const PLAN: AgentImportPlan = {
  slug: "river_scalper",
  source_slug: "river_scalper",
  name: "River Scalper",
  description: "grids a pool",
  when_to_consult: "",
  exported_at: "2026-10-18T09:00:00Z",
  schema_version: 1,
  agent_uid: "a".repeat(32),
  version: 3,
  notes: "Widened the grid",
  author: { name: "", handle: "" },
  requires: {
    connectors: [],
    tools: [],
    agent_key: "",
    skills: { carried: [], inherited: [] },
  },
  unmet: [],
  findings: [],
  files: [{ path: "AGENT.md", bytes: 240 }],
  executables: [{ path: "routines/r.py", bytes: 980 }],
  total_bytes: 1220,
  stamps: { created_by: "7", server_name: "" },
  mutes: { routines: ["r"], skills: ["s"] },
  agent_key: "claude-code",
  agent_key_inherited: false,
  collision: "",
  suggested_slug: "",
  collision_agent_uid: "",
  is_update: false,
  refusals: [],
};

const UPDATE: AgentUpdatePlan = {
  slug: "river_scalper",
  agent_uid: "a".repeat(32),
  installed_agent_uid: "a".repeat(32),
  version: 3,
  installed_version: 2,
  exported_at: "2026-10-18T09:00:00Z",
  notes: "Widened the grid",
  author: { name: "", handle: "" },
  requires: {
    connectors: [],
    tools: [],
    agent_key: "",
    skills: { carried: [], inherited: [] },
  },
  unmet: [],
  findings: [],
  files: [
    { path: "AGENT.md", verdict: "changed", bytes: 240, executable: false, incoming: "" },
    {
      path: "routines/r.py",
      verdict: "conflict",
      bytes: 980,
      executable: true,
      incoming: "# theirs\n",
    },
  ],
  counts: { changed: 1, conflict: 1 },
  mutes: { routines: [], skills: [] },
  blockers: [],
  warnings: [],
};

let container: HTMLDivElement;
let root: Root;
let requests: { url: string; method: string }[] = [];
let listingStatus = 200;
let listingBody: unknown = { listing: LISTING, trust: TRUST };
/** What the preview and install endpoints answer with. */
let verb: "install" | "update" = "install";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  requests = [];
  listingStatus = 200;
  listingBody = { listing: LISTING, trust: TRUST };
  verb = "install";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      requests.push({ url, method: init?.method ?? "GET" });
      if (url.includes("/install")) {
        const plan = verb === "update" ? UPDATE : PLAN;
        const body =
          init?.method === "POST" && !url.includes("/preview")
            ? { ...plan, ...(verb === "update" ? { updated: true } : { imported: true }), next_step: "unmute what you trust" }
            : plan;
        return json({ verb, listing: LISTING, plan: body });
      }
      if (listingStatus !== 200) {
        return json(listingBody, listingStatus);
      }
      return json(listingBody);
    }),
  );
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

async function render(onInstalled = () => {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <HubBrowser onInstalled={onInstalled} />
      </QueryClientProvider>,
    );
  });
}

async function waitFor(done: () => boolean) {
  for (let i = 0; i < 50 && !done(); i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
  if (!done()) throw new Error("condition never became true");
}

function button(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(label),
  );
  if (!found) throw new Error(`no button matching ${label}`);
  return found as HTMLButtonElement;
}

async function click(label: string) {
  const target = button(label);
  await act(async () => {
    target.click();
  });
}

async function lookUp(slug: string) {
  const input = container.querySelector(
    "input[aria-label='Botcamp agent slug']",
  ) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(input, slug);
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    container.querySelector("form")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  });
  await waitFor(
    () => container.querySelector("[data-hub-listing], [data-hub-error]") !== null,
  );
}

it("says there is no browsable catalogue yet", async () => {
  await render();

  expect(container.textContent).toContain("does not serve a browsable catalogue");
});

it("looks up one agent by slug and shows its latest version", async () => {
  await render();
  await lookUp("river_scalper");

  expect(requests).toEqual([
    { url: "/api/v1/hub/agents/river_scalper", method: "GET" },
  ]);
  const row = container.querySelector("[data-hub-listing='river_scalper']");
  expect(row?.textContent).toContain("River Scalper");
  expect(row?.textContent).toContain("river_scalper");
  expect(row?.textContent).toContain("latest version 3");
});

it("previews before it writes, and never posts to the install endpoint", async () => {
  await render();
  await lookUp("river_scalper");
  await click("Review");

  expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
    "GET /api/v1/hub/agents/river_scalper",
    "POST /api/v1/hub/agents/river_scalper/install/preview",
  ]);
});

it("shows the one trust sentence beside the agent it describes", async () => {
  await render();
  await lookUp("river_scalper");
  await click("Review");

  const provenance = container.querySelector("[data-hub-provenance]");
  expect(provenance?.textContent).toContain("not a security boundary");
});

it("reuses the import review, so every executable is named before the write", async () => {
  await render();
  await lookUp("river_scalper");
  await click("Review");

  const listed = [...container.querySelectorAll("[data-executable]")].map((n) =>
    n.textContent?.trim(),
  );
  expect(listed).toHaveLength(1);
  expect(listed[0]).toContain("routines/r.py");
  expect(container.textContent).toContain("no sandbox");
  // The muted-on-arrival sentence, which is the other half of the gate.
  expect(container.textContent).toContain("Muted on arrival");
  expect(container.textContent).toContain("r, s");
});

it("installs only when the operator confirms, through the install endpoint", async () => {
  const seen: number[] = [];
  await render(() => seen.push(1));
  await lookUp("river_scalper");
  await click("Review");
  // The dialog's own label, because the dialog is reused rather than wrapped:
  // the hub path and the file path offer the operator one verb, spelled once.
  await click("Import river_scalper");

  expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
    "GET /api/v1/hub/agents/river_scalper",
    "POST /api/v1/hub/agents/river_scalper/install/preview",
    "POST /api/v1/hub/agents/river_scalper/install",
  ]);
  expect(seen).toEqual([1]);
  expect(container.querySelector("[data-import-done]")?.textContent).toContain(
    "unmute what you trust",
  );
});

it("shows the update diff when the agent is already installed", async () => {
  verb = "update";
  await render();
  await lookUp("river_scalper");
  await click("Review");

  expect(container.querySelector("[data-update-plan]")).not.toBeNull();
  const kept = [...container.querySelectorAll("[data-update-file='conflict']")].map(
    (n) => n.textContent,
  );
  expect(kept[0]).toContain("routines/r.py");
  expect(kept[0]).toContain("your copy kept");
  // Never the import plan's button for an update: a second copy beside the
  // old one with a forked memory is the exact failure FEAT-145 exists for.
  expect(() => button("Import river_scalper")).toThrow();
});

it("applies an update through the same install endpoint the server routes", async () => {
  verb = "update";
  await render();
  await lookUp("river_scalper");
  await click("Review");
  await click("Update river_scalper");

  expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
    "GET /api/v1/hub/agents/river_scalper",
    "POST /api/v1/hub/agents/river_scalper/install/preview",
    "POST /api/v1/hub/agents/river_scalper/install",
  ]);
  expect(container.querySelector("[data-update-done]")).not.toBeNull();
});

it("says the hub is down rather than showing an empty result", async () => {
  listingStatus = 503;
  listingBody = { detail: "the hub is down for maintenance" };
  await render();
  await lookUp("river_scalper");

  const shown = container.querySelector("[data-hub-error]");
  expect(shown?.textContent).toContain("maintenance");
});

it("shows the server's own distinct sentence for a membership-gated agent", async () => {
  listingStatus = 402;
  listingBody = { detail: "this agent requires a Botcamp membership or purchase" };
  await render();
  await lookUp("river_scalper");

  const shown = container.querySelector("[data-hub-error]");
  expect(shown?.getAttribute("data-hub-error-status")).toBe("402");
  expect(shown?.textContent).toContain("membership");
});

it("shows the server's own distinct sentence for an agent that is not found", async () => {
  listingStatus = 404;
  listingBody = { detail: "agent 'nope': not found on Botcamp." };
  await render();
  await lookUp("nope");

  const shown = container.querySelector("[data-hub-error]");
  expect(shown?.getAttribute("data-hub-error-status")).toBe("404");
  expect(shown?.textContent).toContain("not found");
});

it("the slug is addressed with what the caller typed, never a path segment escape", async () => {
  listingStatus = 400;
  listingBody = { detail: "invalid agent slug" };
  await render();
  await lookUp("../../admin");

  const sent = requests[requests.length - 1].url;
  expect(sent).toBe(`/api/v1/hub/agents/${encodeURIComponent("../../admin")}`);
  expect(sent).not.toContain("/admin");
});
