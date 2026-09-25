/**
 * The strategy pane has a way back to the agent it belongs to (CORR-423).
 *
 * Its only exit used to be the right edge's fold glyph, titled "Close", which
 * reads as "close the panel" — not "back to Brigado". The bar now starts with
 * a back arrow that names the agent. The harness wires `onClose` the way
 * `AgentChatTab` does, so what is pinned is the whole trip: arrow → the pane is
 * the agent panel of the strategy's own agent.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { PaneView } from "./paneUrl";

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: () =>
      Promise.resolve({ slug: "brigado", name: "Brigado", strategies: [] }),
    getStrategy: () => Promise.resolve({ slug: "grid", name: "SOL grid" }),
  },
  CHAT_SLUG: "condor",
}));

// The run screen is not under test; the bar above it is.
vi.mock("@/components/agent/workspace/AgentRunScreen", () => ({
  AgentRunScreen: () => <div data-screen />,
}));

const { StrategySheet } = await import("./StrategySheet");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let seen: PaneView = null;

/** `AgentChatTab`'s pane, reduced to the strategy sheet and its `onClose`. */
function Chat({ panelSlug }: { panelSlug: string }) {
  const [pane, setPane] = useState<PaneView>({
    kind: "strategy",
    agentSlug: "brigado",
    strategySlug: "grid",
  });
  useEffect(() => {
    seen = pane;
  }, [pane]);
  if (pane?.kind !== "strategy") return <div data-agent-panel />;
  return (
    <StrategySheet
      pane={pane}
      onPane={setPane}
      onClose={() =>
        setPane({
          kind: "agent",
          ...(pane.agentSlug === panelSlug ? {} : { slug: pane.agentSlug }),
        })
      }
    />
  );
}

async function render(panelSlug: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <Chat panelSlug={panelSlug} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  // Let the two queries settle so the bar has the agent's name.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

function back(): HTMLButtonElement {
  const el = container.querySelector<HTMLButtonElement>("[data-strategy-back]");
  expect(el).not.toBeNull();
  return el!;
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  seen = null;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("StrategySheet back control", () => {
  it("names the agent in the arrow's tooltip", async () => {
    await render("brigado");
    expect(back().title).toBe("Back to Brigado");
    expect(container.textContent).toContain("SOL grid");
  });

  it("the arrow puts the agent panel back in the pane", async () => {
    await render("brigado");
    await act(async () => back().click());
    expect(seen).toEqual({ kind: "agent" });
    expect(container.querySelector("[data-agent-panel]")).not.toBeNull();
  });

  it("goes back to the strategy's own agent, not the conversation's", async () => {
    await render("condor");
    await act(async () => back().click());
    expect(seen).toEqual({ kind: "agent", slug: "brigado" });
  });
});
