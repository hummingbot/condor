/**
 * Pulling a Botcamp agent — picked from the catalogue or looked up by slug —
 * and installing it, is the import review with a label on it (FEAT-146).
 *
 * The catalogue only says which agents exist; a pull from it is the same
 * lookup and review as a slug typed by hand. Worth testing: a distinct server
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

import type {
  AgentImportPlan,
  AgentUpdatePlan,
  HubCatalogueEntry,
  HubListing,
} from "@/lib/api";
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
/** Catalogue pages are kept apart from `requests`, which pins the pull's own wire. */
let catalogueRequests: string[] = [];
let cataloguePages: HubCatalogueEntry[][] = [[]];
let listingStatus = 200;
let listingBody: unknown = { listing: LISTING, trust: TRUST };
/** What the preview and install endpoints answer with. */
let verb: "install" | "update" = "install";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  requests = [];
  catalogueRequests = [];
  cataloguePages = [[]];
  listingStatus = 200;
  listingBody = { listing: LISTING, trust: TRUST };
  verb = "install";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (url.startsWith("/api/v1/hub/catalogue")) {
        catalogueRequests.push(url);
        const page = Number(new URL(url, "http://x").searchParams.get("page"));
        return json({
          listings: cataloguePages[page - 1] ?? [],
          page,
          trust: TRUST,
          botcamp_url: "http://hub.test/strategies?type=AGENT",
        });
      }
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
    input.closest("form")!.dispatchEvent(
      new Event("submit", { bubbles: true, cancelable: true }),
    );
  });
  await waitFor(
    () => container.querySelector("[data-hub-listing], [data-hub-error]") !== null,
  );
}

const entry = (slug: string, fields: Partial<HubCatalogueEntry> = {}): HubCatalogueEntry => ({
  listing_id: slug,
  slug,
  name: slug.replace(/_/g, " "),
  description: "",
  version: 1,
  author: { name: "James", handle: "james" },
  badge: "validated",
  digest: "",
  bytes: 0,
  published_at: "",
  free: true,
  page_url: `http://hub.test/strategies/${slug}`,
  ...fields,
});

it("lists Botcamp's published agents, with who made them and whether they're free", async () => {
  cataloguePages = [[entry("river_scalper", { name: "River Scalper" }), entry("paid_bot", { free: false })]];
  await render();
  await waitFor(() => container.querySelectorAll("[data-hub-catalogue-entry]").length === 2);

  expect(catalogueRequests).toEqual(["/api/v1/hub/catalogue?query=&page=1"]);
  const river = container.querySelector("[data-hub-catalogue-entry='river_scalper']");
  expect(river?.textContent).toContain("River Scalper");
  expect(river?.textContent).toContain("James");
  expect(river?.textContent).toContain("Validated");
  expect(container.querySelector("[data-hub-catalogue-entry='paid_bot']")?.textContent).toContain(
    "Members",
  );
});

it("links to Botcamp's agents and to each agent's own page, in a new tab", async () => {
  cataloguePages = [[entry("river_scalper")]];
  await render();
  await waitFor(() => container.querySelector("[data-hub-catalogue-entry]") !== null);

  const all = container.querySelector("[data-hub-botcamp-link]") as HTMLAnchorElement;
  expect(all.href).toBe("http://hub.test/strategies?type=AGENT");
  const page = container.querySelector(
    "[data-hub-catalogue-entry='river_scalper'] a",
  ) as HTMLAnchorElement;
  expect(page.href).toBe("http://hub.test/strategies/river_scalper");
  for (const link of [all, page]) {
    expect(link.target).toBe("_blank");
    expect(link.rel).toContain("noopener");
  }
});

it("reviews a catalogue agent through the same preview a looked-up slug gets", async () => {
  cataloguePages = [[entry("river_scalper")]];
  await render();
  await waitFor(() => container.querySelector("[data-hub-catalogue-entry]") !== null);
  await click("Review");
  await waitFor(() => container.querySelector("[data-hub-provenance]") !== null);

  expect(requests.map((r) => `${r.method} ${r.url}`)).toEqual([
    "POST /api/v1/hub/agents/river_scalper/install/preview",
  ]);
  expect(container.querySelector("[data-hub-provenance]")?.textContent).toContain(
    "not a security boundary",
  );
});

it("searches the catalogue with what was typed", async () => {
  await render();
  await waitFor(() => catalogueRequests.length === 1);
  const input = container.querySelector(
    "input[aria-label='Search Botcamp agents']",
  ) as HTMLInputElement;
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")!.set!;
  await act(async () => {
    setter.call(input, "grid bot");
    input.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await act(async () => {
    input.closest("form")!.dispatchEvent(new Event("submit", { bubbles: true, cancelable: true }));
  });
  await waitFor(() => catalogueRequests.length === 2);
  await waitFor(() => container.querySelector("[data-hub-catalogue-empty]")?.textContent?.includes("grid bot") ?? false);

  expect(catalogueRequests[1]).toBe("/api/v1/hub/catalogue?query=grid+bot&page=1");
  expect(container.querySelector("[data-hub-catalogue-empty]")?.textContent).toContain(
    'No published agent matches "grid bot"',
  );
});

it("pages on only while Botcamp sends full pages", async () => {
  cataloguePages = [
    Array.from({ length: 20 }, (_, i) => entry(`bot_${i}`)),
    [entry("last_bot")],
  ];
  await render();
  await waitFor(() => container.querySelectorAll("[data-hub-catalogue-entry]").length === 20);
  await click("Show more");
  await waitFor(() => container.querySelectorAll("[data-hub-catalogue-entry]").length === 21);

  expect(catalogueRequests).toEqual([
    "/api/v1/hub/catalogue?query=&page=1",
    "/api/v1/hub/catalogue?query=&page=2",
  ]);
  expect(container.textContent).not.toContain("Show more");
});

it("opens the review over the page, and closes it with the X or Escape", async () => {
  cataloguePages = [[entry("river_scalper", { name: "River Scalper" })]];
  await render();
  await waitFor(() => container.querySelector("[data-hub-catalogue-entry]") !== null);
  expect(container.querySelector("[role='dialog']")).toBeNull();

  await click("Review");
  const dialog = container.querySelector("[role='dialog'][aria-modal='true']");
  expect(dialog?.getAttribute("aria-label")).toBe("Review River Scalper");
  expect(dialog?.querySelector("[data-hub-provenance]")).not.toBeNull();

  await act(async () => {
    (container.querySelector("[aria-label='Close review']") as HTMLButtonElement).click();
  });
  expect(container.querySelector("[role='dialog']")).toBeNull();

  await click("Review");
  expect(container.querySelector("[role='dialog']")).not.toBeNull();
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  });
  expect(container.querySelector("[role='dialog']")).toBeNull();
});

it("still offers the lookup by slug, for a private agent the catalogue doesn't list", async () => {
  await render();

  expect(container.textContent).toContain("Private agents aren't listed");
  expect(container.querySelector("input[aria-label='Botcamp agent slug']")).not.toBeNull();
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
  // In the Hub's modal it stays in view while the review scrolls.
  expect(button("Import river_scalper").parentElement?.className).toContain("sticky");
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
