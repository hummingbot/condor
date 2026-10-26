/**
 * Publishing is not publishing — it is a submission (FEAT-150). The hub
 * answers an upload with a pending review, not a listing, so this component's
 * whole job is to never claim "Published" until a verdict actually says so,
 * and to keep showing the right state across a reload, a rejection and an
 * eventual badge.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { HubReceipt } from "@/lib/api";
import { PublishAgentButton } from "./AgentsSettings";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let requests: { url: string; method: string; body?: string }[] = [];
/** Possible keys the export stops on until each is acknowledged (SEC review C2). */
let possibleKeys: { path: string; line: number; label: string; fingerprint: string }[] = [];
/** What `GET .../submission` answers with next. */
let submissionReceipt: HubReceipt | null = null;
/** What `POST .../publish` answers with. */
let publishReceipt: HubReceipt;
let bundleText: string;

const PENDING: HubReceipt = {
  submission_id: "sub_1",
  status: "pending",
  agent_uid: "a".repeat(32),
  slug: "river_scalper",
  version: 1,
  digest: "d".repeat(64),
  bytes: 128,
  submitted_at: "2026-10-21T12:00:00Z",
  decided_at: "",
  reasons: [],
  listing_id: "",
  badge: "",
  published_at: "",
};

function json(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  requests = [];
  submissionReceipt = null;
  publishReceipt = PENDING;
  bundleText = JSON.stringify({ version: 1, files: [{ path: "AGENT.md" }] });
  possibleKeys = [];

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      requests.push({ url, method, body: init?.body as string | undefined });
      if (url.includes("/bundle")) {
        const acknowledged = new URL(url, "http://x").searchParams.getAll("acknowledge");
        const pending = possibleKeys.filter((k) => !acknowledged.includes(k.fingerprint));
        if (pending.length) {
          return json({ detail: "may be a private key", possible_keys: pending }, 409);
        }
        return new Response(bundleText);
      }
      if (url.endsWith("/submission")) return json({ receipt: submissionReceipt });
      if (url.endsWith("/publish")) {
        submissionReceipt = publishReceipt;
        return json({ published: publishReceipt.status === "published", receipt: publishReceipt });
      }
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
        <PublishAgentButton slug="river_scalper" />
      </QueryClientProvider>,
    );
  });
  await waitFor(() => requests.some((r) => r.url.endsWith("/submission")));
  // The request having fired is not the same as react-query having settled
  // its response into state and re-rendered — give it a few more ticks.
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

function button(label: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(label),
  );
  if (!found) throw new Error(`no button matching ${label}`);
  return found as HTMLButtonElement;
}

async function click(label: string) {
  await act(async () => button(label).click());
}

it("shows nothing pending before any publish, and polls nothing", async () => {
  await render();

  expect(container.querySelector("[data-publish-status]")).toBeNull();
  expect(requests.filter((r) => r.method === "GET")).toHaveLength(1);
});

it("never claims published from the upload response alone", async () => {
  await render();
  await click("Publish");
  await click("Publish"); // the preview's own confirm button

  await waitFor(() => container.querySelector("[data-publish-status='pending']") !== null);

  expect(container.textContent).toContain("Pending review");
  expect(container.textContent).toContain("sub_1");
  expect(container.textContent).not.toContain("Published");
});

it("shows a rejection with its reasons, and offers to resubmit", async () => {
  submissionReceipt = {
    ...PENDING,
    status: "rejected",
    decided_at: "2026-10-21T14:00:00Z",
    reasons: [{ rule: "human-review", detail: "Tool-steering language in a skill." }],
  };
  await render();

  expect(container.querySelector("[data-publish-status='rejected']")?.textContent).toContain(
    "Tool-steering language",
  );
  // A fresh publish is still offered — a rejection is not a dead end.
  expect(() => button("Publish again")).not.toThrow();
});

it("shows the listing and badge once a verdict publishes it", async () => {
  submissionReceipt = {
    ...PENDING,
    status: "published",
    decided_at: "2026-10-21T14:00:00Z",
    listing_id: "lst_8f3a9c2e1b",
    badge: "validated",
  };
  await render();

  const shown = container.querySelector("[data-publish-status='published']")?.textContent;
  expect(shown).toContain("lst_8f3a9c2e1b");
  expect(shown).toContain("validated");
});

it("recovers from a transient status failure without re-uploading", async () => {
  submissionReceipt = { ...PENDING };
  let failNext = true;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      requests.push({ url, method });
      if (url.endsWith("/submission")) {
        if (failNext) {
          failNext = false;
          return json({ detail: "the hub is down for maintenance" }, 502);
        }
        return json({ receipt: submissionReceipt });
      }
      throw new Error(`unexpected request: ${method} ${url}`);
    }),
  );

  await render();
  await waitFor(() => container.querySelector("[data-publish-error]") !== null);
  expect(container.querySelector("[data-publish-error]")?.textContent).toContain(
    "maintenance",
  );
  // Recovering means retrying the same GET — never re-uploading the agent.
  expect(requests.every((r) => !r.url.endsWith("/publish"))).toBe(true);
});

it("stops on a possible key and publishes only once the person rules it out", async () => {
  possibleKeys = [
    { path: "routines/r.py", line: 11, label: "evm-hex64", fingerprint: "sha256:08064dddc912e6a3" },
  ];
  await render();
  await click("Publish");

  const check = container.querySelector("[data-possible-keys]");
  expect(check?.textContent).toContain("routines/r.py:11");
  expect(container.querySelector("[data-publish-preview]")).toBeNull();
  // Continue stays off until every value is ticked as not a key.
  expect(button("Continue").disabled).toBe(true);

  const box = check!.querySelector("input[type=checkbox]") as HTMLInputElement;
  await act(async () => box.click());
  expect(button("Continue").disabled).toBe(false);
  await click("Continue");

  const preview = container.querySelector("[data-publish-preview]");
  expect(preview?.textContent).toContain("1 value you confirmed are not keys");
  expect(requests.at(-1)?.url).toContain("acknowledge=sha256%3A08064dddc912e6a3");

  await click("Publish"); // the preview's own confirm button
  const publish = requests.find((r) => r.url.endsWith("/publish"));
  expect(JSON.parse(publish!.body!).acknowledge).toEqual(["sha256:08064dddc912e6a3"]);
});
