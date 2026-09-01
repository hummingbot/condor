/**
 * Who is answering, on what, where — and the one door to all of it (FEAT-081).
 *
 * The workspace header used to carry a model picker, a server chip and a link
 * that *left* the conversation to read what the agent knows. What replaced them
 * is pinned here across two homes: the dock card, which names the agent once
 * and does nothing else, and the panel it opens, which holds everything the
 * card no longer says — including the two switches. The card sits in the
 * right-hand column so that opening an agent is the same gesture as opening a
 * routine's report: click in the dock, it opens in the pane.
 *
 * What a refactor of this chrome must not lose: the card is one line and one
 * door, with no description, no model and no server restating what the panel
 * is for; both switches live in the panel's bar and go dead with a reason
 * while a turn is in flight; a pinned server offers no picker; with no session
 * the model field still sets what the next conversation starts on while the
 * server field is a statement rather than a dead control; and the panel keeps
 * no link out of the workspace.
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

// The socket is the shell's; nothing under test opens one of its own.
vi.mock("@/hooks/useChat", () => ({
  useChat: () => ({ isConnected: true }),
}));

const { AgentPanel } = await import("./AgentPanel");
const { DockAgentCard } = await import("./DockAgent");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const AGENTS: ChatAgentOption[] = [
  { key: "claude-code", label: "Claude (ACP) — Sonnet" },
  { key: "gpt-5", label: "OpenAI — GPT-5" },
];
const BINDINGS = [
  {
    slug: "orca",
    name: "Orca LP Expert",
    description: "Solana liquidity",
    agent_key: "claude-code",
    when_to_consult: "",
  },
];

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
  skills: [],
  skill_proposal: null,
  memories: [],
  routines: [],
  strategies: [],
};

/** A live conversation with Orca on brigado_2. */
function slot(over: Partial<ChatSlot["info"]> = {}): ChatSlot {
  return {
    info: {
      slot_id: "s1",
      conversation_id: "c1",
      agent_key: "",
      agent_slug: "orca",
      server_name: "brigado_2",
      ...over,
    },
    messages: [],
  } as ChatSlot;
}

let container: HTMLDivElement;
let root: Root;
let closed: number;
let opened: number;
let picked: { model?: string; server?: string };

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function client() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

type PanelProps = Parameters<typeof AgentPanel>[0];

async function renderPanel(over: Partial<PanelProps> = {}) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client()}>
          <AgentPanel
            slug="orca"
            name="Orca LP Expert"
            slot={slot()}
            pendingAgentKey=""
            ambientServer="brigado_2"
            agents={AGENTS}
            customProviders={[]}
            agentBindings={BINDINGS}
            isStreaming={false}
            onSelectBrain={(sel) => (picked.model = sel.agentKey)}
            onSelectServer={(name) => (picked.server = name)}
            onOpenRoutine={() => {}}
            onClose={() => (closed += 1)}
            {...over}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

type CardProps = Parameters<typeof DockAgentCard>[0];

async function renderCard(over: Partial<CardProps> = {}) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client()}>
          <DockAgentCard
            name="Orca LP Expert"
            open={false}
            onOpen={() => (opened += 1)}
            {...over}
          />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

const row = (name: string) =>
  document.querySelector<HTMLElement>(`[data-session-row="${name}"]`);
/** The portalled menu's rows, wherever in the document they landed. */
const option = (label: string) =>
  [...document.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === label,
  );

