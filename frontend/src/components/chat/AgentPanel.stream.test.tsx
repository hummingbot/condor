/**
 * The agent panel does not re-parse AGENT.md on every stream flush (PERF-393).
 *
 * While an answer streams, the chat socket commits a new slot object every
 * 50 ms and `AgentChatTab` re-renders with it; the panel open beside the
 * conversation re-renders too, because its bar holds the wiring that reads the
 * slot. What must not follow it down is `AgentKnowledge`, whose Brain section
 * runs the whole AGENT.md through ReactMarkdown — a parse and a tree rebuild
 * inside render, twenty times a second. `AgentKnowledge` is `memo`'d and the
 * host hands it stable handlers, so a flush that changes only the transcript
 * leaves it alone, and a real prop change (another section) still lands.
 *
 * `react-markdown` is replaced by a counter so the parse is what is measured.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentBrain, ChatAgentOption } from "@/lib/api";
import type { ChatSlot } from "@/hooks/useChatSocket";

const getAgentBrain = vi.fn();
const getAgent = vi.fn();
const getDelegationHistory = vi.fn();
const getServers = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgentBrain: (...a: unknown[]) => getAgentBrain(...a),
    getAgent: (...a: unknown[]) => getAgent(...a),
    getDelegationHistory: (...a: unknown[]) => getDelegationHistory(...a),
    getServers: (...a: unknown[]) => getServers(...a),
  },
  CHAT_SLUG: "condor",
  parseCustomAgentKey: () => null,
  customAgentKey: (p: string, m: string) => `custom:${p}:${m}`,
}));

vi.mock("@/hooks/useChat", () => ({
  useChat: () => ({ isConnected: true }),
}));

/** How many times any ReactMarkdown rendered — each one is a full parse. */
const markdown = { renders: 0 };
vi.mock("react-markdown", () => ({
  default: ({ children }: { children?: string }) => {
    markdown.renders += 1;
    return <div data-markdown>{children}</div>;
  },
}));

const { AgentPanel } = await import("./AgentPanel");
const { AgentWiring } = await import("./AgentWiring");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

type PanelProps = Parameters<typeof AgentPanel>[0];

const AGENTS: ChatAgentOption[] = [
  { key: "claude-code", label: "Claude (ACP) — Sonnet" },
];

const BRAIN: AgentBrain = {
  slug: "orca",
  name: "Orca LP Expert",
  description: "Solana liquidity",
  agent_md: [
    "# Orca LP Expert",
    "",
    "You provide liquidity on Orca whirlpools.",
    "",
    "## Rules",
    "",
    "- Keep ranges inside the daily band.",
    "- Never rebalance twice in one hour.",
  ].join("\n"),
  agent_key: "claude-code",
  when_to_consult: "",
  server_required: false,
  server_name: "",
  tools: [],
  tools_unrestricted: true,
  skills: [],
  skill_proposal: null,
  memories: [],
  routines: [],
  strategies: [],
};

/** A conversation with Orca mid-answer: `text` is how far the stream got. */
function streamingSlot(text: string): ChatSlot {
  return {
    info: {
      slot_id: "s1",
      conversation_id: "c1",
      agent_key: "",
      agent_slug: "orca",
      server_name: "brigado_2",
    },
    messages: [
      { id: "u1", role: "user", text: "How are the ranges?", toolCalls: [] },
      { id: "a1", role: "assistant", text, toolCalls: [] },
    ],
  } as ChatSlot;
}

// The host's handlers, stable the way `AgentChatTab`'s `useCallback`s are.
const noop = () => {};
const handlers = {
  onTabChange: noop,
  onOpenRoutine: noop,
  onOpenStrategy: noop,
  onAskAgent: noop,
  onDirtyChange: noop,
  onClose: noop,
} satisfies Partial<PanelProps>;

let container: HTMLDivElement;
let root: Root;
let client: QueryClient;

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** One host render: the panel as `AgentChatTab` mounts it, off `slot`. */
async function renderPanel(slot: ChatSlot, over: Partial<PanelProps> = {}) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <AgentPanel
            slug="orca"
            name="Orca LP Expert"
            wiring={
              <AgentWiring
                slot={slot}
                pendingAgentKey=""
                ambientServer="brigado_2"
                agents={AGENTS}
                customProviders={[]}
                agentBindings={[]}
                isStreaming
                onSelectBrain={noop}
                onSelectServer={noop}
              />
            }
            {...handlers}
            {...over}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

const selectedSection = () =>
  [...document.querySelectorAll<HTMLElement>('[role="tab"]')].find(
    (t) => t.getAttribute("aria-selected") === "true",
  )?.textContent ?? "";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  markdown.renders = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  getAgentBrain.mockReset().mockResolvedValue(BRAIN);
  getAgent.mockReset().mockResolvedValue({ ...BRAIN, strategies: [] });
  getDelegationHistory.mockReset().mockResolvedValue({ delegations: [] });
  getServers.mockReset().mockResolvedValue([
    { name: "brigado_2", online: true },
  ]);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the agent panel while an answer streams (PERF-393)", () => {
  it("does not re-parse AGENT.md when a flush only moves the transcript", async () => {
    await renderPanel(streamingSlot("The ranges"));
    expect(document.querySelector("[data-markdown]")?.textContent).toContain(
      "Never rebalance twice in one hour.",
    );
    const afterMount = markdown.renders;
    expect(afterMount).toBeGreaterThan(0);

    // Twenty flushes: a new slot object each time, the stream one word longer.
    let text = "The ranges";
    for (let i = 0; i < 20; i++) {
      text += " are";
      await renderPanel(streamingSlot(text));
    }

    expect(markdown.renders).toBe(afterMount);
  });

  it("still re-renders the sections when the host changes a real prop", async () => {
    await renderPanel(streamingSlot("The ranges"));
    expect(selectedSection()).toContain("Brain");

    await renderPanel(streamingSlot("The ranges are"), { tab: "skills" });
    expect(selectedSection()).toContain("Skills");
    // Skills has no AGENT.md body, so the Brain section's markdown is gone.
    expect(document.querySelector("[data-markdown]")).toBeNull();

    const beforeBack = markdown.renders;
    await renderPanel(streamingSlot("The ranges are wide"), { tab: "brain" });
    expect(selectedSection()).toContain("Brain");
    expect(markdown.renders).toBeGreaterThan(beforeBack);
  });

  it("re-renders the sections when a handler's identity changes", async () => {
    // The memo is only as good as the host's handlers: an inline arrow per
    // render — what `AgentChatTab` used to pass — defeats it on every flush.
    await renderPanel(streamingSlot("The ranges"), { onAskAgent: () => {} });
    const afterMount = markdown.renders;

    await renderPanel(streamingSlot("The ranges are"), {
      onAskAgent: () => {},
    });

    expect(markdown.renders).toBeGreaterThan(afterMount);
  });
});
