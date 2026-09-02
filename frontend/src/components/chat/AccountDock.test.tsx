/**
 * The desk beside the conversation (FEAT-094).
 *
 * What is pinned here is the part of the design that is not visible in a
 * screenshot: that a tab nobody opened costs *nothing* — no portfolio walk, no
 * bots call, no executors call, no socket channel — that opening one panel does
 * not open the other, and that both the panels and the width they were dragged
 * to survive a reload.
 *
 * The panels' own contents are tested in DockExecution.test.tsx; here they are
 * stubbed, because the question is the shell.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ACCOUNT_DOCK_KEY, ACCOUNT_DOCK_WIDTH_KEY } from "@/lib/sessionState";

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

const { AccountDock } = await import("./AccountDock");
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

async function render(server: string | null = "brigado_2", warm = false) {
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          {warm && server && <PortfolioPageQuery server={server} />}
          <AccountDock server={server} />
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
/** The panel column, which exists only while something is open. */
const column = () =>
  container.querySelector<HTMLElement>('[data-testid="account-dock"]');
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
  getPortfolio.mockResolvedValue({ server: "brigado_2", connectors: [], total_usd: 0 });
  getPortfolioHistory.mockResolvedValue({ server: "brigado_2", points: [], interval: "1h" });
  getBots.mockResolvedValue({ controllers: [], bots: [], total_pnl: 0, total_volume: 0 });
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

  it("takes a column in flow rather than floating over the dock", async () => {
    await render();
    await click(tab("Portfolio"));

    // The load-bearing assertion of the revision: nothing positions this out of
    // the row, so the context dock beside it stays visible and readable while a
    // balance is open. A float here is what made the two docks exclusive.
    const panel = column()!;
    expect(panel.className).not.toContain("absolute");
    expect(panel.className).not.toContain("z-40");
    expect(panel.className).toContain("shrink-0");

    // And it comes *before* the rail in the row, so the panels open away from
    // their own controls rather than under them.
    const rail = tab("Portfolio").closest("aside")!;
    expect(panel.compareDocumentPosition(rail)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
  });

  it("names the server the panels are reading, once", async () => {
    await render();
    await click(tab("Portfolio"));

    // In the dock's own bar rather than on every section header — the width
    // that bought is what the tables inside spend.
    expect(column()!.textContent).toContain("brigado_2");
    expect(sectionHeaders().join(" ")).not.toContain("brigado_2");
  });

  it("remembers the width the seam was dragged to", async () => {
    await render();
    await click(tab("Portfolio"));

    const seam = container.querySelector<HTMLElement>(
      '[aria-label="Resize account panels"]',
    )!;
    // The keyboard path, which is the same `onWidth` the drag calls.
    await act(async () => {
      seam.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }),
      );
    });

    // Stored rounded, applied exact — the same split the context dock's width
    // has, so what comes back on the next mount is what is on screen now.
    const stored = Number(localStorage.getItem(ACCOUNT_DOCK_WIDTH_KEY));
    expect(stored).toBeGreaterThan(0);
    expect(Math.round(parseFloat(column()!.style.width))).toBe(stored);
  });

  it("survives a reload, and nothing else", async () => {
    await render();
    await click(tab("Execution"));

    expect(JSON.parse(localStorage.getItem(ACCOUNT_DOCK_KEY)!)).toEqual([
      "execution",
    ]);

    // A fresh mount of the same browser comes back where it was left.
    await act(() => root.unmount());
    root = createRoot(container);
    await render();
    expect(tab("Execution").getAttribute("aria-pressed")).toBe("true");
    expect(tab("Portfolio").getAttribute("aria-pressed")).toBe("false");
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
    expect(tab("Portfolio").title).toBe("Select a server to see your portfolio");
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
