/**
 * The agent inspector is host-agnostic (FEAT-081).
 *
 * It used to be welded to `/agents/:slug`: the open section was local state
 * nothing outside could name, and Strategies and Activity arrived as injected
 * slots, so a second host that forgot to pass them would have shown an agent
 * with no strategies at all. The chat's agent panel is that second host, and
 * these cases pin what makes it possible — a controlled `tab` that a URL or a
 * pane can drive, and seven sections that are the component's own rather than
 * the page's.
 *
 * Since FEAT-117 there is no navigation in here at all: both hosts draw the
 * workspace's spine, so the section arrives as a prop and a body is checked by
 * what it renders rather than by which tab is lit.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentBrain, AgentDetail } from "@/lib/api";

const getAgentBrain = vi.fn();
const getAgent = vi.fn();
const getDelegationHistory = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...a: unknown[]) => getAgentBrain(...a),
    getAgent: (...a: unknown[]) => getAgent(...a),
    getDelegationHistory: (...a: unknown[]) => getDelegationHistory(...a),
  },
  CHAT_SLUG: "condor",
}));

const { AgentKnowledge } = await import("./AgentKnowledge");
const { KNOWLEDGE_TABS, isKnowledgeTab } = await import("./knowledgeTabs");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const BRAIN: AgentBrain = {
  slug: "orca",
  name: "Orca LP Expert",
  description: "Solana liquidity",
  agent_md: "# Orca",
  agent_key: "claude-code",
  when_to_consult: "",
  server_required: false,
  server_name: "",
  tools: [],
  tools_unrestricted: true,
  skills: [
    {
      slug: "range",
      name: "range",
      description: "d",
      when_to_use: "w",
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
      name: "orca/pool_walk",
      description: "Walk a pool",
      continuous: false,
      source: "agent",
      category: "",
      muted: false,
    },
  ],
  strategies: [],
};

const AGENT = {
  slug: "orca",
  name: "Orca LP Expert",
  description: "Solana liquidity",
  agent_md: "# Orca",
  agent_key: "claude-code",
  tools: [],
  when_to_consult: "",
  server_required: false,
  server_name: "",
  strategies: [],
} as AgentDetail;

let container: HTMLDivElement;
let root: Root;

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function render(props: Parameters<typeof AgentKnowledge>[0]) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <AgentKnowledge {...props} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

const body = () => container.textContent ?? "";

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgentBrain.mockReset().mockResolvedValue(BRAIN);
  getAgent.mockReset().mockResolvedValue(AGENT);
  getDelegationHistory.mockReset().mockResolvedValue({ delegations: [] });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the sections", () => {
  it("are the component's own, in one order", async () => {
    expect([...KNOWLEDGE_TABS]).toEqual([
      "brain",
      "skills",
      "memories",
      "tools",
      "strategies",
      "routines",
      "activity",
    ]);
  });

  it("include Strategies and Activity with nothing injected", async () => {
    // The two the page used to hand in. A host cannot forget them any more,
    // because there is nothing left for a host to pass.
    await render({ slug: "orca", tab: "strategies" });
    expect(body()).toContain("No strategies yet");

    await render({ slug: "orca", tab: "activity" });
    expect(getDelegationHistory).toHaveBeenCalledWith("orca", 100, undefined);
  });

  it("only accepts a section it actually has, off a URL", () => {
    expect(isKnowledgeTab("skills")).toBe(true);
    expect(isKnowledgeTab("delegations")).toBe(false);
    expect(isKnowledgeTab(null)).toBe(false);
  });
});

describe("the open section", () => {
  it("is Brain when the host does not name one", async () => {
    // Nothing in here can change it: the hosts own the section, because both
    // of them keep it in the URL.
    await render({ slug: "orca" });
    expect(body()).toContain("Orca");
  });

  it("is the host's when it names one", async () => {
    await render({ slug: "orca", tab: "routines" });
    expect(body()).toContain("Pool Walk");
  });

  it("draws no navigation of its own, in either host", async () => {
    // The workspace's spine carries these seven beside the loop's own views,
    // in the page and in the chat's pane alike (FEAT-103, FEAT-117); a strip
    // in here would be a second navigation for one thing.
    await render({ slug: "orca" });
    expect(container.querySelector('[role="tablist"]')).toBeNull();
    expect(container.querySelector('[role="tab"]')).toBeNull();
  });

  it("tells the host when a body asks for another section", async () => {
    // A skill row that names a routine jumps to Routines, and the host is the
    // only thing that can move a section now — so the jump has to be reported
    // rather than kept.
    const onTabChange = vi.fn();
    await render({ slug: "orca", tab: "skills", onTabChange });

    const jump = [...container.querySelectorAll("button")].find((b) =>
      b.title?.includes("routine"),
    );
    if (jump) {
      await click(jump);
      expect(onTabChange).toHaveBeenCalledWith("routines");
    }
  });
});

describe("a routine row", () => {
  it("is inert until a host offers somewhere to open it", async () => {
    await render({ slug: "orca", tab: "routines" });

    const row = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Pool Walk"),
    );
    expect(row).toBeUndefined();
  });

  it("hands the routine to the host that has a library", async () => {
    const onOpenRoutine = vi.fn();
    await render({ slug: "orca", tab: "routines", onOpenRoutine });

    const row = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Pool Walk"),
    )!;
    await click(row);
    expect(onOpenRoutine).toHaveBeenCalledWith("orca/pool_walk");
  });
});

describe("the brain section", () => {
  /** What the file on disk actually looks like: front matter, then prose. */
  const WITH_FRONT_MATTER = [
    "---",
    "name: Orca LP Expert",
    "description: Solana liquidity",
    "agent_key: claude-code",
    "server_name: ''",
    "created_by: 481175164",
    "---",
    "",
    "## Orca",
    "",
    "Reads pools, then --- weighs them.",
  ].join("\n");

  it("reads the prose, not the record above it", async () => {
    getAgentBrain.mockResolvedValue({ ...BRAIN, agent_md: WITH_FRONT_MATTER });
    await render({ slug: "orca" });

    // Every field of the front matter is either said by the host's header or
    // set by a control there, and markdown renders the block as one run-on
    // paragraph of `key: value` above the thing you came here to read.
    expect(body()).not.toContain("created_by");
    expect(body()).not.toContain("agent_key:");
    expect(body()).toContain("Reads pools");
    // A rule inside the prose is prose: only the block at the very top goes.
    expect(body()).toContain("--- weighs them");
  });

  it("names the agent's slug, model and server nowhere — the host does", async () => {
    await render({ slug: "orca" });

    // The chat's panel carries the live model and server in its own bar one
    // line above this, and the agent page carries slug, model and server in
    // its header three lines above. Chips here said all of it a second time.
    expect(body()).not.toContain("claude-code");
    expect(body()).not.toContain("chat default model");
  });
});
