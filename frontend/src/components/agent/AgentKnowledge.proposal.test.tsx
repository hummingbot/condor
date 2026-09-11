/**
 * The playbook the agent offered, and the click that accepts it (FEAT-074).
 *
 * The card is the human half of "the agent proposes, a human accepts": until
 * one of its two buttons is pressed the proposal is on disk and in no prompt.
 * So what is pinned here is that both buttons reach the right endpoint and then
 * re-read the brain — and that a pending proposal is not counted as a skill,
 * because a count that included it would say the library had grown when it has
 * not.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentBrain, SkillProposal } from "@/lib/api";

const getAgentBrain = vi.fn();
const acceptAgentSkillProposal = vi.fn();
const discardAgentSkillProposal = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...args: unknown[]) => getAgentBrain(...args),
    acceptAgentSkillProposal: (...args: unknown[]) =>
      acceptAgentSkillProposal(...args),
    discardAgentSkillProposal: (...args: unknown[]) =>
      discardAgentSkillProposal(...args),
  },
  CHAT_SLUG: "condor",
}));

const { AgentKnowledge } = await import("./AgentKnowledge");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const PROPOSAL: SkillProposal = {
  name: "clmm_rebalance",
  description: "Re-centre a CLMM position when price leaves the range",
  when_to_use: "The user asks to check or rebalance an LP range",
  body: "1. Pull the pool state\n2. Compare it to the position bounds",
  source: "reflection",
  from_conversation: "8f2c1a4b90de",
  created: "2026-08-27T10:00:00Z",
};

function brain(proposal: SkillProposal | null): AgentBrain {
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
        slug: "existing",
        name: "existing",
        description: "d",
        when_to_use: "w",
        shared: false,
        inherited: false,
        muted: false,
        references_routine: "",
        routine_ok: true,
      },
    ],
    skill_proposal: proposal,
    memories: [],
    routines: [],
    strategies: [],
  };
}

let container: HTMLDivElement;
let root: Root;

function buttonWith(text: string): HTMLButtonElement {
  const found = [...container.querySelectorAll("button")].find((b) =>
    b.textContent?.includes(text),
  );
  if (!found) throw new Error(`No button reading "${text}"`);
  return found as HTMLButtonElement;
}

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function click(text: string) {
  await act(async () => {
    buttonWith(text).click();
  });
  await settle();
}

/** Render the panel on Skills, where the card lives. The section is a prop
 *  and not a click: the panel draws no navigation of its own (FEAT-117). */
async function open() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <AgentKnowledge slug="brigado" tab="skills" />
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
  getAgentBrain.mockReset().mockResolvedValue(brain(PROPOSAL));
  acceptAgentSkillProposal.mockReset().mockResolvedValue({
    accepted: true,
    name: "clmm_rebalance",
  });
  discardAgentSkillProposal.mockReset().mockResolvedValue({ discarded: true });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the proposed playbook", () => {
  it("offers itself above the library, with its trigger and its origin", async () => {
    await open();

    expect(container.textContent).toContain("Proposed from a conversation");
    expect(container.textContent).toContain("clmm_rebalance");
    expect(container.textContent).toContain(
      "The user asks to check or rebalance an LP range",
    );
    // The steps are behind the expander until asked for.
    expect(container.textContent).not.toContain("Pull the pool state");
  });

  it("shows its steps on demand", async () => {
    await open();

    await click("Read the steps");

    expect(container.textContent).toContain("Pull the pool state");
  });

  it("accepts into the library and takes the card away", async () => {
    await open();
    getAgentBrain.mockResolvedValue(brain(null));

    await click("Accept");

    expect(acceptAgentSkillProposal).toHaveBeenCalledWith("brigado");
    expect(discardAgentSkillProposal).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("Proposed from a conversation");
  });

  it("discards without touching the library", async () => {
    await open();
    getAgentBrain.mockResolvedValue(brain(null));

    await click("Discard");

    expect(discardAgentSkillProposal).toHaveBeenCalledWith("brigado");
    expect(acceptAgentSkillProposal).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain("Proposed from a conversation");
    expect(container.textContent).toContain("existing");
  });

  it("says so when the accept was refused, and keeps the card", async () => {
    acceptAgentSkillProposal.mockRejectedValue(new Error("the library said no"));
    await open();

    await click("Accept");

    expect(container.textContent).toContain("the library said no");
    expect(container.textContent).toContain("Proposed from a conversation");
  });

  it("renders nothing of the kind when nothing is proposed", async () => {
    getAgentBrain.mockResolvedValue(brain(null));

    await open();

    expect(container.textContent).not.toContain("Proposed from a conversation");
  });
});
