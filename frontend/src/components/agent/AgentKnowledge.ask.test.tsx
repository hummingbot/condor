/**
 * The row action that hands a playbook, routine or tool to its own agent
 * (FEAT-092).
 *
 * What is pinned here is the contract between the panel and whichever host it
 * is mounted in: the panel decides *what to say* and always names the item, so
 * the conversation it opens is about the row you clicked and not about a name
 * you had to retype. The host decides how to say it, so the panel offers the
 * action only when a host handed it a way to — a host that passes nothing gets
 * the panel it had, unchanged.
 *
 * The inherited playbook gets its own sentence, because the fix there is
 * shadowing it locally rather than an edit the store would refuse.
 *
 * The last case is the URL contract the agent page depends on: these openers
 * carry backticks, parens and quotes, so the round trip through a query string
 * has to come back byte for byte or the agent is asked something else.
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
type KnowledgeTabId = NonNullable<Parameters<typeof AgentKnowledge>[0]["tab"]>;

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function brain(): AgentBrain {
  return {
    slug: "brigado",
    name: "Brigado",
    description: "BRL market making",
    agent_md: "# Brigado",
    agent_key: "claude-code",
    when_to_consult: "",
    server_required: false,
    server_name: "",
    tools: [
      {
        name: "manage_clmm",
        server: "hummingbot",
        description: "Direct CLMM position operations",
        muted: false,
        allowlisted: false,
      },
    ],
    tools_unrestricted: true,
    skills: [
      {
        slug: "lp_rebalance",
        name: "Rebalance an LP position",
        description: "Re-centre a range",
        when_to_use: "The range drifted",
        shared: false,
        inherited: false,
        muted: false,
        references_routine: "",
        routine_ok: true,
      },
      {
        slug: "routine_cookbook",
        name: "Routine cookbook",
        description: "How to write a routine",
        when_to_use: "Authoring a routine",
        shared: true,
        inherited: true,
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
        source: "agent",
        category: "",
        muted: false,
      },
    ],
    strategies: [],
  };
}

let container: HTMLDivElement;
let root: Root;
const asked = vi.fn();

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}


function rowFor(title: string): HTMLElement {
  const row = [...container.querySelectorAll("div.group")].find((d) =>
    d.textContent?.includes(title),
  );
  if (!row) throw new Error(`No row for "${title}"`);
  return row as HTMLElement;
}

/** The row's ask action, addressed by the tooltip it carries. */
function askOn(title: string): HTMLButtonElement | null {
  return rowFor(title).querySelector<HTMLButtonElement>(
    'button[title^="Ask the agent"]',
  );
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await settle();
}

/**
 * Mount the panel on a section — with the host callback, or (`false`) without.
 *
 * The section is a prop and not a click since FEAT-117: every host draws its
 * own navigation now (the workspace's spine), so the panel has no tab strip of
 * its own to click.
 */
async function open(tab: KnowledgeTabId, withHost = true) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <AgentKnowledge
          slug="brigado"
          tab={tab}
          onAskAgent={withHost ? asked : undefined}
        />
      </QueryClientProvider>,
    );
  });
  await settle();
}

/** The sentence the row handed the host on the one click it was given. */
function saidOnce(): string {
  expect(asked).toHaveBeenCalledTimes(1);
  return asked.mock.calls[0][0] as string;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  asked.mockReset();
  getAgentBrain.mockReset().mockResolvedValue(brain());
  setAgentMute.mockReset().mockResolvedValue({});
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("asking the agent about a playbook", () => {
  it("names the playbook by its slug, and the tool that would apply it", async () => {
    await open("skills");

    await click(askOn("Rebalance an LP position")!);

    const text = saidOnce();
    expect(text).toContain("lp_rebalance");
    expect(text).toContain("manage_skill");
    expect(text).toContain("Revise your");
  });

  it("offers an inherited one the action too, pointed at shadowing it", async () => {
    await open("skills");

    // The shared library's copy: no edit button, because the store refuses the
    // write — but the conversation is exactly where the fix lives.
    expect(
      rowFor("Routine cookbook").querySelector('button[title^="Edit"]'),
    ).toBeNull();

    await click(askOn("Routine cookbook")!);

    const text = saidOnce();
    expect(text).toContain("routine_cookbook");
    expect(text).toContain("inherited from the shared library");
    expect(text).toContain('manage_skill(action="create")');
  });
});

describe("asking the agent about a routine", () => {
  it("names it the way manage_routines does, not the way the row reads", async () => {
    await open("routines");

    // The row is titled "Lp Scanner"; the agent is asked about `lp_scanner`.
    await click(askOn("Lp Scanner")!);

    const text = saidOnce();
    expect(text).toContain("lp_scanner");
    expect(text).not.toContain("Lp Scanner");
    expect(text).toContain("manage_routines");
  });
});

describe("asking the agent about a tool", () => {
  it("asks about the tool rather than offering to rewrite it", async () => {
    await open("tools");

    await click(askOn("manage_clmm")!);

    const text = saidOnce();
    expect(text).toContain("manage_clmm");
    // A tool has no body, so the sentence is not an edit-this-file one.
    expect(text).not.toContain("manage_skill");
    expect(text).toContain("when you reach for it");
  });
});

describe("a host that cannot ask", () => {
  it("gets no action at all, on any of the three tabs", async () => {
    const cases: [KnowledgeTabId, string][] = [
      ["skills", "Rebalance an LP position"],
      ["routines", "Lp Scanner"],
      ["tools", "manage_clmm"],
    ];
    for (const [tab, row] of cases) {
      await open(tab, false);
      expect(askOn(row)).toBeNull();
      // …and the row is otherwise untouched: its switch is still there.
      expect(rowFor(row).querySelector('button[role="switch"]')).toBeTruthy();
      await act(async () => root.unmount());
      container.remove();
      container = document.createElement("div");
      document.body.appendChild(container);
      root = createRoot(container);
    }
  });
});

describe("the URL the agent page carries the request in", () => {
  it("round-trips an opener through ?ask= unchanged", async () => {
    await open("skills");
    await click(askOn("Rebalance an LP position")!);
    const text = saidOnce();

    // Exactly what `AgentDetail` builds, and exactly what `AgentChatTab` reads
    // back out of it. The opener has backticks, parens and double quotes in it,
    // which is why the encode is not optional.
    expect(text).toMatch(/[`("]/);
    const url = `/?agent=${encodeURIComponent("brigado")}&ask=${encodeURIComponent(text)}`;
    const params = new URL(url, "http://dashboard.local").searchParams;

    expect(params.get("agent")).toBe("brigado");
    expect(params.get("ask")).toBe(text);
  });
});
