/**
 * The backtesting controller-config picker is portalled, not `absolute` + a
 * hand-rolled backdrop (ARCH-261).
 *
 * This picker was the last survivor of the `fixed inset-0` backdrop idiom that
 * ARCH-260 removed everywhere else — and the reason a grep for it still had a
 * hit. That backdrop covered the whole viewport while the
 * menu was open, so the click that dismissed the menu was consumed by the
 * backdrop and never reached the control the user aimed at — on a panel whose
 * next control (Time Range, a range preset, Run) is almost always the target.
 * Escape was wired on the search input alone, so it did nothing once focus had
 * moved to a list row.
 *
 * These cases pin the properties a later refactor of the menu's contents must
 * not lose: the panel is a child of `document.body` at fixed coordinates, no
 * backdrop stands between the user and the page, one outside mousedown both
 * dismisses and lands on its target, and Escape closes from anywhere.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BacktestingTab } from "./BacktestingTab";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const CONFIGS = {
  configs: [
    {
      id: "pmm-sol",
      controller_name: "pmm_simple",
      connector_name: "binance",
      trading_pair: "SOL-USDC",
    },
    {
      id: "pmm-btc",
      controller_name: "pmm_simple",
      connector_name: "binance",
      trading_pair: "BTC-USDT",
    },
  ],
};

vi.mock("@/hooks/useServer", () => ({ useServer: () => ({ server: "alpha" }) }));
vi.mock("@/hooks/useTheme", () => ({ useTheme: () => ({ theme: "dark" }) }));
vi.mock("@/lib/api", () => ({
  api: {
    getAvailableConfigs: vi.fn(async () => CONFIGS),
    getRoutineInstances: vi.fn(async () => []),
    getRoutineInstance: vi.fn(async () => null),
    listBacktestArchive: vi.fn(async () => ({ migrated: true, summaries: [] })),
    getArchivedBacktest: vi.fn(async () => null),
    runRoutine: vi.fn(async () => ({ instance_id: "i-1" })),
    stopRoutineInstance: vi.fn(async () => ({ stopped: true })),
    deleteArchivedBacktest: vi.fn(async () => ({})),
  },
}));

let container: HTMLDivElement;
let root: Root;

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <BacktestingTab />
      </QueryClientProvider>,
    );
  });
}

/** The config picker's trigger — the one announcing a listbox popup. */
const trigger = () =>
  container.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!;

/**
 * jsdom lays nothing out, so every rect is 0x0 at the origin. Stand the trigger
 * where it actually sits: the left column of the config panel.
 */
function placeTrigger() {
  trigger().getBoundingClientRect = () =>
    ({
      top: 100,
      bottom: 140,
      left: 24,
      right: 364,
      width: 340,
      height: 40,
    }) as DOMRect;
}

/** Let the configs query settle so the menu has rows to show. */
const flush = () =>
  act(async () => void (await new Promise((r) => setTimeout(r, 0))));

/**
 * A config row, wherever in the document it ended up. Rows live outside the
 * app's own container once portalled — which is also what keeps this from
 * matching the trigger after it starts showing the selected config's id.
 */
const row = (id: string) =>
  Array.from(document.querySelectorAll("button")).find(
    (b) => !container.contains(b) && b.textContent?.startsWith(id),
  );

/** Walk up from a row to the element `document.body` owns directly. */
const panel = () => {
  let node: HTMLElement | null = row("pmm-btc") ?? null;
  while (node && node.parentElement !== document.body) node = node.parentElement;
  return node;
};

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

async function pressEscape() {
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("BacktestingTab config picker placement", () => {
  it("renders the open panel outside the trigger's subtree, left-aligned", async () => {
    await render();
    await flush();
    placeTrigger();
    await click(trigger());
    await flush();

    const p = panel();
    expect(p).toBeTruthy();
    expect(p!.parentElement).toBe(document.body);
    expect(container.contains(p!)).toBe(false);
    expect(p!.style.position).toBe("fixed");
    // align="left" pins the panel's left edge to the trigger's.
    expect(parseFloat(p!.style.left)).toBe(24);
    expect(p!.style.right).toBe("");
    // matchAnchorWidth="min": never narrower than the trigger.
    expect(parseFloat(p!.style.minWidth)).toBe(340);
    expect(parseFloat(p!.style.maxHeight)).toBeGreaterThan(0);
    // The search box still claims focus on open.
    expect(document.activeElement?.getAttribute("placeholder")).toBe(
      "Search configs...",
    );
  });

  it("has no full-viewport backdrop to swallow the next click", async () => {
    await render();
    await flush();
    placeTrigger();
    await click(trigger());
    await flush();
    expect(panel()).toBeTruthy();

    const backdrops = Array.from(document.querySelectorAll("div")).filter((d) =>
      d.className.includes("fixed inset-0"),
    );
    expect(backdrops).toEqual([]);

    const outside = document.createElement("button");
    let hits = 0;
    outside.addEventListener("click", () => hits++);
    document.body.appendChild(outside);
    await click(outside);

    // One click: the menu closes *and* the button underneath fires.
    expect(panel()).toBeNull();
    expect(hits).toBe(1);
    outside.remove();
  });

  it("closes on Escape even when focus has left the search input", async () => {
    await render();
    await flush();
    placeTrigger();
    await click(trigger());
    await flush();
    expect(panel()).toBeTruthy();

    // Focus a list row: the old input-only handler went deaf right here.
    row("pmm-btc")!.focus();
    await pressEscape();
    expect(panel()).toBeNull();
  });

  it("selects a config, closes, and clears the search box", async () => {
    await render();
    await flush();
    placeTrigger();
    await click(trigger());
    await flush();

    await click(row("pmm-btc")!);
    expect(panel()).toBeNull();
    expect(trigger().textContent).toContain("pmm-btc");

    // Reopening shows an empty search box and the full, unfiltered list.
    await click(trigger());
    await flush();
    const search = document.querySelector<HTMLInputElement>(
      'input[placeholder="Search configs..."]',
    )!;
    expect(search.value).toBe("");
    expect(row("pmm-sol")).toBeTruthy();
    expect(row("pmm-btc")).toBeTruthy();
  });
});
