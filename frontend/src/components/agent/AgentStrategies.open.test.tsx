/**
 * A strategy card opens where its host can hold it.
 *
 * Clicking one used to always `navigate`, on the stated theory that "starting a
 * loop with real money belongs on the page that owns its config". But the guard
 * on starting a loop is the start dialog and its confirmation, not the width of
 * the window — and the rule cost you the whole workspace every time an agent
 * named a strategy you wanted to look at: the chat, the session tabs and the
 * pane, replaced by a page whose headline numbers were the ones you had just
 * been reading on the card.
 *
 * So: given a host with somewhere to put it, the card hands it over. Given none
 * — the agent's own page — it navigates as before. Both halves are pinned here,
 * because the failure mode of the fix is a page that silently stops navigating.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { AgentStrategies } from "./AgentStrategies";

const STRATEGY = {
  slug: "brl_mm",
  name: "BRL MM",
  description: "Brazilian market making",
  status: "idle",
  agent_id: "",
  session_count: 0,
  experiment_count: 2,
  tick_count: 0,
  daily_pnl: 0,
  total_pnl: 0,
  total_volume: 0,
  open_positions: 0,
  instances: [],
};

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: vi.fn(async () => ({
      slug: "brigado",
      name: "Brigado",
      description: "",
      agent_md: "",
      agent_key: "",
      tools: [],
      when_to_consult: "",
      server_required: false,
      server_name: "",
      strategies: [STRATEGY],
    })),
  },
}));

let host: HTMLDivElement;
let root: Root;
let path = "";
let search = "";

/**
 * Reports whatever route the router is on, so navigation is observable.
 *
 * The write is in an effect rather than in render: assigning to an outer
 * binding during render is a side effect, and the rule that forbids it is the
 * same one that makes a component safe to re-render twice.
 */
function Probe() {
  const { pathname, search: query } = useLocation();
  useEffect(() => {
    path = pathname;
    search = query;
  }, [pathname, query]);
  return null;
}

function client() {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
}

async function renderList(onOpenStrategy?: (slug: string) => void) {
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/"]}>
        <Probe />
        <QueryClientProvider client={client()}>
          <AgentStrategies slug="brigado" onOpenStrategy={onOpenStrategy} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

/** The house idiom for letting react-query resolve before asserting. */
async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

function card(): HTMLButtonElement {
  const found = [...host.querySelectorAll("button")].find((b) =>
    b.textContent?.includes("BRL MM"),
  );
  if (!found) throw new Error("no strategy card rendered");
  return found as HTMLButtonElement;
}

beforeEach(() => {
  host = document.createElement("div");
  document.body.appendChild(host);
  root = createRoot(host);
  path = "";
  search = "";
});

afterEach(() => {
  act(() => root.unmount());
  host.remove();
});

it("hands the strategy to a host that has somewhere to put it", async () => {
  const opened: string[] = [];
  await renderList((s) => opened.push(s));

  await act(async () => {
    card().click();
  });

  expect(opened).toEqual(["brl_mm"]);
  // And crucially: the workspace stayed where it was.
  expect(path).toBe("/");
});

// A page navigates to the agent workspace's *runs* view since FEAT-103 (the
// Lab, before it was folded in): a card summarises what a loop has been doing,
// and its runs are what it has been doing. The workbench — where you operate it
// — is one click further, on the workspace's own spine.
it("still navigates when the host is a page", async () => {
  await renderList(undefined);

  await act(async () => {
    card().click();
  });

  expect(path).toBe("/agents/brigado");
  expect(search).toBe("?view=runs&strategy=brl_mm");
});

it("counts dry runs on the card, which book no PnL to be seen by", async () => {
  await renderList(() => {});
  // The only trace a dry run leaves on a summary — every money column is 0 —
  // so it is a chip of its own rather than a superscript on the session count.
  expect(card().textContent).toContain("2 dry");
});
