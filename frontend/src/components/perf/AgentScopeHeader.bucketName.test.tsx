/**
 * What the scope header calls the row it was opened from (CORR-363).
 *
 * The agent axis carries three kinds of node and only one of them is a run.
 * The sidebar has always known that and named all three through
 * `agentBucketLabel`; the header did not, and clicking either bucket row
 * replaced a named row with an `<h2>` holding one whitespace character — the
 * sentinel `" outside"` handed back unchanged by a namer that only knows how to
 * split a run key. The pane a reader lands on right after finding a misfiled
 * executor had no title.
 *
 * Pinned at the rendered header rather than at the label function, because the
 * function was already right: the defect was a *caller* that did not ask it. So
 * all three rows are rendered here, and the ordinary one is in the file to
 * prove the fix did not quietly rename every agent on the page.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { FleetOwner } from "@/lib/agent-attribution";
import { AgentScopeHeader } from "./AgentScopeHeader";
import { BEFORE_LEDGER, OUTSIDE } from "./agentFilter";

const navigate = vi.fn();
vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>("react-router-dom");
  return { ...actual, useNavigate: () => navigate };
});

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const brigado: FleetOwner = {
  runKey: "brigado.brl_mm",
  agentSlug: "brigado",
  agentName: "Brigado",
  strategySlug: "brl_mm",
  strategyName: "BRL MM",
  namespace: "brigado-brl_mm",
  declaredBots: [],
  agentIds: ["brigado.brl_mm_7"],
  live: {
    agentId: "brigado.brl_mm_7",
    sessionNum: 7,
    status: "running",
    tickCount: 214,
    lastTickAt: Math.floor(Date.now() / 1000),
    frequencySec: 60,
    lastAction: "",
    lastDid: null,
    lastError: "",
  },
};

/** A door, as `_pseudo_owners` builds it: empty namespace, words in the map. */
const dashboard: FleetOwner = {
  runKey: "condor.ui",
  agentSlug: "condor",
  agentName: "Condor",
  strategySlug: "ui",
  strategyName: "Dashboard",
  namespace: "",
  declaredBots: [],
  agentIds: [],
  live: null,
};

const OWNERS: readonly FleetOwner[] = [brigado, dashboard];

let container: HTMLDivElement;
let root: Root;

async function render(runKey: string, owner?: FleetOwner) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <AgentScopeHeader runKey={runKey} owners={OWNERS} owner={owner} />
      </MemoryRouter>,
    );
  });
}

const heading = () => container.querySelector("h2")!;
/** The name itself, without the chips and status words sharing the heading. */
const name = () => heading().querySelector("span")!.textContent;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  navigate.mockClear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the agent scope header's name", () => {
  it("names the bucket that found no record, instead of showing its sentinel", async () => {
    await render(OUTSIDE);

    expect(name()).toBe("No record found");
    // The sentinel is a leading space, so a blank title is exactly how the bug
    // looked: the tooltip fell back to the raw key too.
    expect(heading().querySelector("span")!.title).toBe("No record found");
    expect(container.textContent).not.toContain(OUTSIDE);
  });

  it("names the bucket from before the ledger, the sibling that goes the same way", async () => {
    await render(BEFORE_LEDGER);

    expect(name()).toBe("Before the ledger");
    expect(heading().querySelector("span")!.title).toBe("Before the ledger");
    expect(container.textContent).not.toContain(BEFORE_LEDGER);
  });

  it("still calls an ordinary agent row by its slugs, with its loop beside it", async () => {
    await render(brigado.runKey, brigado);

    expect(name()).toBe("brigado / brl_mm");
    expect(heading().querySelector("span")!.title).toBe("Brigado / BRL MM");
    expect(container.textContent).toContain("running");
  });

  // Not a new judgement, just the one the sidebar row already makes: a door's
  // slugs name nothing a reader has seen, so the map's words are the name.
  it("says the fleet map's words for a door, the same as the row it was opened from", async () => {
    await render(dashboard.runKey, dashboard);

    expect(name()).toBe("Condor / Dashboard");
  });
});

describe("what a bucket is not offered", () => {
  it("reports no loop status for a bucket, which has no loop to be idle", async () => {
    await render(OUTSIDE);

    expect(container.textContent).not.toContain("idle");
    expect(container.querySelector("button")).toBeNull();
  });

  it("keeps the status and the session button for a real run", async () => {
    await render(brigado.runKey, brigado);

    const open = [...container.querySelectorAll<HTMLElement>("button")].find((b) =>
      b.textContent?.includes("Open session"),
    );
    expect(open).toBeTruthy();
  });
});
