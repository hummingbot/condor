/**
 * The switch that takes a playbook or a routine out of one agent's run (FEAT-090).
 *
 * The panel is the only place a mute is set, and the only place a muted item is
 * still visible — so what is pinned here is the pair: the switch reaches the
 * endpoint with the right kind and name, and a muted row keeps saying so
 * (chip + an off switch) instead of disappearing, which is what would make the
 * curation impossible to undo.
 *
 * The tab count is the third: it counts what the *agent* gets, so a library
 * with one of two muted reads "1/2" and not "2".
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

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...args: unknown[]) => getAgentBrain(...args),
    setAgentMute: (...args: unknown[]) => setAgentMute(...args),
  },
  CHAT_SLUG: "condor",
}));

const { AgentKnowledge } = await import("./AgentKnowledge");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function brain(muted: { lp?: boolean; scanner?: boolean } = {}): AgentBrain {
  return {
    slug: "brigado",
    name: "Brigado",
    description: "BRL market making",
    agent_md: "# Brigado",
    agent_key: "claude-code",
    when_to_consult: "",
    server_required: false,
    server_name: "",
    tools: [],
    tools_unrestricted: true,
    skills: [
      {
        slug: "lp_rebalance",
        name: "lp_rebalance",
        description: "Re-centre a range",
        when_to_use: "The range drifted",
        shared: true,
        inherited: true,
        muted: muted.lp ?? false,
        references_routine: "",
        routine_ok: true,
      },
      {
        slug: "funding_carry",
        name: "funding_carry",
        description: "Carry the funding",
        when_to_use: "Funding is rich",
        shared: false,
        inherited: false,
        muted: false,
        references_routine: "",
        routine_ok: true,
      },
    ],
    skill_proposal: null,
    memories: [],
    routines: [
      {
        name: "lp_scanner",
        description: "Scan the pools",
        continuous: false,
        source: "global",
        category: "",
        muted: muted.scanner ?? false,
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

function buttonWith(text: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(text),
  );
  if (!found) throw new Error(`No button reading "${text}"`);
  return found as HTMLButtonElement;
}

/** The row's switch, addressed by the label it exposes to a screen reader. */
function switchFor(rowTitle: string): HTMLButtonElement {
  const row = [...container.querySelectorAll("div.group")].find((d) =>
    d.textContent?.includes(rowTitle),
  );
  if (!row) throw new Error(`No row for "${rowTitle}"`);
  const found = row.querySelector('button[role="switch"]');
  if (!found) throw new Error(`No switch on the "${rowTitle}" row`);
  return found as HTMLButtonElement;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await settle();
}

async function open(tab: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <AgentKnowledge slug="brigado" />
      </QueryClientProvider>,
    );
  });
  await settle();
  await click(buttonWith(tab));
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgentBrain.mockReset().mockResolvedValue(brain());
  setAgentMute.mockReset().mockResolvedValue({
    kind: "skill",
    name: "lp_rebalance",
    muted: true,
  });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("muting a playbook", () => {
  it("switches it off through the endpoint, by slug", async () => {
    await open("Skills");

    const toggle = switchFor("lp_rebalance");
    expect(toggle.getAttribute("aria-checked")).toBe("true");

    await click(toggle);

    expect(setAgentMute).toHaveBeenCalledWith("brigado", {
      kind: "skill",
      name: "lp_rebalance",
      muted: true,
    });
  });

  it("is offered on an inherited shared playbook too — a mute is per-agent", async () => {
    await open("Skills");
    // lp_rebalance is inherited: no edit or delete, but a switch all the same.
    expect(switchFor("lp_rebalance")).toBeTruthy();
  });

  it("keeps the muted row listed, flagged and switchable back on", async () => {
    getAgentBrain.mockResolvedValue(brain({ lp: true }));
    await open("Skills");

    expect(container.textContent).toContain("lp_rebalance");
    expect(container.textContent).toContain("muted");
    const toggle = switchFor("lp_rebalance");
    expect(toggle.getAttribute("aria-checked")).toBe("false");

    await click(toggle);

    expect(setAgentMute).toHaveBeenCalledWith("brigado", {
      kind: "skill",
      name: "lp_rebalance",
      muted: false,
    });
  });

  it("counts what the agent gets, not what the panel lists", async () => {
    getAgentBrain.mockResolvedValue(brain({ lp: true }));
    await open("Skills");

    expect(buttonWith("Skills").textContent).toBe("Skills1/2");
  });
});

describe("muting a routine", () => {
  it("switches it off through the endpoint, by name", async () => {
    await open("Routines");

    // The routines tab titles rows through `formatRoutineName`.
    await click(switchFor("Lp Scanner"));

    expect(setAgentMute).toHaveBeenCalledWith("brigado", {
      kind: "routine",
      name: "lp_scanner",
      muted: true,
    });
  });

  it("says the human routines page is unaffected", async () => {
    await open("Routines");
    expect(container.textContent).toContain(
      "/routines still lists and runs it",
    );
  });
});
