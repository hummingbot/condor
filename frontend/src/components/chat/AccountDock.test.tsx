/**
 * The desk beside the conversation (FEAT-094).
 *
 * What is pinned here is the part of the design that is not visible in a
 * screenshot: that a tab nobody opened costs *nothing* — no portfolio walk, no
 * bots call, no executors call, no socket channel — that opening one section
 * does not open the other, that the sections survive a reload, and that the
 * whole desk lives in the *workspace pane* rather than in a column of its own,
 * which is what makes it exclusive with the agent panel.
 *
 * The panels' own contents are tested in DockExecution.test.tsx; here they are
 * stubbed, because the question is the shell.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import {
  QueryClient,
  QueryClientProvider,
  useQuery,
} from "@tanstack/react-query";
import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { LiveFleetOwner } from "@/hooks/useLiveLoops";
import type { LiveLoop } from "@/lib/agent-attribution";
import { ACCOUNT_DOCK_KEY, DESK_SPLIT_KEY } from "@/lib/sessionState";

/** Every call the three panels can make; none of them may fire while closed. */
const getPortfolio = vi.fn();
const getPortfolioHistory = vi.fn();
const getBots = vi.fn();
const getExecutors = vi.fn();
const getFleetMap = vi.fn();
const getAgents = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getPortfolio: (...a: unknown[]) => getPortfolio(...a),
    getPortfolioHistory: (...a: unknown[]) => getPortfolioHistory(...a),
    getBots: (...a: unknown[]) => getBots(...a),
    getExecutors: (...a: unknown[]) => getExecutors(...a),
    // The execution panel reads its fleet through `useFleetData` (FEAT-114),
    // under the same keys `/bots` holds.
    getExecutorsPage: async (...a: unknown[]) => ({
      executors: (await getExecutors(...a)) ?? [],
      next_cursor: null,
    }),
    // The Loops section reads through `useLiveLoops` and the shared agent
    // roster (FEAT-1xx) — both empty by default, below, so a test that never
    // touches Loops sees exactly the "nothing looping" shell it always did.
    getFleetMap: (...a: unknown[]) => getFleetMap(...a),
    getAgents: (...a: unknown[]) => getAgents(...a),
    getRates: () => Promise.resolve({ rates: {} }),
  },
}));

/** Which channels the open panels hold — the other half of "closed is free". */
const subscribed = new Set<string>();
vi.mock("@/hooks/useWebSocket", () => ({
  useCondorWebSocket: (channels: string[], server: string | null) => {
    for (const ch of server ? channels : []) subscribed.add(ch);
    return {};
  },
}));