/** jsdom lays nothing out, so a menu anchored to a 0×0 rect gets no room. */
function place(el: HTMLElement) {
  el.getBoundingClientRect = () =>
    ({
      top: 100,
      bottom: 124,
      left: 900,
      right: 1200,
      width: 300,
      height: 24,
    }) as DOMRect;
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  closed = 0;
  opened = 0;
  picked = {};
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgentBrain.mockReset().mockResolvedValue(BRAIN);
  getAgent.mockReset().mockResolvedValue({ ...BRAIN, strategies: [] });
  getDelegationHistory.mockReset().mockResolvedValue({ delegations: [] });
  getServers.mockReset().mockResolvedValue([
    { name: "brigado_2", online: true },
    { name: "moneymaker", online: true },
  ]);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the dock's agent card", () => {
  it("says what the click does, and who it does it to", async () => {
    await renderCard();

    // The name alone was here first, under a session tab carrying the same
    // name: a label where a door was meant, saying nothing about what clicking
    // it would do. The verb comes first now and the agent is muted beside it.
    expect(container.textContent).toContain("Tune agent");
    expect(container.textContent).toContain("Orca LP Expert");
    // The description, the model and the server were all here once, and
    // between this card and the panel the same wiring was on screen twice.
    expect(container.textContent).not.toContain("Solana liquidity");
    expect(row("model")).toBeNull();
    expect(row("server")).toBeNull();
  });

  it("is the door to the panel", async () => {
    await renderCard();
    await click(container.querySelector("button")!);
    expect(opened).toBe(1);
  });

  it("says whether the panel it opens is open", async () => {
    await renderCard();
    expect(
      container.querySelector("button")!.getAttribute("aria-pressed"),
    ).toBe("false");

    await renderCard({ open: true });
    expect(
      container.querySelector("button")!.getAttribute("aria-pressed"),
    ).toBe("true");
  });
});

describe("the panel's wiring bar", () => {
  it("names the model and the server the conversation runs on", async () => {
    await renderPanel();

    // Beside the server in a narrow bar the model wears its short name; the
    // catalogue name it was cut from is still one hover away.
    expect(row("model")!.textContent).toContain("Sonnet");
    expect(row("model")!.title).toContain("Claude (ACP) — Sonnet");
    expect(row("server")!.textContent).toContain("brigado_2");
  });

  it("switches the model through the same list the picker offers", async () => {
    await renderPanel();
    place(row("model")!);
    await click(row("model")!);

    await click(option("OpenAI — GPT-5")!);
    expect(picked.model).toBe("gpt-5");
  });

  it("moves the conversation to another server", async () => {
    await renderPanel();
    place(row("server")!);
    await click(row("server")!);

    await click(option("moneymaker")!);
    expect(picked.server).toBe("moneymaker");
  });

  it("goes dead with a reason while a turn is in flight", async () => {
    await renderPanel({ isStreaming: true });

    for (const name of ["model", "server"]) {
      const control = row(name) as HTMLButtonElement;
      expect(control.disabled).toBe(true);
      expect(control.title).toContain("Finish this turn");
    }
  });

  it("offers no picker for a server the agent pinned", async () => {
    await renderPanel({
      slot: slot({ server_pinned: true, label: "Orca LP Expert" }),
    });

    const control = row("server")!;
    expect(control.tagName).not.toBe("BUTTON");
    expect(control.textContent).toContain("brigado_2");
    expect(control.title).toContain("Pinned by Orca LP Expert");
  });
});

describe("with no session yet", () => {
  it("still reads, and the model field is the next chat's", async () => {
    await renderPanel({ slot: null, pendingAgentKey: "gpt-5" });

    expect(row("model")!.textContent).toContain("GPT-5");
    expect(row("model")!.title).toContain("next conversation");

    place(row("model")!);
    await click(row("model")!);
    await click(option("Claude (ACP) — Sonnet")!);
    expect(picked.model).toBe("claude-code");
  });

  it("states the server rather than offering a dead control", async () => {
    await renderPanel({ slot: null });

    const control = row("server")!;
    // Nothing to respawn, and the ambient selector already owns the choice.
    expect(control.tagName).not.toBe("BUTTON");
    expect(control.textContent).toContain("brigado_2");
    expect(control.title).toContain("next conversation");
  });
});

describe("the panel", () => {
  it("keeps the sections, and no door out of the workspace", async () => {
    await renderPanel();

    // The sections are a rail of vertical names, not a link to the page that
    // has them laid out horizontally: everything the agent is stays in here.
    const sections = [...document.querySelectorAll('[role="tab"]')].map((t) =>
      t.getAttribute("aria-label"),
    );
    expect(sections).toContain("Brain");
    expect(container.textContent).not.toContain("Full page");
    expect(container.querySelector("a")).toBeNull();
  });

  it("offers the pane and nothing larger", async () => {
    await renderPanel();

    // The panel is worked in while glancing back at the conversation beside
    // it, so the one outcome a full-screen button had was losing that chat.
    expect(document.querySelector('button[title^="Full screen"]')).toBeNull();
  });

  it("just closes when nothing is being edited", async () => {
    await renderPanel();
    await click(document.querySelector<HTMLElement>('button[title^="Close"]')!);
    expect(closed).toBe(1);
  });
});
