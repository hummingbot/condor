/**
 * One screen, and the two promises that make it one (FEAT-119).
 *
 * The first is that the reader gets the answer with no click: the vitals, the
 * last decision, the chart and the deployed table are on `/agents/:slug`, and
 * nothing on the page is a link to another view of itself.
 *
 * The second is that the evidence costs nothing until it is asked for. That is
 * the whole difference between this screen and the longer scroll it could have
 * been: `AgentFleet` pulls the entire fleet and `PlaybookView` mounts two
 * markdown editors, so a closed disclosure has to mount *nothing* — which is
 * what the stubs below can prove and a rendered page cannot.
 *
 * The bodies are stubbed. Each has its own tests and each fetches its own
 * world; what is under test here is what is on screen, what is mounted, and
 * what the URL says afterwards.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation, useSearchParams } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentDetail, AgentRunRow, StrategyDetail } from "@/lib/api";
import { AGENT_SECTIONS_KEY } from "@/lib/sessionState";

const getAgent = vi.fn();
const getAgentRuns = vi.fn();
const getStrategy = vi.fn();
const getConversationDeployments = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: (...a: unknown[]) => getAgent(...a),
    getAgentRuns: (...a: unknown[]) => getAgentRuns(...a),
    getStrategy: (...a: unknown[]) => getStrategy(...a),
    getConversationDeployments: (...a: unknown[]) =>
      getConversationDeployments(...a),
    getSessionJournal: () => Promise.resolve({ content: "" }),
    getSessionActions: () => Promise.resolve({ actions: [] }),
    getSessionReport: () => Promise.resolve({ report: null }),
    getStrategySessionExecutors: () => Promise.resolve({ executors: [] }),
  },
  CHAT_SLUG: "condor",
}));

/**
 * One stub per body, recording that it was mounted at all.
 *
 * `mounted` is the assertion behind "a closed disclosure mounts nothing": the
 * expensive two are `fleet` and `playbook`, and their queries only exist
 * because the component does.
 */
const mounted: string[] = [];
const stub = (name: string) => () => {
  mounted.push(name);
  return <div data-body={name} />;
};

vi.mock("@/components/agent/workspace/NowView", () => ({
  NowView: stub("answers"),
}));
vi.mock("@/components/agent/workspace/MoneyView", () => ({
  MoneyView: stub("money"),
}));
vi.mock("@/components/agent/workspace/AgentFleet", () => ({
  AgentFleet: stub("fleet"),
}));
vi.mock("@/components/agent/workspace/PlaybookView", () => ({
  PlaybookView: stub("playbook"),
}));
vi.mock("@/components/agent/lab/RunRail", () => ({ RunRail: stub("rail") }));
vi.mock("@/components/agent/lab/RunOverview", () => ({
  RunOverview: stub("detail"),
  ExperimentDetail: stub("experiment"),
}));
vi.mock("@/components/agent/AgentSessionContent", () => ({
  SnapshotDetail: stub("tick"),
}));
vi.mock("@/components/agent/DelegationSheet", () => ({
  DelegationSheet: stub("delegation"),
}));

const { AgentRunScreen } = await import("./AgentRunScreen");
const { useWorkspaceUrl } = await import("./workspaceUrl");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const AGENT = {
  slug: "brigado",
  name: "Brigado",
  description: "",
  agent_md: "",
  agent_key: "claude-code",
  tools: [],
  when_to_consult: "",
  server_required: false,
  server_name: "brigado_2",
  strategies: [
    { slug: "brl_mm", name: "BRL MM", status: "running", instances: [] },
    { slug: "sol_lp", name: "SOL LP", status: "stopped", instances: [] },
  ],
} as unknown as AgentDetail;

const RUN = {
  id: "session_3",
  run_id: "s:3",
  kind: "session",
  number: 3,
  strategy_slug: "brl_mm",
  title: "Session 3",
  status: "running",
  started_at: 200,
  ended_at: 0,
  agent_id: "a1",
} as unknown as AgentRunRow;

const CHAT = {
  id: "7f3a",
  run_id: "c:7f3a",
  kind: "conversation",
  number: 0,
  strategy_slug: "",
  strategy_name: "",
  title: "Deploy a PMM on SOL",
  status: "done",
  started_at: 100,
  ended_at: 150,
  tick_count: 0,
} as unknown as AgentRunRow;

let container: HTMLDivElement;
let root: Root;
/** Where the router is, so a control's effect on the URL can be read. */
let at = "";

