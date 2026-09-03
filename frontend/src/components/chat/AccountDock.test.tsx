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

import { ACCOUNT_DOCK_KEY } from "@/lib/sessionState";

/** Every call the two panels can make; none of them may fire while closed. */
const getPortfolio = vi.fn();
const getPortfolioHistory = vi.fn();
const getBots = vi.fn();
const getExecutors = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getPortfolio: (...a: unknown[]) => getPortfolio(...a),
    getPortfolioHistory: (...a: unknown[]) => getPortfolioHistory(...a),
    getBots: (...a: unknown[]) => getBots(...a),
    getExecutors: (...a: unknown[]) => getExecutors(...a),
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
function Desk({ server }: { server: string | null }) {
  const [open, setOpen] = useState(deskWasOpen);
  const account = useAccountPanels({ server, open, onOpenChange: setOpen });
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
      />
      {/* The agent panel, reduced to the only thing it does to the desk: take
          the pane. The union in `AgentChatTab` is what makes this one line. */}
      <button data-testid="open-agent" onClick={() => setOpen(false)} />
    </WorkspacePaneProvider>
  );
}

async function render(server: string | null = "brigado_2", warm = false) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          {warm && server && <PortfolioPageQuery server={server} />}
          <Desk server={server} />
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
    expect([...subscribed].sort()).toEqual(["bots", "executors:brigado_2"]);

    // Both open: two panes, each `flex-1 basis-0`, so half and half.
    const open = [...column()!.querySelectorAll("div.flex-1.basis-0")];
    expect(open).toHaveLength(2);
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
