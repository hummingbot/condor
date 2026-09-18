/**
 * One screen in two hosts, and the two promises that make it one (FEAT-119).
 *
 * The first is that the reader gets the answer with no click: the vitals, the
 * last decision, the chart and the deployed table are the Now tab, the one a
 * bare `/agents/:slug` opens on.
 *
 * The second is that the evidence costs nothing until it is asked for:
 * `AgentFleet` pulls the entire fleet and `PlaybookView` mounts two markdown
 * editors, so a tab that is not showing has to mount *nothing* — which is what
 * the stubs below can prove and a rendered page cannot.
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

import type { WorkspaceUrlPatch } from "./workspaceUrl";

const getAgent = vi.fn();
const getAgentRuns = vi.fn();
const getStrategy = vi.fn();
const getConversationDeployments = vi.fn();
const getSessionJournal = vi.fn();
const getSessionActions = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getAgent: (...a: unknown[]) => getAgent(...a),
    getAgentRuns: (...a: unknown[]) => getAgentRuns(...a),
    getStrategy: (...a: unknown[]) => getStrategy(...a),
    getConversationDeployments: (...a: unknown[]) =>
      getConversationDeployments(...a),
    getSessionJournal: (...a: unknown[]) => getSessionJournal(...a),
    getSessionActions: (...a: unknown[]) => getSessionActions(...a),
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
const stub =
  (name: string) =>
  ({ serverName }: { serverName?: string }) => {
    mounted.push(name);
    return <div data-body={name} data-server={serverName} />;
  };

// The answers' stub keeps one readout: the last decision it was handed, under
// the real view's own `data-now-decision` hook (CORR-369).
vi.mock("@/components/agent/workspace/NowView", () => ({
  NowView: ({
    decisions,
    onShowOlderRuns,
  }: {
    decisions: { action: string }[];
    onShowOlderRuns?: () => void;
  }) => {
    mounted.push("answers");
    return (
      <div data-body="answers">
        <div data-now-decision>{decisions.at(-1)?.action ?? ""}</div>
        {onShowOlderRuns && (
          <button type="button" data-now-older-runs onClick={onShowOlderRuns} />
        )}
      </div>
    );
  },
}));
vi.mock("@/components/agent/workspace/AgentFleet", () => ({
  AgentFleet: stub("fleet"),
}));
// The Playbook's stub keeps one control: a count that names a run, which is the
// one move the band asks of the screen around it.
vi.mock("@/components/agent/workspace/PlaybookView", () => ({
  PlaybookView: ({ onOpenRun }: { onOpenRun: (run: string) => void }) => {
    mounted.push("playbook");
    return (
      <div data-body="playbook">
        <button type="button" data-open-run onClick={() => onOpenRun("s:3")} />
      </div>
    );
  },
}));
/** What the rail stub was last handed (CORR-378). */
let railProps: {
  runs: AgentRunRow[];
  selectedKey: string | null;
  strategyFilter: string | null;
  hasMore?: boolean;
  onShowMore?: () => void;
} | null = null;
vi.mock("@/components/agent/lab/RunRail", () => ({
  RunRail: (props: NonNullable<typeof railProps>) => {
    mounted.push("rail");
    railProps = props;
    return <div data-body="rail" />;
  },
}));
vi.mock("@/components/agent/lab/ExperimentDetail", () => ({
  ExperimentDetail: stub("experiment"),
}));
vi.mock("@/components/agent/session/SessionCanvasPanel", () => ({
  SessionCanvasPanel: stub("canvas"),
}));
/** What the Runs tab's market-chart stub was last handed (ARCH-427). */
let chartProps: { serverName?: string; controllerIds?: string[] } | null = null;
vi.mock("@/components/agent/session/SessionExecutors", () => ({
  SessionExecutors: (props: NonNullable<typeof chartProps>) => {
    chartProps = props;
    return stub("chart")(props);
  },
}));
vi.mock("@/components/agent/session/Snapshot", () => ({
  SnapshotDetail: stub("tick"),
}));
/** The task the delegation stub was last handed (ARCH-398). */
let delegationTask: Record<string, unknown> | null = null;
vi.mock("@/components/agent/DelegationSheet", () => ({
  DelegationSheet: ({ task }: { task: Record<string, unknown> }) => {
    mounted.push("delegation");
    delegationTask = task;
    return <div data-body="delegation" />;
  },
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
const tab = (id: string) =>
  container.querySelector<HTMLButtonElement>(`[data-pane-tab="${id}"]`)!;
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
  delegationTask = null;
  railProps = null;
  chartProps = null;
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
  getSessionJournal.mockReset().mockResolvedValue({ content: "" });
  getSessionActions.mockReset().mockResolvedValue({ actions: [] });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("what a bare address opens on", () => {
  it("is the Now tab, with no click and no spine", async () => {
    await render("/");
    expect(bodies()).toEqual(["answers"]);
    expect(tab("now").getAttribute("aria-selected")).toBe("true");
    // The spine is gone: nothing on this page is a door to another view of it.
    expect(container.querySelector("[data-spine-entry]")).toBeNull();
  });

  it("offers the side panel's tabs, and mounts none of their bodies", async () => {
    await render("/");
    const tabs = [...container.querySelectorAll("[data-pane-tab]")].map((el) =>
      el.getAttribute("data-pane-tab"),
    );
    // Money is gone: it charted the fold Fleet already charts. Detail is gone
    // too (ARCH-427): Fleet has its records, Runs its ticks and its canvas.
    expect(tabs).toEqual(["now", "runs", "fleet", "playbook"]);
    expect(mounted).not.toContain("fleet");
    expect(mounted).not.toContain("playbook");
  });

  it("opens a retired `?open=detail` on Fleet, where its records went", async () => {
    await render("/?open=detail");
    expect(bodies()).toEqual(["fleet"]);
    expect(tab("fleet").getAttribute("aria-selected")).toBe("true");
  });

  it("is the panel's layout — no index down the side, no disclosures", async () => {
    await render("/");
    expect(container.querySelector("[data-section-rail]")).toBeNull();
    expect(container.querySelector("[data-section]")).toBeNull();
  });
});

describe("the page's tabs", () => {
  it("swap the body — one section at a time, like the panel", async () => {
    await render("/");
    await click(tab("fleet"));
    expect(bodies()).toEqual(["fleet"]);
    expect(tab("fleet").getAttribute("aria-selected")).toBe("true");
  });

  it("write the tab to `?open=`, and Now clears it", async () => {
    await render("/");
    await click(tab("fleet"));
    expect(search()).toBe("open=fleet");
    await click(tab("playbook"));
    expect(search()).toBe("open=playbook");
    await click(tab("now"));
    expect(search()).toBe("");
    expect(bodies()).toEqual(["answers"]);
  });

  it("open on arrival from `?open=`", async () => {
    await render("/?open=runs");
    expect(tab("runs").getAttribute("aria-selected")).toBe("true");
    expect(bodies()).toContain("rail");
  });

  it("land a pre-tabs `?open=` set on its first section", async () => {
    await render("/?open=playbook.runs");
    expect(tab("runs").getAttribute("aria-selected")).toBe("true");
  });

  it("land a retired `?open=money` on Now", async () => {
    await render("/?open=money");
    expect(bodies()).toEqual(["answers"]);
  });

  it("count what the screen already knows, and buy nothing to say it", async () => {
    await render("/");
    expect(tab("runs").textContent).toContain("1");
    // Fleet stays blank on purpose: it headlines a fold of the whole fleet,
    // which is the query the tab exists to defer.
    expect(tab("fleet").textContent).toBe("Fleet");
    expect(mounted).not.toContain("fleet");
  });

  it("are not drawn when the agent has no strategy", async () => {
    getAgent.mockResolvedValue({ ...AGENT, strategies: [] });
    getAgentRuns.mockResolvedValue([]);
    await render("/");
    expect(container.querySelector("[data-pane-tabs]")).toBeNull();
    expect(container.textContent).toContain("no strategies yet");
  });
});

describe("which server Fleet folds (ARCH-382)", () => {
  const withServers = (pin: string, own?: string) =>
    ({
      ...AGENT,
      server_name: pin,
      strategies: [
        { ...AGENT.strategies[0], server_name: own },
        AGENT.strategies[1],
      ],
    }) as unknown as AgentDetail;
  const servers = () =>
    ["fleet"].map((name) =>
      container
        .querySelector(`[data-body="${name}"]`)
        ?.getAttribute("data-server"),
    );

  it("is the scoped strategy's own server, before its detail has loaded", async () => {
    getAgent.mockResolvedValue(withServers("pin", "own"));
    // The detail never arrives: the summary in the agent query is the answer.
    getStrategy.mockReturnValue(new Promise(() => {}));
    await render("/?strategy=brl_mm&open=fleet");
    expect(servers()).toEqual(["own"]);
  });

  it("falls back to the agent's pin when the strategy declares none", async () => {
    getAgent.mockResolvedValue(withServers("pin", ""));
    await render("/?strategy=brl_mm&open=fleet");
    expect(servers()).toEqual(["pin"]);

    getAgent.mockResolvedValue(withServers("pin"));
    act(() => root.unmount());
    root = createRoot(container);
    await render("/?strategy=brl_mm&open=fleet");
    expect(servers()).toEqual(["pin"]);
  });
});

describe("the Runs tab's market chart (ARCH-427)", () => {
  it("reads the agent's own server and streams the engine's executors", async () => {
    getAgent.mockResolvedValue({
      ...AGENT,
      server_name: "pin",
      strategies: [
        { ...AGENT.strategies[0], server_name: "own" },
        AGENT.strategies[1],
      ],
    } as unknown as AgentDetail);
    getStrategy.mockResolvedValue({
      slug: "brl_mm",
      instances: [{ agent_id: "a1", status: "running" }],
      config: {},
    } as unknown as StrategyDetail);
    await render("/?strategy=brl_mm&open=runs");
    expect(mounted).toContain("canvas");
    expect(chartProps?.serverName).toBe("own");
    expect(chartProps?.controllerIds).toEqual(["a1"]);
  });
});

describe("the tick", () => {
  it("opens over the screen rather than instead of it", async () => {
    await render("/?tick=40");
    // The tab is still mounted underneath, which is what makes closing the
    // overlay a return to the same place rather than a re-render.
    expect(bodies()).toEqual(["answers", "tick"]);
  });

  it("closes back to the screen and clears `?tick=`", async () => {
    await render("/?open=fleet&tick=40");
    await click(
      container.querySelector<HTMLButtonElement>('[aria-label="Close tick"]')!,
    );
    expect(bodies()).toEqual(["fleet"]);
    expect(search()).toBe("open=fleet");
  });
});

describe("a conversation in the Runs tab", () => {
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

describe("a delegation in the Runs tab (ARCH-398)", () => {
  const delegation = (status: string) =>
    ({
      id: "abc123",
      run_id: "d:abc123",
      kind: "delegation",
      number: 0,
      strategy_slug: "",
      title: "Check the BRL book",
      status,
      execution_mode: "consult",
      started_at: 300,
      ended_at: null,
      agent_id: "",
    }) as unknown as AgentRunRow;

  it("opens the sheet on a status it cannot colour as `unknown`", async () => {
    getAgentRuns.mockResolvedValue([RUN, delegation("exploded")]);
    await render("/?open=runs&run=d:abc123");
    expect(mounted).toContain("delegation");
    expect(delegationTask).toEqual({
      task_id: "abc123",
      agent: "brigado",
      task: "Check the BRL book",
      status: "unknown",
      kind: "consult",
      started_at: 300,
    });
  });

  it("passes a known status through, and invents no user, chat or end", async () => {
    getAgentRuns.mockResolvedValue([RUN, delegation("interrupted")]);
    await render("/?open=runs&run=d:abc123");
    expect(delegationTask?.status).toBe("interrupted");
    for (const key of ["user_id", "chat_id", "server_name", "conversation_id", "ended_at"]) {
      expect(delegationTask).not.toHaveProperty(key);
    }
  });
});

describe("the loop bar", () => {
  it("moves the scope and drops the run and the tick with it", async () => {
    await render("/?open=fleet&strategy=brl_mm&run=s:3&tick=40");

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
    // …and the tab you were on is still showing.
    expect(params.get("open")).toBe("fleet");
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

describe("a loop session in the Runs tab", () => {
  it("lists its ticks there rather than pointing somewhere else", async () => {
    getSessionJournal.mockResolvedValue({
      content: "## Ticks\n- tick#1 | 2026-09-18 14:00 | actions=0 | Held\n",
    });
    await render("/?open=runs&run=s:3");
    expect(tab("runs").getAttribute("aria-selected")).toBe("true");
    const row = container.querySelector<HTMLButtonElement>('[data-run-tick="1"]')!;
    expect(row.textContent).toContain("Held");

    // A tick opens over the tab, on that run.
    await click(row);
    const params = new URLSearchParams(search());
    expect(params.get("tick")).toBe("1");
    expect(params.get("run")).toBe("s:3");
    expect(params.get("open")).toBe("runs");
    expect(bodies()).toContain("tick");
  });

  it("stays on Runs when a loop run is picked from the rail", async () => {
    await render("/?open=runs");
    await act(async () => {
      (railProps as unknown as { onSelectRun: (r: AgentRunRow) => void }).onSelectRun(RUN);
    });
    await settle();
    const params = new URLSearchParams(search());
    expect(params.get("run")).toBe("s:3");
    expect(params.get("open")).toBe("runs");
  });
});

describe("a count in the Playbook that names a run", () => {
  const count = () =>
    container.querySelector<HTMLButtonElement>("[data-open-run]")!;

  it("moves to Runs, on that run of this strategy, in one write", async () => {
    await render("/?open=playbook");
    await click(count());

    const params = new URLSearchParams(search());
    expect(params.get("open")).toBe("runs");
    expect(params.get("run")).toBe("s:3");
    expect(params.get("strategy")).toBe("brl_mm");
    expect(bodies()).toContain("rail");
  });
});

describe("a live run, left open (CORR-369)", () => {
  const journalWith = (tick: number, action: string) => ({
    content: `## Decisions\n- **#${tick}** (12:00) ${action} -- because\n`,
  });

  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  async function renderLive() {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/"]}>
          <QueryClientProvider client={client}>
            <Host />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
    // Short of the first poll, but past the render the first read lands in
    // under fake timers.
    await advance(1_000);
  }

  async function advance(ms: number) {
    for (let i = 0; i < 10; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(i === 0 ? ms : 0);
      });
    }
  }

  it("shows the newer last decision after 10s, with no refocus or remount", async () => {
    getSessionJournal
      .mockReset()
      .mockResolvedValueOnce(journalWith(1, "hold"))
      .mockResolvedValue(journalWith(2, "deploy pmm"));
    await renderLive();
    expect(getSessionJournal).toHaveBeenCalledTimes(1);
    const decision = () =>
      container.querySelector("[data-now-decision]")!.textContent;
    expect(decision()).toBe("hold");
    const node = container.querySelector("[data-now-decision]");

    await advance(10_000);
    expect(decision()).toBe("deploy pmm");
    // The same element: the answer came from the poll, not a fresh mount.
    expect(container.querySelector("[data-now-decision]")).toBe(node);
  });

  it("reads each once per poll, however many bands observe the keys", async () => {
    await renderLive();
    await advance(10_000);
    expect(getSessionJournal).toHaveBeenCalledTimes(2);
    expect(getSessionActions).toHaveBeenCalledTimes(2);
  });
});

describe("a strategy whose runs are behind newer chats (CORR-376)", () => {
  const chats = Array.from({ length: 100 }, (_, i) => ({
    ...CHAT,
    id: `chat-${i}`,
    run_id: `c:chat-${i}`,
    started_at: 1_000 + i,
  })) as AgentRunRow[];
  const SESSION_1 = {
    ...RUN,
    id: "1",
    run_id: "s:1",
    number: 1,
    status: "stopped",
    started_at: 10,
    tick_count: 2,
    has_actions_log: false,
  } as unknown as AgentRunRow;

  beforeEach(() => {
    getStrategy.mockResolvedValue({
      slug: "brl_mm",
      instances: [],
      config: {},
      sessions: [{ number: 1 }],
    } as unknown as StrategyDetail);
  });

  it("never says it has not run, and widens the window instead", async () => {
    getAgentRuns.mockResolvedValue(chats);
    await render("/?open=runs");

    expect(container.textContent).not.toContain("This strategy has not run yet.");
    expect(container.textContent).not.toContain("This agent has no runs yet.");

    await click(container.querySelector<HTMLButtonElement>("[data-show-older-runs]")!);
    expect(getAgentRuns).toHaveBeenCalledWith("brigado", 200);
  });

  it("widens from the answer stack too", async () => {
    getAgentRuns.mockResolvedValue(chats);
    await render("/");
    await click(container.querySelector<HTMLButtonElement>("[data-now-older-runs]")!);
    expect(getAgentRuns).toHaveBeenCalledWith("brigado", 200);
  });

  it("opens `?run=s:1` once the session row is carried", async () => {
    getAgentRuns.mockResolvedValue([...chats, SESSION_1]);
    await render("/?run=s:1");

    const picker = container.querySelector<HTMLSelectElement>('select[aria-label="Run"]')!;
    expect(picker.value).toBe("s:1");
    // TickSpine is mounted (an empty journal draws its empty state).
    expect(
      container.querySelector('[data-testid="tick-spine"], [data-spine-empty]'),
    ).not.toBeNull();
    expect(container.querySelector("[data-now-older-runs]")).toBeNull();
  });
});

describe("widening the Runs window (CORR-378)", () => {
  const chats = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      ...CHAT,
      id: `chat-${i}`,
      run_id: `c:chat-${i}`,
      started_at: 1_000 + i,
    })) as AgentRunRow[];

  it("keeps the run selected while the wider page loads", async () => {
    getAgentRuns.mockResolvedValue([RUN, ...chats(100)]);
    await render("/?open=runs&run=s:3");

    expect(railProps!.hasMore).toBe(true);
    const before = railProps!.selectedKey;
    expect(before).toBe("brl_mm:s:3");

    let resolveWide!: (rows: AgentRunRow[]) => void;
    getAgentRuns.mockImplementation(
      (_slug: string, limit: number) =>
        limit === 200
          ? new Promise<AgentRunRow[]>((r) => {
              resolveWide = r;
            })
          : Promise.resolve([RUN, ...chats(100)]),
    );
    await act(async () => {
      railProps!.onShowMore!();
    });
    await settle();

    expect(getAgentRuns).toHaveBeenLastCalledWith("brigado", 200);
    expect(bodies()).toContain("rail");
    expect(container.textContent).not.toContain("This agent has no runs yet.");
    expect(railProps!.selectedKey).toBe(before);
    expect(railProps!.runs).toHaveLength(101);

    const wide = [RUN, ...chats(200)];
    await act(async () => {
      resolveWide(wide);
    });
    await settle();

    expect(railProps!.runs).toHaveLength(201);
    expect(railProps!.selectedKey).toBe(before);
    expect(railProps!.hasMore).toBe(true);
  });
});

describe("what the Runs rail is handed (CORR-397)", () => {
  it("does not filter the Runs rail on a slug the agent does not own", async () => {
    await render("/?open=runs&strategy=ghost");
    expect(mounted).toContain("rail");
    expect(railProps?.strategyFilter).toBeNull();
  });

  it("filters the Runs rail on a strategy the agent owns", async () => {
    await render("/?open=runs&strategy=sol_lp");
    expect(railProps?.strategyFilter).toBe("sol_lp");
  });
});

describe("a loop that is on time (PERF-372)", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("re-renders no band while the seconds pass", async () => {
    getStrategy.mockResolvedValue({
      slug: "brl_mm",
      instances: [
        {
          agent_id: "a1",
          status: "running",
          last_tick_at: Math.floor(Date.now() / 1000),
          frequency_sec: 60,
        },
      ],
      config: {},
    } as unknown as StrategyDetail);
    // Only the interval and the clock are faked, and before the render so the
    // screen's clocks are the fake ones: `settle` runs on real `setTimeout(0)`.
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "Date"] });
    await render("/?strategy=brl_mm&run=s:3&open=playbook");
    expect(bodies()).toContain("playbook");
    const count = (name: string) => mounted.filter((n) => n === name).length;
    const answers = count("answers");
    const playbook = count("playbook");

    for (let i = 0; i < 3; i++) {
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
    }

    expect(count("answers")).toBe(answers);
    expect(count("playbook")).toBe(playbook);
  });
});

