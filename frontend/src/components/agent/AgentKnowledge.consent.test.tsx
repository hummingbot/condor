/**
 * The panel is where imported code is read and allowed to run (FEAT-147).
 *
 * Three things are pinned: a pending routine is listed rather than hidden, it
 * says so and says what no sandbox means; the Allow chip reaches the
 * `code-trust` endpoint and not the mute one; and the mute switch on a pending
 * row still works, because the two gestures are independent — unmuting says
 * *this agent may use it*, allowing says *this code may run here*.
 *
 * The tab count is the fourth: a pending routine is one the agent cannot call,
 * so a library of one pending and one loaded reads "1/2".
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentBrain } from "@/lib/api";

const getAgentBrain = vi.fn();
const setAgentMute = vi.fn();
const setAgentCodeTrust = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...args: unknown[]) => getAgentBrain(...args),
    setAgentMute: (...args: unknown[]) => setAgentMute(...args),
    setAgentCodeTrust: (...args: unknown[]) => setAgentCodeTrust(...args),
  },
  CHAT_SLUG: "condor",
}));

const { AgentKnowledge } = await import("./AgentKnowledge");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function brain(pendingReason = "pending"): AgentBrain {
  return {
    slug: "brigado",
    name: "Brigado",
    description: "an imported agent",
    agent_md: "# Brigado",
    agent_key: "claude-code",
    when_to_consult: "",
    server_required: false,
    server_name: "",
    tools: [],
    tools_unrestricted: true,
    skills: [],
    skill_proposal: null,
    memories: [],
    routines: [
      {
        name: "funding_watch",
        description: "Watch the funding",
        continuous: false,
        source: "agent:brigado",
        category: "Probe",
        muted: false,
      },
      {
        name: "lp_scanner",
        description: "Scan the pools",
        continuous: false,
        source: "agent:brigado",
        category: "Probe",
        muted: false,
        pending: true,
        pending_reason: pendingReason,
      },
    ],
    strategies: [],
  };
}

let container: HTMLDivElement;
let root: Root;

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

function rowFor(rowTitle: string): Element {
  const row = [...container.querySelectorAll("div.group")].find((d) =>
    d.textContent?.includes(rowTitle),
  );
  if (!row) throw new Error(`No row for "${rowTitle}"`);
  return row;
}

function chipFor(rowTitle: string, label: string): HTMLButtonElement {
  const found = [...rowFor(rowTitle).querySelectorAll("button")].find((b) =>
    b.textContent?.includes(label),
  );
  if (!found) throw new Error(`No "${label}" chip on the "${rowTitle}" row`);
  return found as HTMLButtonElement;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await settle();
}

async function openRoutines() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <AgentKnowledge slug="brigado" tab="routines" />
      </QueryClientProvider>,
    );
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgentBrain.mockReset().mockResolvedValue(brain());
  setAgentMute.mockReset().mockResolvedValue({});
  setAgentCodeTrust.mockReset().mockResolvedValue({
    kind: "routine",
    name: "lp_scanner",
    allowed: true,
    digest: "9f2c",
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("a routine awaiting execution consent", () => {
  it("is listed, flagged, and says the code runs unsandboxed", async () => {
    await openRoutines();

    expect(container.textContent).toContain("Lp Scanner");
    expect(container.textContent).toContain("not allowed to run");
    expect(container.textContent).toContain("no sandbox");
  });

  it("says nothing about sandboxes when everything is allowed", async () => {
    getAgentBrain.mockResolvedValue({
      ...brain(),
      routines: brain().routines.filter((r) => !r.pending),
    });
    await openRoutines();

    expect(container.textContent).toContain("Funding Watch");
    expect(container.textContent).not.toContain("no sandbox");
  });

  it("reads differently when it was allowed and then edited", async () => {
    getAgentBrain.mockResolvedValue(brain("changed"));
    await openRoutines();

    expect(container.textContent).toContain("changed — allow");
  });

  it("allows it through the code-trust endpoint, not the mute one", async () => {
    await openRoutines();

    await click(chipFor("Lp Scanner", "allow"));

    expect(setAgentCodeTrust).toHaveBeenCalledWith("brigado", {
      kind: "routine",
      name: "lp_scanner",
      allowed: true,
    });
    expect(setAgentMute).not.toHaveBeenCalled();
  });

  it("keeps its mute switch, because the two gestures are separate", async () => {
    await openRoutines();

    const toggle = rowFor("Lp Scanner").querySelector(
      'button[role="switch"]',
    ) as HTMLButtonElement;
    await click(toggle);

    expect(setAgentMute).toHaveBeenCalledWith("brigado", {
      kind: "routine",
      name: "lp_scanner",
      muted: true,
    });
    expect(setAgentCodeTrust).not.toHaveBeenCalled();
  });

  it("counts against what the agent gets", async () => {
    await openRoutines();

    // One of the two routines can actually be called, so the tab says 1/2.
    expect(container.textContent).toContain("1/2");
  });
});