/** jsdom answers every query `false`; the pane turns on width. */
window.matchMedia = ((media: string) => ({
  matches: true,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

const { AccountDock } = await import("./AccountDock");
const { deskWasOpen, useAccountPanels } = await import("./accountPanels");
const { WorkspaceRail } = await import("./WorkspaceRail");
const { WorkspacePaneOutlet, WorkspacePaneProvider } =
  await import("./WorkspacePane");
const { api } = await import("@/lib/api");

/**
 * `/portfolio`'s own observer, reduced to its cache key.
 *
 * Mounted beside the panel to check the claim the design rests on: the panel is
 * a *reader* of that page's cache, so a user with the page warm pays no second
 * walk of every connector to open it.
 */
function PortfolioPageQuery({ server }: { server: string }) {
  useQuery({
    queryKey: ["portfolio", server],
    queryFn: () => api.getPortfolio(server),
    refetchInterval: 15_000,
  });
  return null;
}

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let qc: QueryClient;

/**
 * The desk as the page composes it: the pane it opens in, and its two words on
 * the rail.
 *
 * The rail is shared with the agent's entry and so is built by the page rather
 * than by the desk (`useAccountPanels`) — but the pair is one feature, and a
 * test that rendered only the panel could not click anything. The pane is here
 * for the same reason: the panel is a sheet now, and a sheet with nowhere to
 * portal into is not the thing the reader sees.
 *
 * `open` is the page's, standing in for the `PaneView` union in `AgentChatTab`
 * — which is exactly the point being pinned: the desk does not decide whether
 * it is on screen, the pane does, and that is what makes it exclusive with the
 * agent panel without either one knowing about the other.
 */
function Desk({
  server,
  loopsCount,
  onOpenLoop,
}: {
  server: string | null;
  /**
   * How many loops this server has, standing in for `AgentChatTab`'s own
   * `loopsOnServer(...).length` (FEAT-1xx) — `undefined` leaves the Loops
   * section exactly as unaware of the fleet as Portfolio and Execution are.
   */
  loopsCount?: number;
  onOpenLoop?: (agentSlug: string, strategySlug: string) => void;
}) {
  const [open, setOpen] = useState(deskWasOpen);
  const account = useAccountPanels({
    server,
    open,
    onOpenChange: setOpen,
    loopsActivity:
      loopsCount === undefined
        ? undefined
        : { count: loopsCount, isLoading: false },
  });
  return (
    <WorkspacePaneProvider>
      <div className="flex">
        <div className="flex-1" />
        <WorkspacePaneOutlet />
        <WorkspaceRail groups={[{ id: "desk", items: account.railItems }]} />
      </div>
      <AccountDock
        server={server}
        shown={account.shown}
        onToggle={account.toggle}
        onClose={account.close}
        onOpenLoop={onOpenLoop}
      />
      {/* The agent panel, reduced to the only thing it does to the desk: take
          the pane. The union in `AgentChatTab` is what makes this one line. */}
      <button data-testid="open-agent" onClick={() => setOpen(false)} />
    </WorkspacePaneProvider>
  );
}

async function render(
  server: string | null = "brigado_2",
  warm = false,
  loopsCount?: number,
  onOpenLoop?: (agentSlug: string, strategySlug: string) => void,
) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          {warm && server && <PortfolioPageQuery server={server} />}
          <Desk server={server} loopsCount={loopsCount} onOpenLoop={onOpenLoop} />
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

const tab = (label: string) =>
  container.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
/** The panel body, which exists only while something is open. */
const column = () =>
  container.querySelector<HTMLElement>('[data-testid="account-dock"]');
/** Where a split sheet portals to: the pane, not a column of the desk's own. */
const paneHost = () =>
  container.querySelector<HTMLElement>('aside[aria-label="Workspace pane"]');
/** The whole sheet — its bar included, which is where the server is named. */
const sheet = () => (column() ? paneHost() : null);
const sectionHeaders = () =>
  [...(column()?.querySelectorAll("button[title]") ?? [])].map(
    (b) => b.textContent ?? "",
  );

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  qc = new QueryClient({
    // The app's own defaults (lib/queryClient), because the cache-sharing
    // claim below is a fact about `staleTime`, not about this harness.
    defaultOptions: { queries: { retry: false, staleTime: 5000 } },
  });
  localStorage.clear();
  subscribed.clear();
  getPortfolio.mockResolvedValue({
    server: "brigado_2",
    connectors: [],
    total_usd: 0,
  });
  getPortfolioHistory.mockResolvedValue({
    server: "brigado_2",
    points: [],
    interval: "1h",
  });
  getBots.mockResolvedValue({
    controllers: [],
    bots: [],
    total_pnl: 0,
    total_volume: 0,
  });
  getExecutors.mockResolvedValue([]);
  getFleetMap.mockResolvedValue({ owners: [], deeds: { bots: {}, since: 0 } });
  getAgents.mockResolvedValue([]);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("the account dock", () => {
  it("costs nothing with both tabs off", async () => {
    await render();

    // The rail is all there is: no column, no query, no channel. A closed
    // section unmounts its body, and that is the whole of the `enabled` gate.
    expect(column()).toBeNull();
    expect(getPortfolio).not.toHaveBeenCalled();
    expect(getPortfolioHistory).not.toHaveBeenCalled();
    expect(getBots).not.toHaveBeenCalled();
    expect(getExecutors).not.toHaveBeenCalled();
    expect([...subscribed]).toEqual([]);
  });

  it("opens each panel independently, and both at once", async () => {
    await render();

    await click(tab("Portfolio"));
    expect(tab("Portfolio").getAttribute("aria-pressed")).toBe("true");
    expect(tab("Execution").getAttribute("aria-pressed")).toBe("false");
    expect(getPortfolio).toHaveBeenCalledTimes(1);
    // The other panel is a header bar, so its own fetches stay unmade.
    expect(getBots).not.toHaveBeenCalled();

    await click(tab("Execution"));
    expect(tab("Portfolio").getAttribute("aria-pressed")).toBe("true");
    expect(tab("Execution").getAttribute("aria-pressed")).toBe("true");
    expect(getBots).toHaveBeenCalledTimes(1);
    // The three channels `/bots` itself holds, because the panel now reads
    // through the same hook (FEAT-114). The socket is shared and ref-counted
    // per channel, so a reader with the browser open pays for none of them
    // twice — and a closed section still subscribes to nothing at all.
    expect([...subscribed].sort()).toEqual([
      "bots",
      "controller_perf",
      "executors:brigado_2",
    ]);

    // Both open: two panes, half and half, with the seam that moves the
    // boundary between them.
    const open = [...column()!.querySelectorAll("div.flex-1.basis-0")];
    expect(open).toHaveLength(2);
    expect(open.map((el) => (el as HTMLElement).style.flexGrow)).toEqual([
      "0.5",
      "0.5",
    ]);
    expect(column()!.querySelector('[role="separator"]')).not.toBeNull();
  });

  it("hands the whole panel to a lone section, with no seam to drag", async () => {
    localStorage.setItem(DESK_SPLIT_KEY, "0.7");
    await render();
    await click(tab("Execution"));

    // One pane means one occupant: it takes the panel whatever the stored
    // split says, and a handle under it would drag against a header.
    const [only] = [...column()!.querySelectorAll("div.flex-1.basis-0")];
    expect((only as HTMLElement).style.flexGrow).toBe("");
    expect(column()!.querySelector('[role="separator"]')).toBeNull();

    // Opening the other one back up restores the split the reader dragged.
    await click(tab("Portfolio"));
    const both = [...column()!.querySelectorAll("div.flex-1.basis-0")];
    const shares = both.map((el) => Number((el as HTMLElement).style.flexGrow));
    expect(shares[0]).toBeCloseTo(0.7);
    expect(shares[1]).toBeCloseTo(0.3);
  });

  it("opens in the workspace pane, not in a column of its own", async () => {
    await render();
    await click(tab("Portfolio"));

    // The load-bearing assertion of the second revision: the desk is a sheet in
    // the one pane, which is what makes it exclusive with the agent panel by
    // construction rather than by a rule somebody has to remember — and what
    // stopped the row asking for a fifth column it could not pay for.
    const host = paneHost()!;
    expect(host.contains(column()!)).toBe(true);
    // Nothing floats: a desk drawn over the dock beside it is what the first
    // revision got wrong, and the pane is in flow.
    expect(host.className).not.toContain("absolute");

    // And the pane comes *before* the rail in the row, so the panel opens away
    // from its own controls rather than under them — the rail is the far edge.
    const rail = tab("Portfolio").closest("aside")!;
    expect(host.compareDocumentPosition(rail)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("gives the pane back when the last section is closed", async () => {
    await render();
    await click(tab("Portfolio"));
    expect(paneHost()!.className).not.toContain("hidden");

    await click(tab("Portfolio"));
    // No empty panel with two collapsed headers in it, and the pane is free
    // for whatever the reader opens next.
    expect(column()).toBeNull();
    expect(paneHost()!.className).toContain("hidden");
  });

  it("comes back on the desk the agent panel took it from", async () => {
    await render();
    await click(tab("Portfolio"));
    await click(tab("Execution"));

    // Something else claims the pane. The desk is off screen and its tiles say
    // so — nothing is open to be pressed about.
    await click(
      container.querySelector<HTMLElement>("[data-testid=open-agent]")!,
    );
    expect(column()).toBeNull();
    expect(tab("Portfolio").getAttribute("aria-pressed")).toBe("false");

    // Either tile brings back both, because both is where it was left. A click
    // on an unpressed tile can only mean "show me this" — it must never quietly
    // turn a section off on the way in.
    await click(tab("Execution"));
    expect(tab("Portfolio").getAttribute("aria-pressed")).toBe("true");
    expect(tab("Execution").getAttribute("aria-pressed")).toBe("true");
  });

  it("forgets the desk when the panel's own Close says so", async () => {
    await render();
    await click(tab("Portfolio"));

    // Unlike losing the pane, this is the reader saying they are done — and it
    // is the fact a reload reads, so a close that kept the sections would be a
    // close that undid itself on the next mount.
    await click(sheet()!.querySelector<HTMLElement>("button[title='Close']")!);
    expect(column()).toBeNull();
    expect(localStorage.getItem(ACCOUNT_DOCK_KEY)).toBe("[]");
  });

  it("names the server the panels are reading, once", async () => {
    await render();
    await click(tab("Portfolio"));

    // In the panel's own bar rather than on every section header — the width
    // that bought is what the tables inside spend.
    expect(sheet()!.textContent).toContain("brigado_2");
    expect(sectionHeaders().join(" ")).not.toContain("brigado_2");
  });

  it("closes with the edge's own glyph, not a modal's X", async () => {
    await render();
    await click(tab("Portfolio"));

    // Every bar along the right edge closes the same way — the dock's, the
    // desk's, the agent panel's. An X here read as "discard" beside three
    // chevrons that read as "fold away".
    const close = sheet()!.querySelector<HTMLElement>("button[title='Close']")!;
    expect(close.querySelector("svg.lucide-panel-right-close")).not.toBeNull();
  });

  it("survives a reload, and nothing else", async () => {
    await render();
    await click(tab("Execution"));

    expect(JSON.parse(localStorage.getItem(ACCOUNT_DOCK_KEY)!)).toEqual([
      "execution",
    ]);

    // A fresh mount of the same browser comes back where it was left — the
    // panel up, on the section it was showing. The recorded sections are what
    // says the desk was open; there is no second flag to keep in step.
    await act(() => root.unmount());
    root = createRoot(container);
    await render();
    expect(tab("Execution").getAttribute("aria-pressed")).toBe("true");
    expect(tab("Portfolio").getAttribute("aria-pressed")).toBe("false");
    expect(paneHost()!.contains(column()!)).toBe(true);
  });

  it("reads /portfolio's cache rather than walking the connectors again", async () => {
    await render("brigado_2", true);
    expect(getPortfolio).toHaveBeenCalledTimes(1);

    await click(tab("Portfolio"));

    // The heaviest call the server makes, and the panel makes it zero times:
    // same key, same entry, and never the forced `refresh=true` warm-up the
    // page runs on mount.
    expect(getPortfolio).toHaveBeenCalledTimes(1);
    expect(getPortfolio).not.toHaveBeenCalledWith("brigado_2", true);
  });

  it("disables both tabs, and says why, with no server", async () => {
    await render(null);

    expect(tab("Portfolio").disabled).toBe(true);
    expect(tab("Execution").disabled).toBe(true);
    expect(tab("Portfolio").title).toBe(
      "Select a server to see your portfolio",
    );
    expect(column()).toBeNull();
  });

  it("reaches no panel of zeroes when a stored panel loses its server", async () => {
    localStorage.setItem(ACCOUNT_DOCK_KEY, JSON.stringify(["portfolio"]));

    await render(null);

    expect(column()).toBeNull();
    expect(getPortfolio).not.toHaveBeenCalled();
    // Still recorded: the panel comes back when a server does.
    expect(localStorage.getItem(ACCOUNT_DOCK_KEY)).toBe('["portfolio"]');
  });
});

describe("the desk's Loops section (FEAT-1xx)", () => {
  function live(over: Partial<LiveLoop> = {}): LiveLoop {
    return {
      agentId: "a1",
      sessionNum: 1,
      status: "running",
      tickCount: 3,
      lastTickAt: 0,
      frequencySec: 30,
      lastAction: "",
      lastDid: null,
      lastError: "",
      ...over,
    };
  }

  function owner(over: Partial<LiveFleetOwner> = {}): LiveFleetOwner {
    return {
      runKey: "brigado.brl_mm",
      agentSlug: "brigado",
      agentName: "Brigado",
      strategySlug: "brl_mm",
      strategyName: "BRL MM",
      namespace: "brigado-brl_mm",
      declaredBots: [],
      agentIds: [],
      live: live(),
      ...over,
    };
  }

  it("sits as a third tab beside Portfolio and Execution, sharing the panel evenly", async () => {
    await render();

    await click(tab("Portfolio"));
    await click(tab("Execution"));
    await click(tab("Loops"));

    expect(tab("Loops").getAttribute("aria-pressed")).toBe("true");
    // Three open sections, not the two-way drag: the seam only knows how to
    // divide Portfolio and Execution, so a third pane falls back to the even
    // split every `DockSection` gets when nobody hands it a `share`.
    const open = [...column()!.querySelectorAll("div.flex-1.basis-0")];
    expect(open).toHaveLength(3);
    expect(open.every((el) => (el as HTMLElement).style.flexGrow === "")).toBe(
      true,
    );
    expect(column()!.querySelector('[role="separator"]')).toBeNull();
  });

  it("opens expanded the first time the desk shows a server that is already looping", async () => {
    await render("brigado_2", false, 2);

    // Nobody has touched Loops yet — opening any other tab is what puts the
    // desk on screen for the first time, and that is the moment its default
    // is decided.
    await click(tab("Portfolio"));
    expect(tab("Loops").getAttribute("aria-pressed")).toBe("true");
  });

  it("leaves Loops closed by default when this server has nothing looping", async () => {
    await render("brigado_2", false, 0);

    await click(tab("Portfolio"));
    expect(tab("Loops").getAttribute("aria-pressed")).toBe("false");
  });

  it("carries the same badge the old standalone tile showed", async () => {
    await render("brigado_2", false, 3);

    expect(tab("Loops").textContent).toContain("3");
  });

  it("does not decide a default before the fleet data has settled", async () => {
    // `getFleetMap` never resolves in this test — the loop the desk would
    // have opened on cannot yet be told from a quiet server, so the default
    // must wait rather than guess closed.
    getFleetMap.mockReturnValue(new Promise(() => {}));
    await render("brigado_2");

    await click(tab("Portfolio"));
    expect(tab("Loops").getAttribute("aria-pressed")).toBe("false");
  });

  it("reads the fleet's own loops, narrowed to this server, and opens the one clicked", async () => {
    getFleetMap.mockResolvedValue({
      owners: [
        owner({ agentSlug: "brigado", strategySlug: "brl_mm" }),
        owner({
          agentSlug: "brigado",
          strategySlug: "elsewhere",
          strategyName: "Elsewhere",
        }),
      ],
      deeds: { bots: {}, since: 0 },
    });
    getAgents.mockResolvedValue([
      {
        slug: "brigado",
        name: "Brigado",
        description: "",
        when_to_consult: "",
        agent_key: "",
        strategy_count: 2,
        server_name: "brigado_2",
        strategies: [
          {
            slug: "brl_mm",
            name: "BRL MM",
            description: "",
            status: "running",
            agent_id: "brigado.brl_mm",
            session_count: 1,
            experiment_count: 0,
            tick_count: 3,
            latest_session_pnl: 0,
            total_pnl: 0,
            total_volume: 0,
            open_positions: 0,
            instances: [],
          },
          {
            slug: "elsewhere",
            name: "Elsewhere",
            description: "",
            status: "running",
            agent_id: "brigado.elsewhere",
            session_count: 1,
            experiment_count: 0,
            tick_count: 3,
            latest_session_pnl: 0,
            total_pnl: 0,
            total_volume: 0,
            open_positions: 0,
            // Declared on another server (CORR-429): this section's whole
            // contract is one server, the same as its Portfolio/Execution
            // siblings, so this loop must not show up here.
            server_name: "other_box",
            instances: [],
          },
        ],
        status: "running",
        session_count: 1,
        experiment_count: 0,
        tick_count: 3,
        latest_session_pnl: 0,
        total_pnl: 0,
        total_volume: 0,
        open_positions: 0,
        instances: [],
      },
    ]);
    const onOpenLoop = vi.fn();

    await render("brigado_2", false, undefined, onOpenLoop);
    await click(tab("Loops"));
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });

    const cards = [
      ...column()!.querySelectorAll<HTMLButtonElement>("[data-loop-row]"),
    ];
    expect(cards).toHaveLength(1);
    expect(cards[0].textContent).toContain("BRL MM");

    await click(cards[0]);
    expect(onOpenLoop).toHaveBeenCalledWith("brigado", "brl_mm");
  });
});
