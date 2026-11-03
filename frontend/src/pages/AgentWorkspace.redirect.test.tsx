/**
 * Every address the retired `?view=` grammar ever named still lands (FEAT-119).
 *
 * This is the compatibility surface the feature spends. `?view=` is in
 * notification payloads, in the chat's route facts and in whatever anyone has
 * bookmarked, and it named twelve different things across two features — so the
 * one rule that matters is that no value of it dead-ends. The table is
 * `sectionForView` and is pinned in `sections.test.ts`; what is pinned *here*
 * is that the page calls it, and that a Being section leaves this route for the
 * chat's panel rather than being answered with an empty screen.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: () => new Promise(() => {}),
    getAgentRuns: () => new Promise(() => {}),
    getStrategy: () => new Promise(() => {}),
  },
  CHAT_SLUG: "condor",
}));

// The screen and the header are not what is under test; a redirect is answered
// before either would render anyway, and stubbing them keeps this file about
// the address.
vi.mock("@/components/agent/workspace/AgentRunScreen", () => ({
  AgentRunScreen: () => <div data-screen />,
}));
vi.mock("@/components/agent/workspace/WorkspaceHeader", () => ({
  WorkspaceHeader: () => null,
}));

const { AgentWorkspace, AgentRunsRedirect, AgentStrategyRedirect } =
  await import("./AgentWorkspace");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let at = "";

function Where() {
  const location = useLocation();
  useEffect(() => {
    at = `${location.pathname}${location.search}`;
  }, [location]);
  return null;
}

async function render(entry: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[entry]}>
        <QueryClientProvider client={client}>
          <Where />
          <Routes>
            <Route path="/" element={<div data-home />} />
            <Route path="/agents/:slug" element={<AgentWorkspace />} />
            <Route path="/agents/:slug/runs" element={<AgentRunsRedirect />} />
            <Route
              path="/agents/:slug/strategies/:sslug"
              element={<AgentStrategyRedirect />}
            />
          </Routes>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  at = "";
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("a `?view=` naming one of the seven Being sections", () => {
  it("lands on the chat's panel, open on that section", async () => {
    await render("/agents/brigado?view=skills");
    const params = new URLSearchParams(at.split("?")[1]);
    expect(at.split("?")[0]).toBe("/");
    expect(params.get("panel")).toBe("agent");
    expect(params.get("who")).toBe("brigado");
    expect(params.get("tab")).toBe("skills");
  });

  it("does the same for the older `?tab=`, which is in bookmarks", async () => {
    // What the agent page spelled its section with before FEAT-103 (FEAT-081).
    await render("/agents/brigado?tab=memories");
    expect(new URLSearchParams(at.split("?")[1]).get("tab")).toBe("memories");
  });
});

describe("a `?view=` naming one of the Doing views", () => {
  it.each([["money"], ["fleet"], ["playbook"], ["runs"]])(
    "opens the %s disclosure on this screen",
    async (view) => {
      await render(`/agents/brigado?view=${view}`);
      expect(at).toBe(`/agents/brigado?open=${view}`);
    },
  );

  it("carries the rest of the query string with it", async () => {
    await render("/agents/brigado?view=fleet&strategy=brl_mm&fscope=bot%3Ax");
    const params = new URLSearchParams(at.split("?")[1]);
    expect(params.get("open")).toBe("fleet");
    expect(params.get("strategy")).toBe("brl_mm");
    expect(params.get("fscope")).toBe("bot:x");
    expect(params.get("view")).toBeNull();
  });
});

describe("a `?view=` that names no disclosure at all", () => {
  it("lands on the screen with none open — the answer stack is Now", async () => {
    await render("/agents/brigado?view=now");
    expect(at).toBe("/agents/brigado");
  });

  it("keeps a tick, whose overlay `?tick=` opens on its own", async () => {
    await render("/agents/brigado?view=tick&strategy=brl_mm&tick=40");
    const params = new URLSearchParams(at.split("?")[1]);
    expect(params.get("tick")).toBe("40");
    expect(params.get("open")).toBeNull();
  });

  it("is a screen and not an error page for a value nobody has", async () => {
    await render("/agents/brigado?view=nonsense");
    expect(at).toBe("/agents/brigado");
  });
});

describe("the retired routes", () => {
  it("send the Lab to the Runs disclosure, carrying its query string", async () => {
    await render("/agents/brigado/runs?strategy=brl_mm&run=s3&tick=7");
    const params = new URLSearchParams(at.split("?")[1]);
    expect(at.split("?")[0]).toBe("/agents/brigado");
    expect(params.get("open")).toBe("runs");
    expect(params.get("run")).toBe("s3");
    expect(params.get("tick")).toBe("7");
  });

  it("send the strategy page to the playbook, scoped to it", async () => {
    await render("/agents/brigado/strategies/brl_mm");
    const params = new URLSearchParams(at.split("?")[1]);
    expect(at.split("?")[0]).toBe("/agents/brigado");
    expect(params.get("open")).toBe("playbook");
    expect(params.get("strategy")).toBe("brl_mm");
  });
});