describe("the strategy detail poll (PERF-374)", () => {
  const idle = { slug: "brl_mm", instances: [], config: {} } as unknown as StrategyDetail;
  const live = {
    slug: "brl_mm",
    instances: [
      { agent_id: "a1", status: "running", last_tick_at: 0, frequency_sec: 60 },
    ],
    config: {},
  } as unknown as StrategyDetail;

  let client: QueryClient;

  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  /** Let `ms` of polling elapse, flushing the fetches it schedules. */
  async function elapse(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  async function mount() {
    client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/?strategy=brl_mm"]}>
          <QueryClientProvider client={client}>
            <Host />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
    await elapse(0);
    await elapse(0);
  }

  it("reads an idle strategy once and then stops asking", async () => {
    getStrategy.mockResolvedValue(idle);
    await mount();
    expect(getStrategy).toHaveBeenCalledTimes(1);

    await elapse(30_000);
    expect(getStrategy).toHaveBeenCalledTimes(1);
  });

  it("keeps the 5s cadence while an engine is up", async () => {
    getStrategy.mockResolvedValue(live);
    await mount();
    await elapse(30_000);
    expect(getStrategy.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("re-arms the poll when a lifecycle invalidation finds a running instance", async () => {
    getStrategy.mockResolvedValue(idle);
    await mount();
    expect(getStrategy).toHaveBeenCalledTimes(1);

    getStrategy.mockResolvedValue(live);
    await act(async () => {
      await client.invalidateQueries({ queryKey: ["strategy", "brigado", "brl_mm"] });
    });
    await elapse(0);
    expect(getStrategy).toHaveBeenCalledTimes(2);

    await elapse(10_000);
    expect(getStrategy.mock.calls.length).toBeGreaterThanOrEqual(4);
  });

  it("goes quiet once the last instance is gone", async () => {
    getStrategy.mockResolvedValue(live);
    await mount();
    getStrategy.mockResolvedValue(idle);
    await elapse(5_000);
    const afterStop = getStrategy.mock.calls.length;
    expect(afterStop).toBeGreaterThanOrEqual(2);

    await elapse(20_000);
    expect(getStrategy).toHaveBeenCalledTimes(afterStop);
  });
});

describe("the side panel's variant", () => {
  /** The pane: one section at a time, its tab held by the host. */
  function PaneHost({
    initial,
    onMove,
  }: {
    initial: string;
    onMove: (section: string, patch?: WorkspaceUrlPatch) => void;
  }) {
    const [params, setParams] = useSearchParams();
    const adapter = useWorkspaceUrl(params, setParams);
    return (
      <AgentRunScreen
        slug="brigado"
        adapter={adapter}
        variant="pane"
        section={initial as "now"}
        onSection={onMove}
      />
    );
  }

  async function renderPane(
    initial: string,
    onMove: (section: string, patch?: WorkspaceUrlPatch) => void = () => {},
  ) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={["/"]}>
          <QueryClientProvider client={client}>
            <PaneHost initial={initial} onMove={onMove} />
          </QueryClientProvider>
        </MemoryRouter>,
      );
    });
    await settle();
  }

  it("shows Now and nothing else", async () => {
    await renderPane("now");
    expect(bodies()).toEqual(["answers"]);
    expect(container.querySelector("[data-section-rail]")).toBeNull();
    expect(container.querySelector("[data-section]")).toBeNull();
    expect(tab("now").getAttribute("aria-selected")).toBe("true");
  });

  it("mounts only the open tab's body", async () => {
    await renderPane("fleet");
    expect(bodies()).toEqual(["fleet"]);
    expect(mounted).not.toContain("answers");
    expect(mounted).not.toContain("playbook");
  });

  it("hands a tab click to the host", async () => {
    const onMove = vi.fn();
    await renderPane("now", onMove);
    await click(tab("fleet"));
    expect(onMove.mock.calls[0][0]).toBe("fleet");
  });

  it("sends a run named from the Playbook to the Runs tab, in one move", async () => {
    const onMove = vi.fn();
    await renderPane("playbook", onMove);
    await click(container.querySelector<HTMLButtonElement>("[data-open-run]")!);
    expect(onMove).toHaveBeenCalledWith("runs", { strategy: "brl_mm", run: "s:3" });
  });
});
