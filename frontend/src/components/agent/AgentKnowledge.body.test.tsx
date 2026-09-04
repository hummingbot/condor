/**
 * A saved playbook is the one you read back (CORR-302).
 *
 * The reader caches a body under its own key, `["agent-brain-body", …]`, and
 * stays mounted across a save — the editor only flips off. So unless the
 * panel's `refresh` reaches that key too, Save drops you back onto the text
 * from before it, and clicking Edit again re-opens the editor on that stale
 * text: the next save silently overwrites the one you just made.
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

import type { AgentBrain, SkillBody } from "@/lib/api";

const getAgentBrain = vi.fn();
const getAgent = vi.fn();
const getDelegationHistory = vi.fn();
const getAgentSkill = vi.fn();
const updateAgentSkill = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...a: unknown[]) => getAgentBrain(...a),
    getAgent: (...a: unknown[]) => getAgent(...a),
    getDelegationHistory: (...a: unknown[]) => getDelegationHistory(...a),
    getAgentSkill: (...a: unknown[]) => getAgentSkill(...a),
    updateAgentSkill: (...a: unknown[]) => updateAgentSkill(...a),
  },
  CHAT_SLUG: "condor",
}));

const { AgentKnowledge } = await import("./AgentKnowledge");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SKILL_CARD = {
  slug: "range",
  name: "range",
  description: "d",
  when_to_use: "w",
  shared: false,
  inherited: false,
  muted: false,
  references_routine: "",
  routine_ok: true,
};

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
  skills: [SKILL_CARD],
  skill_proposal: null,
  memories: [],
  routines: [],
  strategies: [],
};

const OLD_BODY = "Widen the range when funding flips.";
const NEW_BODY = "Narrow the range when funding flips.";

const bodyPayload = (body: string): SkillBody => ({
  ...SKILL_CARD,
  body,
  files: [],
});

let container: HTMLDivElement;
let root: Root;

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <AgentKnowledge slug="orca" tab="skills" />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await settle();
}

const buttons = () => [...container.querySelectorAll<HTMLButtonElement>("button")];

/** The one button whose visible text or tooltip starts with `label`. */
function button(label: string) {
  const found = buttons().find(
    (b) =>
      b.textContent?.trim().startsWith(label) || b.title.startsWith(label),
  );
  if (!found) throw new Error(`No button for "${label}"`);
  return found;
}

const textarea = () => container.querySelector("textarea")!;
const body = () => container.textContent ?? "";

/** React listens to the native setter, not to `.value =`. */
async function type(el: HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(
    HTMLTextAreaElement.prototype,
    "value",
  )!.set!;
  await act(async () => {
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgentBrain.mockReset().mockResolvedValue(BRAIN);
  getAgent.mockReset().mockResolvedValue(null);
  getDelegationHistory.mockReset().mockResolvedValue({ delegations: [] });
  // Two reads of the same playbook: the one that opened it, and the one the
  // save has to force. The store answers with what is on disk each time.
  getAgentSkill
    .mockReset()
    .mockResolvedValueOnce(bodyPayload(OLD_BODY))
    .mockResolvedValue(bodyPayload(NEW_BODY));
  updateAgentSkill.mockReset().mockResolvedValue(bodyPayload(NEW_BODY));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("saving a playbook body", () => {
  it("re-reads it, so the reader shows what was just written", async () => {
    await render();

    await click(button("range"));
    expect(getAgentSkill).toHaveBeenCalledTimes(1);
    expect(body()).toContain(OLD_BODY);

    await click(button("Edit"));
    await type(textarea(), NEW_BODY);
    await click(button("Save"));

    expect(updateAgentSkill).toHaveBeenCalledTimes(1);
    // The reader never unmounted, so only an invalidation of its own key can
    // have fetched again.
    expect(getAgentSkill).toHaveBeenCalledTimes(2);
    expect(body()).toContain(NEW_BODY);
    expect(body()).not.toContain(OLD_BODY);
  });

  it("leaves Edit pre-filled with the saved text, not the original", async () => {
    await render();

    await click(button("range"));
    await click(button("Edit"));
    await type(textarea(), NEW_BODY);
    await click(button("Save"));

    // A second edit has to start from the first one, or it overwrites it.
    await click(button("Edit"));
    expect(textarea().value).toBe(NEW_BODY);
  });
});
