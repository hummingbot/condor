/**
 * Browsing the hub, and installing from it, is the import review with a label
 * on it (FEAT-146).
 *
 * The thing worth testing is not that a list renders. It is that nothing about
 * a listing shortens the gate: the `validated` badge is provenance, so a
 * community bundle and a validated one must reach the same screen with the
 * same executables list, the same findings and the same muted-on-arrival
 * sentence — and the dialog that renders them has to be the *same* dialog the
 * file path uses, because a second one would be a second chance to leave
 * something out.
 *
 * These tests assert on what crosses the wire rather than on the arguments
 * handed to `api.*`: a Review that quietly posted to the install endpoint
 * would read identically at the call site, and the promise here is about what
 * the server is asked to do.
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
  "The hub is a discovery service, not a security boundary. A validated badge " +
  "means a human reviewed one submitted revision once; it is not a signature, " +
  "Condor verifies no bundle against it, and it does not shorten this review. " +
  "Whatever you install arrives muted and unstarted, and what protects you is " +
  "reading it.";

const LISTING: HubListing = {
  listing_id: "lst_1",
  agent_uid: "a".repeat(32),
  slug: "river_scalper",
  name: "River Scalper",
  description: "grids a pool",
  version: 3,
  notes: "Widened the grid",
  author: { name: "Fede", handle: "@fede" },
  badge: "community",
  digest: "d".repeat(64),
  bytes: 4096,
  published_at: "2026-10-18T09:00:00Z",
  requires: {
    connectors: [],
    tools: [],
    agent_key: "",
    skills: { carried: [], inherited: [] },
  },
  findings: [],
  review_version: "",
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
  author: { name: "Fede", handle: "@fede" },
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
  author: { name: "Fede", handle: "@fede" },
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
let listings: HubListing[] = [LISTING];
let catalogueStatus = 200;
/** What the preview and install endpoints answer with. */
let verb: "install" | "update" = "install";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  requests = [];
  listings = [LISTING];
  catalogueStatus = 200;
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
      if (catalogueStatus !== 200) {
        return json({ detail: "the hub is down for maintenance" }, catalogueStatus);
      }
      return json({ listings, trust: TRUST });
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
  await waitFor(() => container.querySelector("[data-hub-listing], [data-hub-error]") !== null);
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

it("lists what the hub offers, with its badge and its author", async () => {
  await render();

  expect(requests).toEqual([{ url: "/api/v1/hub/agents?page=1", method: "GET" }]);
  const row = container.querySelector("[data-hub-listing='lst_1']");
  expect(row?.textContent).toContain("River Scalper");
  expect(row?.textContent).toContain("river_scalper");
  expect(row?.textContent).toContain("version 3");
  expect(container.querySelector("[data-hub-badge='community']")).not.toBeNull();
});

it("labels the author as an account claim and not a signature", async () => {
  await render();

  const author = container.querySelector("[data-hub-author]");
  expect(author?.textContent).toContain("@fede");
  expect(author?.textContent).toContain("not a signature");
});

it("previews before it writes, and never posts to the install endpoint", async () => {
  await render();
  await click("Review");

  expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
    "GET /api/v1/hub/agents?page=1",
    "POST /api/v1/hub/agents/lst_1/install/preview",
  ]);
});

it("shows the one trust sentence beside the badge it qualifies", async () => {
  await render();
  await click("Review");

  const provenance = container.querySelector("[data-hub-provenance]");
  expect(provenance?.textContent).toContain("not a security boundary");
  expect(provenance?.textContent).toContain("does not shorten this review");
});

it("reuses the import review, so every executable is named before the write", async () => {
  await render();
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
  await click("Review");
  // The dialog's own label, because the dialog is reused rather than wrapped:
  // the hub path and the file path offer the operator one verb, spelled once.
  await click("Import river_scalper");

  expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
    "GET /api/v1/hub/agents?page=1",
    "POST /api/v1/hub/agents/lst_1/install/preview",
    "POST /api/v1/hub/agents/lst_1/install",
  ]);
  expect(seen).toEqual([1]);
  expect(container.querySelector("[data-import-done]")?.textContent).toContain(
    "unmute what you trust",
  );
});

it("a validated badge reaches exactly the same review as a community one", async () => {
  await render();
  await click("Review");
  const community = container.querySelector("[data-hub-provenance]")!.parentElement!
    .textContent;

  await act(async () => root.unmount());
  container.remove();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  listings = [{ ...LISTING, badge: "validated" }];
  await render();
  await click("Review");
  const validated = container.querySelector("[data-hub-provenance]")!.parentElement!
    .textContent;

  // The badge is the only difference in the whole screen.
  expect(container.querySelector("[data-hub-badge='validated']")).not.toBeNull();
  expect(validated?.replace(/Reviewed once/g, "")).toEqual(
    community?.replace(/Community/g, ""),
  );
});

it("shows the update diff when the listing is an agent already installed", async () => {
  verb = "update";
  await render();
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
  await click("Review");
  await click("Update river_scalper");

  expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
    "GET /api/v1/hub/agents?page=1",
    "POST /api/v1/hub/agents/lst_1/install/preview",
    "POST /api/v1/hub/agents/lst_1/install",
  ]);
  expect(container.querySelector("[data-update-done]")).not.toBeNull();
});

it("says the hub is down rather than showing an empty catalogue", async () => {
  catalogueStatus = 503;
  await render();

  const shown = container.querySelector("[data-hub-error]");
  expect(shown?.textContent).toContain("maintenance");
  expect(container.textContent).not.toContain("nothing matching");
});

it("sends the search as a query parameter, never as a path segment", async () => {
  await render();
  const input = container.querySelector(
    "input[aria-label='Search the hub']",
  ) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(
    HTMLInputElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(input, "../../admin");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    container.querySelector("form")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  });
  await waitFor(() => requests.length > 1);

  const searched = requests[requests.length - 1].url;
  expect(searched.startsWith("/api/v1/hub/agents?")).toBe(true);
  expect(searched).not.toContain("/admin");
  expect(searched).toContain("query=..%2F..%2Fadmin");
});
