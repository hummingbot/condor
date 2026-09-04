/**
 * The workspace is a shell over bodies, and this pins the shell (FEAT-117).
 *
 * It used to be `pages/AgentWorkspace.tsx`, welded to `/agents/:slug` by a
 * `useParams()` for the slug and a `useSearchParams()` for everything else. The
 * chat's pane mounts the same component from the home's query string now, so
 * what has to hold is that nothing in here knows which host it is in: the four
 * parameters arrive through an adapter, every other parameter on the string is
 * somebody else's and must survive, and the spine, the loop bar and the body
 * switch behave the same either way.
 *
 * The bodies are stubbed. Each has its own tests and each fetches its own
 * world; what is under test here is which one is chosen and what the URL says
 * afterwards.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import {
  MemoryRouter,
  useLocation,
  useSearchParams,
} from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentDetail, AgentRunRow, StrategyDetail } from "@/lib/api";

const getAgent = vi.fn();
const getAgentRuns = vi.fn();
const getStrategy = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: (...a: unknown[]) => getAgent(...a),
    getAgentRuns: (...a: unknown[]) => getAgentRuns(...a),
    getStrategy: (...a: unknown[]) => getStrategy(...a),
    getRoutineInstances: () => Promise.resolve([]),
    getSessionJournal: () => Promise.resolve({ decisions: [] }),
    getSessionActions: () => Promise.resolve({ actions: [] }),
    getStrategySessionExecutors: () => Promise.resolve({ executors: [] }),
  },
  CHAT_SLUG: "condor",
}));

/** One stub per body, saying its own name and nothing else. */
const stub = (name: string) => () => <div data-body={name} />;
vi.mock("@/components/agent/AgentKnowledge", () => ({
  AgentKnowledge: (props: { tab: string }) => (
    <div data-body="knowledge" data-tab={props.tab} />
  ),
}));
vi.mock("@/components/agent/workspace/NowView", () => ({
  NowView: stub("now"),
}));
vi.mock("@/components/agent/workspace/MoneyView", () => ({
  MoneyView: stub("money"),
}));
vi.mock("@/components/agent/workspace/AgentFleet", () => ({
  AgentFleet: stub("fleet"),
}));
vi.mock("@/components/agent/StrategyWorkbench", () => ({
  StrategyWorkbench: stub("playbook"),
}));
vi.mock("@/components/agent/lab/RunRail", () => ({ RunRail: stub("runs") }));
vi.mock("@/components/agent/lab/RunOverview", () => ({
  RunOverview: stub("run-overview"),
  ExperimentDetail: stub("experiment"),
}));
vi.mock("@/components/agent/AgentSessionContent", () => ({
  SnapshotDetail: stub("tick"),
}));
vi.mock("@/components/agent/DelegationSheet", () => ({
  DelegationSheet: stub("delegation"),
}));
vi.mock("@/components/routines/ReportBrowser", () => ({
  ReportBrowser: stub("reports"),
}));

const { AgentWorkspaceBody } = await import("./AgentWorkspaceBody");
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

/**
 * The body, bound to a router exactly as either host binds it — which is the
 * point: this harness *is* the host, and it is neither the page nor the pane.
 */
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
    <AgentWorkspaceBody
      slug="brigado"
      adapter={adapter}
      header={
        header
          ? ({ strategy }) => <div data-header={strategy?.slug ?? "none"} />
          : undefined
      }
      onAskAgent={() => {}}
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

const body = () => container.querySelector("[data-body]")?.getAttribute("data-body");
const spine = (id: string) =>
  container.querySelector<HTMLButtonElement>(`[data-spine-entry="${id}"]`)!;
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
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  getAgent.mockReset().mockResolvedValue(AGENT);
  getAgentRuns.mockReset().mockResolvedValue([RUN]);
  getStrategy
    .mockReset()
    .mockResolvedValue({ slug: "brl_mm", instances: [], config: {} } as unknown as StrategyDetail);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("what a bare address opens on", () => {
  it("is Now — the last decision, not an AGENT.md dump", async () => {
    await render("/");
    expect(body()).toBe("now");
    expect(spine("now").getAttribute("aria-current")).toBe("page");
  });

  it("is Now in a host that has other parameters of its own", async () => {
    // The pane's address. `?panel=` and `?who=` are the home's, not this
    // component's, and a bare `?panel=agent` is still a bare workspace.
    await render("/?panel=agent&who=brigado");
    expect(body()).toBe("now");
  });
});

describe("the spine", () => {
  it("swaps the body and writes the section", async () => {
    await render("/");

    await click(spine("money"));
    expect(body()).toBe("money");
    expect(search()).toBe("view=money");

    await click(spine("fleet"));
    expect(body()).toBe("fleet");
    expect(search()).toBe("view=fleet");
  });

  it("reaches the Being sections, which render the same component", async () => {
    await render("/");

    await click(spine("skills"));
    expect(body()).toBe("knowledge");
    expect(
      container.querySelector("[data-body]")!.getAttribute("data-tab"),
    ).toBe("skills");
  });

  it("never spells out the default section", async () => {
    await render("/?view=money");
    await click(spine("now"));
    expect(search()).toBe("");
  });

  it("leaves the host's own parameters alone", async () => {
    // The whole reason the pane can spend this grammar on the home's string.
    await render("/?panel=agent&who=brigado&desk=execution");
    await click(spine("money"));

    const params = new URLSearchParams(search());
    expect(params.get("panel")).toBe("agent");
    expect(params.get("who")).toBe("brigado");
    expect(params.get("desk")).toBe("execution");
    expect(params.get("view")).toBe("money");
  });
});

describe("the loop bar", () => {
  it("moves the scope and drops the run and the tick with it", async () => {
    await render("/?view=money&strategy=brl_mm&run=s:3&tick=40");

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
    // …and the section you were reading is still the section you are reading.
    expect(params.get("view")).toBe("money");
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

  it("is simply absent for a host that draws its own bar", async () => {
    await render("/");
    expect(container.querySelector("[data-header]")).toBeNull();
  });
});
