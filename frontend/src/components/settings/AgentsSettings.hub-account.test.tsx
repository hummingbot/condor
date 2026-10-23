/**
 * The Botcamp *account* surface in Settings (PR #111): who is signed in,
 * read live from `GET /hub-me` rather than the `profile_handle` the
 * device-login poll cached once; which scopes the token carries, with a
 * plain warning when `agents:publish` is missing; and Disconnect, honest
 * about whether the hub's own copy was actually revoked.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import type { HubDeviceState, HubMe } from "@/lib/api";
import { BotcampAccountSettings } from "./AgentsSettings";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let requests: { url: string; method: string }[] = [];
let isAdmin: boolean;
let deviceState: HubDeviceState;
let meState: HubMe | null;
let meStatus: number;
let disconnectResponse: { status: string; revoked: boolean | null; error: string };
let hubDeviceCallCount: number;
//: When set, the Nth `GET /hub-device` answers with the Nth entry (the last
//: entry repeats past the end) — used to simulate the server having already
//: cleared the token by the time a refetch lands, instead of racing a test's
//: own reassignment of `deviceState` against the component's own refetch.
let hubDeviceSequence: HubDeviceState[] | null;

const LINKED: HubDeviceState = {
  status: "linked",
  user_code: "",
  approval_url: "",
  expires_at: 0,
  profile_handle: "cardosofede",
  scope: ["agents:read", "agents:publish"],
};

const NONE: HubDeviceState = {
  status: "none",
  user_code: "",
  approval_url: "",
  expires_at: 0,
  profile_handle: "",
  scope: [],
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
  isAdmin = true;
  deviceState = LINKED;
  meState = { user: { id: "u_1", name: "cardosofede", slug: "cardosofede" }, scopes: ["agents:read", "agents:publish"] };
  meStatus = 200;
  disconnectResponse = { status: "none", revoked: true, error: "" };
  hubDeviceCallCount = 0;
  hubDeviceSequence = null;

  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const method = init?.method ?? "GET";
      requests.push({ url, method });
      if (url.endsWith("/api/v1/admin/people")) {
        return isAdmin ? json([]) : json({ detail: "admin only" }, 403);
      }
      if (method === "GET" && url === "/api/v1/settings/hub-device") {
        hubDeviceCallCount += 1;
        if (hubDeviceSequence) {
          const idx = Math.min(hubDeviceCallCount, hubDeviceSequence.length) - 1;
          return json(hubDeviceSequence[idx]);
        }
        return json(deviceState);
      }
      if (method === "DELETE" && url === "/api/v1/settings/hub-device") {
        return json(disconnectResponse);
      }
      if (method === "GET" && url === "/api/v1/settings/hub-me") {
        return meStatus === 200
          ? json(meState)
          : json({ detail: "The hub refused this token (401)" }, meStatus);
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
        <MemoryRouter>
          <BotcampAccountSettings />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await waitFor(() => requests.some((r) => r.url === "/api/v1/settings/hub-device"));
  for (let i = 0; i < 10; i++) {
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

it("mounts no admin-only query for a non-admin seat", async () => {
  isAdmin = false;
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <MemoryRouter>
          <BotcampAccountSettings />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
  await waitFor(() => requests.some((r) => r.url.endsWith("/api/v1/admin/people")));
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  expect(requests.some((r) => r.url === "/api/v1/settings/hub-device")).toBe(false);
  expect(container.querySelector("[data-botcamp-account-status]")).toBeNull();
});

it("tells the operator nothing is connected when no profile is linked", async () => {
  deviceState = NONE;
  await render();

  expect(container.textContent).toContain("Not connected");
  expect(requests.some((r) => r.url === "/api/v1/settings/hub-me")).toBe(false);
});

it("shows who is signed in and the scopes the token carries, read live", async () => {
  await render();

  const status = container.querySelector("[data-botcamp-account-status='linked']");
  expect(status?.textContent).toContain("cardosofede");
  expect(container.textContent).toContain("agents:read, agents:publish");
  expect(container.querySelector("[data-botcamp-missing-publish-scope]")).toBeNull();
});

it("warns plainly when the token cannot publish", async () => {
  meState = { user: { id: "u_1", name: "cardosofede", slug: "cardosofede" }, scopes: ["agents:read"] };
  await render();

  const warning = container.querySelector("[data-botcamp-missing-publish-scope]");
  expect(warning).not.toBeNull();
  expect(warning?.textContent).toContain("cannot publish");
});

it("treats a 401 from the live check as revoked elsewhere and falls back", async () => {
  meStatus = 401;
  // On a real backend the second `hub-device` read already comes back
  // `"none"`, because `condor.hub.me`'s own 401 handling cleared the token
  // before this route ever answered — simulate that server-side sequencing
  // directly rather than racing it against this test's own timing.
  hubDeviceSequence = [LINKED, NONE];
  await render();

  await waitFor(() =>
    requests.filter((r) => r.url === "/api/v1/settings/hub-device").length >= 2,
  );
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  expect(container.textContent).toContain("Not connected");
});

it("says nothing extra once the hub confirmed the revoke", async () => {
  await render();

  const button = Array.from(container.querySelectorAll("button")).find((b) =>
    b.textContent?.includes("Disconnect"),
  );
  expect(button).toBeTruthy();
  await act(async () => {
    button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await waitFor(() =>
    requests.some((r) => r.method === "DELETE" && r.url === "/api/v1/settings/hub-device"),
  );
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  expect(container.textContent).not.toContain("could not be told");
});

it("tells the operator plainly when the hub could not be told of the disconnect", async () => {
  disconnectResponse = {
    status: "none",
    revoked: false,
    error: "The hub refused revoking this token (500)",
  };
  await render();

  const button = Array.from(container.querySelectorAll("button")).find((b) =>
    b.textContent?.includes("Disconnect"),
  );
  await act(async () => {
    button?.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await waitFor(() =>
    requests.some((r) => r.method === "DELETE" && r.url === "/api/v1/settings/hub-device"),
  );
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }

  expect(container.textContent).toContain(
    "Botcamp could not be told — revoke it yourself from your Botcamp profile's token list.",
  );
});