async function settle() {
  for (let i = 0; i < 10; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** The page, minus the two states only a page can be in. */
function Host({ header }: { header?: boolean }) {
  const [params, setParams] = useSearchParams();
  const location = useLocation();
  // In an effect, not in render: recording where the router went is a side
  // effect, and the rule that forbids one in render holds in a harness too.
  useEffect(() => {
    at = `${location.pathname}${location.search}`;
  }, [location]);
  const adapter = useWorkspaceUrl(params, setParams);
  return (
    <AgentRunScreen
      slug="brigado"
      adapter={adapter}
      header={
        header
          ? ({ strategy }) => <div data-header={strategy?.slug ?? "none"} />
          : undefined
      }
    />
  );
}

async function render(entry = "/", header = false) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={[entry]}>
        <QueryClientProvider client={client}>
          <Host header={header} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await settle();
}

const bodies = () =>
  Array.from(container.querySelectorAll("[data-body]")).map((el) =>
    el.getAttribute("data-body"),
  );
const section = (id: string) =>
  container.querySelector<HTMLButtonElement>(`[data-section="${id}"]`)!;
const rail = (id: string) =>
  container.querySelector<HTMLButtonElement>(`[data-rail="${id}"]`)!;
const search = () => at.split("?")[1] ?? "";

async function click(el: HTMLElement) {
  await act(async () => {
    el.click();
  });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  at = "";
  mounted.length = 0;
  localStorage.clear();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgent.mockReset().mockResolvedValue(AGENT);
  getAgentRuns.mockReset().mockResolvedValue([RUN]);
  getStrategy
    .mockReset()
    .mockResolvedValue({ slug: "brl_mm", instances: [], config: {} } as unknown as StrategyDetail);
  getConversationDeployments
    .mockReset()
    .mockResolvedValue({ deployments: [], predates_ledger: false });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("what a bare address opens on", () => {
  it("is the answer stack, with no click and no spine", async () => {
    await render("/");
    expect(bodies()).toEqual(["answers"]);
    // The spine is gone: nothing on this page is a door to another view of it.
    expect(container.querySelector("[data-spine-entry]")).toBeNull();
  });

  it("offers all five disclosures, and mounts none of them", async () => {
    await render("/");
    for (const id of ["runs", "detail", "money", "fleet", "playbook"]) {
      expect(section(id).getAttribute("aria-expanded")).toBe("false");
    }
    // The whole argument for a disclosure over a band: the fleet browser's
    // query does not exist until the reader asks for it.
    expect(mounted).not.toContain("fleet");
    expect(mounted).not.toContain("playbook");
  });
});

describe("the disclosures", () => {
  it("open in place — the answer stack stays on screen", async () => {
    await render("/");
    await click(section("money"));
    expect(bodies()).toEqual(["answers", "money"]);
  });

  it("write which are open, and reading down the page replaces", async () => {
    await render("/");
    await click(section("money"));
    expect(search()).toBe("open=money");

    await click(section("fleet"));
    expect(new URLSearchParams(search()).get("open")).toBe("money.fleet");
  });

  it("come off the URL entirely when the last one shuts", async () => {
    await render("/?open=money");
    await click(section("money"));
    expect(search()).toBe("");
    expect(bodies()).toEqual(["answers"]);
  });

  it("open on arrival from `?open=`, in the order the screen draws them", async () => {
    await render("/?open=runs.money");
    expect(section("runs").getAttribute("aria-expanded")).toBe("true");
    expect(section("money").getAttribute("aria-expanded")).toBe("true");
    expect(section("fleet").getAttribute("aria-expanded")).toBe("false");
    expect(bodies()).toEqual(["answers", "rail", "money"]);
  });

  it("come back to what this browser last had open, with no `?open=`", async () => {
    localStorage.setItem(AGENT_SECTIONS_KEY, JSON.stringify(["fleet"]));
    await render("/");
    expect(section("fleet").getAttribute("aria-expanded")).toBe("true");
  });

  it("let the URL win over what the browser recorded", async () => {
    localStorage.setItem(AGENT_SECTIONS_KEY, JSON.stringify(["fleet"]));
    await render("/?open=money");
    expect(section("money").getAttribute("aria-expanded")).toBe("true");
    expect(section("fleet").getAttribute("aria-expanded")).toBe("false");
  });
});

describe("the tick", () => {
  it("opens over the screen rather than instead of it", async () => {
    await render("/?tick=40");
    // The stack is still mounted underneath, which is what makes closing the
    // overlay a return to the same scroll position rather than a re-render.
    expect(bodies()).toEqual(["answers", "tick"]);
  });

  it("closes back to the screen and clears `?tick=`", async () => {
    await render("/?open=money&tick=40");
    await click(
      container.querySelector<HTMLButtonElement>('[aria-label="Close tick"]')!,
    );
    expect(bodies()).toEqual(["answers", "money"]);
    expect(search()).toBe("open=money");
  });
});

describe("a conversation in the Runs disclosure", () => {
  it("says what that conversation deployed, not just where to read it", async () => {
    // The gap FEAT-118 opened: the row said "read it in the chat" and stopped,
    // which left the one question this page exists for unanswered for a quarter
    // of an agent's runs (FEAT-110, FEAT-111).
    getAgentRuns.mockResolvedValue([RUN, CHAT]);
    getConversationDeployments.mockResolvedValue({
      deployments: [
        {
          kind: "controller",
          label: "pmm_1",
          detail: "binance·SOL-USDC",
          created_tick: null,
          started_at: 120,
          ended_at: null,
          live: true,
          pnl: 12,
          volume: 400,
          scope: "ctrl:pmm_1",
        },
      ],
      predates_ledger: false,
    });

    await render("/?open=runs&run=c:7f3a");
    expect(getConversationDeployments).toHaveBeenCalledWith("7f3a");
    expect(container.textContent).toContain("Deploy a PMM on SOL");
    expect(container.textContent).toContain("pmm_1");
  });

  it("tells `deployed nothing` apart from `ran before we recorded it`", async () => {
    getAgentRuns.mockResolvedValue([RUN, CHAT]);
    getConversationDeployments.mockResolvedValue({
      deployments: [],
      predates_ledger: true,
    });

    await render("/?open=runs&run=c:7f3a");
    expect(container.textContent).toContain("before Condor recorded");
    // Not the ledger's own empty case, which would say it deployed nothing.
    expect(container.querySelector("table")).toBeNull();
  });
});

describe("the loop bar", () => {
  it("moves the scope and drops the run and the tick with it", async () => {
    await render("/?open=money&strategy=brl_mm&run=s:3&tick=40");

    const picker = container.querySelector<HTMLSelectElement>("select")!;
    await act(async () => {
      picker.value = "sol_lp";
      picker.dispatchEvent(new Event("change", { bubbles: true }));
    });
    await settle();

    const params = new URLSearchParams(search());
    expect(params.get("strategy")).toBe("sol_lp");
    // A run of the loop you just left is not a run of the one you picked.
    expect(params.get("run")).toBeNull();
    expect(params.get("tick")).toBeNull();
    // …and what you had open is still open.
    expect(params.get("open")).toBe("money");
  });
});

describe("the header slot", () => {
  it("is given the strategy this component resolved", async () => {
    // The page's loop controls act on it, and resolving the scope twice is two
    // answers to one question.
    await render("/", true);
    expect(
      container.querySelector("[data-header]")!.getAttribute("data-header"),
    ).toBe("brl_mm");
  });
});

describe("the index down the side (FEAT-120)", () => {
  it("names the answer stack and all five bands, before any scrolling", async () => {
    // The gap it fills: the bands sit under a chart and a decision and a
    // ledger, so what is *on* this screen cost a scroll to find out.
    await render("/");
    expect(rail("now")).not.toBeNull();
    for (const id of ["runs", "detail", "money", "fleet", "playbook"]) {
      expect(rail(id).getAttribute("aria-expanded")).toBe("false");
    }
  });

  it("carries what the screen already knows, and buys nothing to say it", async () => {
    await render("/");
    expect(rail("runs").textContent).toContain("1");
    // Money and Fleet stay blank on purpose: both headline a fold of the whole
    // fleet, which is the query their disclosure exists to defer, and the
    // cheaper numbers to hand are a different quantity (FEAT-109).
    expect(rail("money").textContent).toBe("Money");
    expect(rail("fleet").textContent).toBe("Fleet");
    expect(mounted).not.toContain("fleet");
  });

  it("is the same button as the band's own header", async () => {
    await render("/");
    await click(rail("money"));
    expect(bodies()).toEqual(["answers", "money"]);
    expect(search()).toBe("open=money");
    expect(section("money").getAttribute("aria-expanded")).toBe("true");

    // Including on the way back: one action, two places to reach it, so the
    // URL never depends on which of the two the reader used.
    await click(rail("money"));
    expect(bodies()).toEqual(["answers"]);
    expect(search()).toBe("");
  });

  it("keeps `?open=` a set — opening a second band does not close the first", async () => {
    await render("/?open=money");
    await click(rail("playbook"));
    expect(new URLSearchParams(search()).get("open")).toBe("money.playbook");
    expect(bodies()).toEqual(["answers", "money", "playbook"]);
  });

  it("marks what is open, so the rail says where the reader is", async () => {
    await render("/?open=fleet");
    expect(rail("fleet").getAttribute("aria-expanded")).toBe("true");
    expect(rail("runs").getAttribute("aria-expanded")).toBe("false");
    // The band it points at is addressable, which is what the scroll uses.
    expect(rail("fleet").getAttribute("aria-controls")).toBe("section-fleet");
    expect(container.querySelector("#section-fleet")).not.toBeNull();
  });

  it("has nothing to index when the agent has no strategy", async () => {
    getAgent.mockResolvedValue({ ...AGENT, strategies: [] });
    getAgentRuns.mockResolvedValue([]);
    await render("/");
    expect(container.querySelector("[data-section-rail]")).toBeNull();
    expect(container.textContent).toContain("no strategies yet");
  });
});
