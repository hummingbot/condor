/**
 * What this conversation put into the world, beside the conversation
 * (FEAT-110).
 *
 * Three things this panel can only get wrong once:
 *
 *  - *deployed nothing* and *ran before Condor recorded anything* look
 *    identical on screen and mean opposite things — every conversation older
 *    than FEAT-105 is the second, and telling its reader the first is a
 *    confident lie about the one question the panel exists to answer;
 *  - the ledger is `DeploymentLedger` unchanged, so the answer to "what did this
 *    run do" reads the same in the chat as in the agent's Lab — a second table
 *    drawn here would drift from it within a release;
 *  - the tile badges the count, because a panel behind an unmarked click is a
 *    panel nobody opens.
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

import type { ConversationDeployments, DeploymentRow } from "@/lib/api";

const getConversationDeployments = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getConversationDeployments: (...a: unknown[]) =>
      getConversationDeployments(...a),
  },
  CHAT_SLUG: "condor",
}));

const { DockDeployed } = await import("./DockDeployed");
const { deployedRailItem } = await import("./deployedPanel");
const { RailButton } = await import("./WorkspaceRail");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const CONVERSATION = "a1b2c3d4e5f6";

function bot(over: Partial<DeploymentRow> = {}): DeploymentRow {
  return {
    kind: "bot",
    label: "condor-solmm",
    detail: "deployed",
    created_tick: null,
    started_at: 1_788_000_000,
    ended_at: null,
    live: true,
    pnl: 12.5,
    volume: 900,
    scope: "bot:condor-solmm-20260904-101500",
    ...over,
  };
}

function answer(over: Partial<ConversationDeployments> = {}) {
  return { deployments: [], predates_ledger: false, ...over };
}

let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;

async function render(node: React.ReactNode) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>{node}</QueryClientProvider>
      </MemoryRouter>,
    );
  });
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const panel = (conversationId = CONVERSATION) => (
  <DockDeployed
    conversationId={conversationId}
    agentSlug=""
    onClose={() => {}}
  />
);

const text = () => container.textContent ?? "";

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  qc = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchInterval: false } },
  });
  getConversationDeployments.mockReset();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  qc.clear();
});

describe("what the conversation deployed", () => {
  it("shows the bot with its money, through the Lab's own ledger", async () => {
    getConversationDeployments.mockResolvedValue(
      answer({
        deployments: [
          bot(),
          bot({
            kind: "controller",
            label: "sol",
            detail: "binance · SOL-USDC",
            scope: "ctrl:condor-solmm-20260904-101500:sol",
          }),
        ],
      }),
    );

    await render(panel());

    expect(getConversationDeployments).toHaveBeenCalledWith(CONVERSATION);
    expect(text()).toContain("condor-solmm");
    expect(text()).toContain("binance · SOL-USDC");
    // The money, formatted by the ledger rather than by this panel.
    expect(text()).toContain("12.50");
    // And the header the shared component draws, counting both rows.
    expect(text()).toContain("Deployed (2)");
  });

  it("links each row into the fleet at that record's own scope", async () => {
    getConversationDeployments.mockResolvedValue(
      answer({ deployments: [bot()] }),
    );

    await render(panel());

    const hrefs = [...container.querySelectorAll("a")].map((a) =>
      a.getAttribute("href"),
    );
    expect(hrefs).toContain("/bots?scope=bot%3Acondor-solmm-20260904-101500");
    // And the panel's own gesture: everything the chat door has made.
    expect(hrefs).toContain("/bots?scope=agent%3Acondor.chat");
  });

  it("asks nothing while there is no conversation to ask about", async () => {
    await render(panel(""));

    expect(getConversationDeployments).not.toHaveBeenCalled();
  });
});

describe("the two ways to have nothing to show", () => {
  it("says a conversation deployed nothing when it recorded that it did not", async () => {
    getConversationDeployments.mockResolvedValue(answer());

    await render(panel());

    expect(text()).toContain("hasn’t deployed anything yet");
    expect(text()).not.toContain("wasn’t recording");
  });

  it("says the record is missing for a conversation older than the ledger", async () => {
    getConversationDeployments.mockResolvedValue(
      answer({ predates_ledger: true }),
    );

    await render(panel());

    expect(text()).toContain("wasn’t recording what it did");
    expect(text()).not.toContain("hasn’t deployed anything yet");
  });
});

describe("the tile on the rail", () => {
  const item = (over: Partial<Parameters<typeof deployedRailItem>[0]> = {}) =>
    deployedRailItem({
      conversationId: CONVERSATION,
      count: 0,
      active: false,
      onToggle: () => {},
      ...over,
    });

  it("badges the count, and shows nothing for a conversation with no deeds", async () => {
    await render(<RailButton {...item({ count: 2 })} />);
    expect(text()).toContain("2");

    await act(async () => root.render(<div />));
    await render(<RailButton {...item({ count: 0 })} />);
    expect(text()).toBe("Deployed");
  });

  it("is dead before anything has been said", () => {
    expect(item({ conversationId: "" }).disabled).toBe(true);
    expect(item().disabled).toBe(false);
  });

  it("opens and closes the panel it is on", () => {
    const onToggle = vi.fn();
    item({ onToggle }).onToggle();
    expect(onToggle).toHaveBeenCalled();
    expect(item({ active: true }).active).toBe(true);
  });
});
