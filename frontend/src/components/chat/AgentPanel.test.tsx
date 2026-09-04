/**
 * Who is answering, on what, where — and the one door to all of it (FEAT-081).
 *
 * The workspace header used to carry a model picker, a server chip and a link
 * that *left* the conversation to read what the agent knows. What replaced them
 * is pinned here across two homes: AGENT at the head of the workspace rail,
 * which is a door and nothing else, and the panel it opens, which holds
 * everything the rail does not say — including the two switches.
 *
 * What a refactor of this chrome must not lose: the door is one word, with no
 * name, description, model or server restating what the tab and the panel
 * behind it already say; both switches live in the panel's bar and go dead
 * with a reason while a turn is in flight; a pinned server offers no picker;
 * and with no session the model field still sets what the next conversation
 * starts on while the server field is a statement rather than a dead control.
 *
 * The panel's own body is the whole agent workspace since FEAT-117, and it is
 * stubbed here — it has its own cases. What this file pins about it is the
 * seam: the slug and the URL adapter it is handed, and the full-screen door
 * that was deliberately absent until the panel became the page.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
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

/**
 * The workspace body, stubbed: it is the agent's whole screen and it has its
 * own tests. Here it stands for "the panel is the page", and reports the two
 * things this seam owes it — whose workspace, and where its URL is kept.
 */
let bodyProps: { slug: string; view: string } | null = null;
vi.mock("@/components/agent/workspace/AgentWorkspaceBody", () => ({
  AgentWorkspaceBody: (props: {
    slug: string;
    adapter: { url: { view: string } };
  }) => {
    bodyProps = { slug: props.slug, view: props.adapter.url.view };
    return <div data-workspace-body={props.slug} />;
  },
}));

const { AgentPanel } = await import("./AgentPanel");
const { RailButton } = await import("./WorkspaceRail");
const { Bot } = await import("lucide-react");

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
/** Wherever the router is, so the full-screen door can be followed. */
let at = "";

function Probe() {
  const location = useLocation();
  // In an effect and not in the render body: recording where the router went
  // is a side effect, and the rule that forbids it in render is the one worth
  // keeping even in a probe.
  useEffect(() => {
    at = `${location.pathname}${location.search}`;
  }, [location]);
  return null;
}

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

async function renderPanel(
  over: Partial<PanelProps> = {},
  entry = "/",
) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[entry]}>
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
            onAskAgent={() => {}}
            onClose={() => (closed += 1)}
            {...over}
          />
          <Probe />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

type RailProps = Parameters<typeof RailButton>[0];

/** The rail entry exactly as `AgentChatTab` builds it. */
async function renderTune(over: Partial<RailProps> = {}) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client()}>
          <RailButton
            label="Agent"
            Icon={Bot}
            hint="Tune Orca LP Expert — read and change what this agent is"
            active={false}
            onToggle={() => (opened += 1)}
            {...over}
          />
          <Probe />
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
  at = "";
  bodyProps = null;
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

describe("the rail's agent entry", () => {
  it("says what the click opens, and nothing the row already says", async () => {
    await renderTune();

    // The subject is the whole label. Who that is, is on the session tab and
    // on the panel's own bar; the name was in here as a card and read as a
    // third statement of the same thing.
    expect(container.textContent).toContain("Agent");
    expect(container.textContent).not.toContain("Orca LP Expert");
    // ...but the reader who wants to be sure before clicking gets it.
    expect(container.querySelector("button")!.title).toContain(
      "Orca LP Expert",
    );
    // The description, the model and the server were all in the door once, and
    // between it and the panel the same wiring was on screen twice.
    expect(container.textContent).not.toContain("Solana liquidity");
    expect(row("model")).toBeNull();
    expect(row("server")).toBeNull();
  });

  it("is the door to the panel", async () => {
    await renderTune();
    await click(container.querySelector("button")!);
    expect(opened).toBe(1);
  });

  it("says whether the panel it opens is open", async () => {
    await renderTune();
    expect(
      container.querySelector("button")!.getAttribute("aria-pressed"),
    ).toBe("false");

    await renderTune({ active: true });
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
  it("is the agent's whole workspace, on the section the URL names", async () => {
    // It used to be `AgentKnowledge`'s seven Being sections and nothing of
    // what the agent was doing, so the question a conversation provokes — "it
    // says it deployed six controllers, did it?" — was the one it could not
    // answer.
    await renderPanel({}, "/?panel=agent&view=money");

    expect(bodyProps).toEqual({ slug: "orca", view: "money" });
    expect(container.querySelector("[data-workspace-body]")).not.toBeNull();
  });

  it("opens on Now, not on an AGENT.md dump", async () => {
    await renderPanel({}, "/?panel=agent");
    expect(bodyProps!.view).toBe("now");
  });

  it("draws no navigation of its own — the body's spine is the one", async () => {
    await renderPanel();
    expect(document.querySelector('[role="tablist"]')).toBeNull();
  });

  it("goes full screen to the page, carrying the whole selection", async () => {
    // The door this panel refused while it was a subset of the page. It is
    // not an escape hatch now: `/agents/:slug` renders the same component
    // from the same parameters, so this is a change of width.
    await renderPanel(
      {},
      "/?panel=agent&who=orca&view=runs&strategy=brl_mm&run=s:3&tick=40",
    );

    const maximize = document.querySelector<HTMLElement>(
      'button[title^="Full screen"]',
    )!;
    expect(maximize.title).toContain("on its own page");

    await click(maximize);
    // The pane's own `?panel=` and `?who=` stay behind; the workspace's four
    // travel.
    expect(at).toBe("/agents/orca?view=runs&strategy=brl_mm&run=s%3A3&tick=40");
  });

  it("goes to a bare page when nothing is selected", async () => {
    await renderPanel({}, "/?panel=agent");
    await click(document.querySelector<HTMLElement>('button[title^="Full screen"]')!);
    expect(at).toBe("/agents/orca");
  });

  it("just closes when nothing is being edited", async () => {
    await renderPanel();
    await click(document.querySelector<HTMLElement>('button[title^="Close"]')!);
    expect(closed).toBe(1);
  });
});
