/**
 * The Playbook band answers its own hint, and nothing the screen already said.
 *
 * The band is the one place `strategy.md` and the learnings can be read, and
 * for as long as it hosted the whole workbench it was the one place they could
 * *not*: the file was behind a button in a modal, over five bands that each
 * restated something within two inches of themselves. Four ways it could go
 * back:
 *
 * 1. Printing the documents' contents nowhere, or only behind a click.
 * 2. Restating the screen's own answers — the strategy's name, its Pause/Stop,
 *    the loop's cadence, the deployment ledger, the performance strip.
 * 3. Printing a disabled limit as the number the engine spells it with. `-1`
 *    is *no limit*, and an operator reading "max drawdown −1%" has been told
 *    something false about their own risk.
 * 4. Saying "empty" where it could say what the file is *for* — a strategy
 *    with no playbook yet reads as a broken page rather than an unwritten one.
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

import type { StrategyDetail } from "@/lib/api";
import { parseYamlMapping } from "@/lib/configYaml";

/** jsdom has none, and the editor dialogs' module graph reads the theme. */
window.matchMedia = ((media: string) => ({
  matches: false,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

vi.mock("@/lib/api", () => ({
  api: {
    deleteStrategy: vi.fn(),
    getRoutineInstances: vi.fn(() => Promise.resolve([])),
    setRestartOnBoot: vi.fn(),
    updateStrategyConfig: vi.fn(),
    updateStrategyMd: vi.fn(),
    updateStrategyLearnings: vi.fn(),
  },
}));

// CodeMirror is a real editor widget needing layout jsdom has not got, and the
// config editor's own file is where its behaviour is asserted. Here it only has
// to hand back the buffer the card seeded it with.
vi.mock("@/components/editor/CodeEditor", () => ({
  CodeEditor: ({
    value,
    onChange,
  }: {
    value: string;
    onChange?: (next: string) => void;
  }) => <textarea data-yaml value={value} onChange={(e) => onChange?.(e.target.value)} />,
}));

vi.mock("@/components/routines/ReportBrowser", () => ({
  ReportBrowser: () => <div data-report-browser />,
}));

const { PlaybookView } = await import("./PlaybookView");
const { api } = await import("@/lib/api");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
/** What a count in the band asks the screen to open. */
const onOpenRun = vi.fn();

const STRATEGY = {
  slug: "pmm_king",
  agent_slug: "brigado",
  name: "PMM King BTC-BRL Fleet Operator",
  description: "Hourly autonomous operator for the PMM King BTC-BRL fleet.",
  strategy_md: "# Brief\n\nQuote both sides, never cross the book.",
  learnings: "## Execution notes\n\nUse raw manage_bots for fleet health.",
  config: {
    execution_mode: "loop",
    frequency_sec: 3600,
    tick_timeout_sec: 600,
    max_ticks: 0,
    restart_on_boot: true,
    server_name: "brigado_2",
    bot_mode: "auto",
    bot_name: "",
    agent_key: "",
    total_amount_quote: 100,
    // A key no build of this card names — the `Other` group's case, and the one
    // a typed form could not have edited at all.
    sizing_hint: "aggressive",
    risk_limits: {
      max_position_size_quote: 500,
      max_open_executors: 5,
      max_drawdown_pct: -1,
      max_leverage: -1,
    },
  },
  default_trading_context: "",
  status: "running",
  agent_id: "brigado.pmm_king_2",
  sessions: [{ number: 1 }, { number: 2 }],
  experiments: [{ number: 1 }],
  instances: [],
} as unknown as StrategyDetail;

function render(strategy: StrategyDetail = STRATEGY) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  act(() => {
    root.render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <PlaybookView
            slug="brigado"
            sslug="pmm_king"
            strategy={strategy}
            onDeleted={() => {}}
            onOpenRun={onOpenRun}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );
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

describe("PlaybookView", () => {
  it("puts both documents on screen, with nothing to click first", () => {
    render();
    const text = container.textContent ?? "";
    expect(text).toContain("Quote both sides, never cross the book.");
    expect(text).toContain("Use raw manage_bots for fleet health.");
  });

  it("does not restate what the screen above it already answered", () => {
    render();
    const text = container.textContent ?? "";
    // The loop bar names the strategy and the header carries the controls.
    expect(text).not.toContain("PMM King BTC-BRL Fleet Operator");
    expect(text).not.toContain("Pause");
    // The loop bar owns the cadence countdown; the answer stack owns the money.
    expect(text).not.toContain("next in");
    expect(text).not.toContain("Total PnL");
  });

  it("draws a disabled limit as off rather than as its sentinel", () => {
    render();
    const text = container.textContent ?? "";
    expect(text).toContain("no limit");
    expect(text).not.toContain("-1%");
    expect(text).not.toContain("−1");
  });

  it("says a cadence in time and a switched-off timeout in words", () => {
    render();
    const text = container.textContent ?? "";
    expect(text).toContain("1h 00m"); // frequency_sec: 3600
    expect(text).toContain("10m 00s"); // tick_timeout_sec: 600
    expect(text).toContain("unlimited"); // max_ticks: 0
  });

  /** The value cell of the setting labelled `label`, as the config card draws it. */
  function settingValue(label: string): HTMLElement {
    const dt = Array.from(container.querySelectorAll("dt")).find(
      (el) => el.textContent === label,
    );
    const dd = dt?.nextElementSibling as HTMLElement | null;
    if (!dd) throw new Error(`no setting "${label}"`);
    return dd;
  }
  const isOff = (dd: HTMLElement) => dd.className.includes("opacity-60");

  it("dims max ticks at its default 0 and shows a positive cap as a live number", () => {
    render();
    let dd = settingValue("max ticks");
    expect(dd.textContent).toBe("unlimited");
    expect(isOff(dd)).toBe(true);

    act(() => root.unmount());
    root = createRoot(container);
    render({ ...STRATEGY, config: { ...STRATEGY.config, max_ticks: 25 } } as StrategyDetail);
    dd = settingValue("max ticks");
    expect(dd.textContent).toBe("25");
    expect(isOff(dd)).toBe(false);
  });

  it("marks a risk limit off exactly when it reads no limit", () => {
    render();
    const drawdown = settingValue("max drawdown"); // -1
    expect(drawdown.textContent).toBe("no limit");
    expect(isOff(drawdown)).toBe(true);
    const drift = settingValue("max drift"); // absent
    expect(drift.textContent).toBe("no limit");
    expect(isOff(drift)).toBe(true);
    const executors = settingValue("max executors"); // 5
    expect(executors.textContent).toBe("5");
    expect(isOff(executors)).toBe(false);
  });

  it("tells an empty playbook what it is for", () => {
    render({ ...STRATEGY, strategy_md: "" });
    expect(container.textContent).toContain("standing brief");
  });

  it("will not delete a running strategy", () => {
    render();
    const del = [...container.querySelectorAll("button")].find((b) =>
      b.textContent?.includes("Delete"),
    );
    expect(del?.disabled).toBe(true);
  });

  it("keeps the restart switch reachable, and says which way it is set", () => {
    render();
    const sw = container.querySelector('[role="switch"]');
    expect(sw?.getAttribute("aria-checked")).toBe("true");
    expect(sw?.textContent).toContain("resumes on restart");
  });

  /** The Configuration card — not the two documents, which each have one too. */
  const configCard = () =>
    [...container.querySelectorAll("section")].find(
      (el) => el.querySelector("h3")?.textContent?.trim() === "Configuration",
    )!;
  /** That card's own Edit/Done door. */
  const editButton = () =>
    [...configCard().querySelectorAll("button")].find((b) =>
      /^(Edit|Done)$/.test(b.textContent?.trim() ?? ""),
    )!;

  it("hands the whole stored mapping to the editor, nested block and unnamed key", () => {
    render();
    act(() => editButton().click());

    const buffer = configCard().querySelector<HTMLTextAreaElement>("[data-yaml]")!.value;
    const parsed = parseYamlMapping(buffer);
    // A round trip, not a projection: the 22 rows the card names are a reading
    // of the config, and an editor that could only write those would silently
    // drop `sizing_hint` — the very key the `Other` group exists to show.
    expect(parsed.ok && parsed.value).toEqual(STRATEGY.config);
  });

  it("puts the laid-out rows back when edit mode closes", () => {
    render();
    act(() => editButton().click());
    expect(configCard().querySelector("[data-yaml]")).not.toBeNull();
    expect(configCard().querySelector("dt")).toBeNull();

    act(() => editButton().click());
    expect(configCard().querySelector("[data-yaml]")).toBeNull();
    // Still the stored values, still read as settings rather than as numbers.
    expect(settingValue("max drawdown").textContent).toBe("no limit");
    expect(settingValue("cadence").textContent).toBe("1h 00m");
  });

  it("asks before closing edit mode over unsaved config edits", () => {
    render();
    act(() => editButton().click());

    const box = configCard().querySelector<HTMLTextAreaElement>("[data-yaml]")!;
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!;
    act(() => {
      setter.call(box, "frequency_sec: 60\n");
      box.dispatchEvent(new Event("input", { bubbles: true }));
    });

    act(() => editButton().click());
    // Done is not Discard. The same bargain `DocCard` strikes one row up.
    expect(container.textContent).toContain("Unsaved Changes");
    expect(configCard().querySelector("[data-yaml]")).not.toBeNull();

    const discard = [...container.querySelectorAll("button")].find(
      (b) => b.textContent?.trim() === "Discard changes",
    )!;
    act(() => discard.click());
    expect(configCard().querySelector("[data-yaml]")).toBeNull();
    expect(settingValue("cadence").textContent).toBe("1h 00m");
  });

  it("leaves the restart switch on its own route, not on the config editor", async () => {
    vi.mocked(api.updateStrategyConfig).mockClear();
    vi.mocked(api.setRestartOnBoot).mockResolvedValueOnce({
      restart_on_boot: false,
      applied_to_running: 1,
    });
    render();
    const sw = container.querySelector<HTMLButtonElement>('[role="switch"]')!;
    await act(async () => sw.click());

    // It is the one config write that reaches live engines. Routing it through
    // the editor — whose save reaches the file and no running session — would
    // quietly make it stop doing that.
    expect(api.setRestartOnBoot).toHaveBeenCalledWith("brigado", "pmm_king", false);
    expect(api.updateStrategyConfig).not.toHaveBeenCalled();
  });

  it("says why a refused restart write snapped back", async () => {
    vi.mocked(api.setRestartOnBoot).mockRejectedValueOnce(
      new Error("Another user's loop is running this strategy — stop it first"),
    );
    render();
    const sw = container.querySelector<HTMLButtonElement>('[role="switch"]')!;
    await act(async () => sw.click());
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    }
    expect(api.setRestartOnBoot).toHaveBeenCalledWith("brigado", "pmm_king", false);
    const after = container.querySelector('[role="switch"]');
    expect(after?.getAttribute("aria-checked")).toBe("true");
    // Beside the switch, in the Configuration card's own header.
    expect(after?.closest("header")?.querySelector('[role="alert"]')?.textContent).toContain(
      "Another user's loop is running this strategy",
    );
  });
});

describe("the run counts", () => {
  beforeEach(() => onOpenRun.mockClear());

  const count = (pattern: RegExp) =>
    [...container.querySelectorAll("button")].find((b) =>
      pattern.test(b.textContent?.trim() ?? ""),
    );

  it("open the newest session and the newest dry run", () => {
    // Only the dry-run count used to go anywhere; the session count beside it
    // was a label in the same pill.
    render();
    act(() => count(/^2 sessions$/)!.click());
    expect(onOpenRun).toHaveBeenLastCalledWith("s:2");
    act(() => count(/^1 dry run$/)!.click());
    expect(onOpenRun).toHaveBeenLastCalledWith("e:1");
  });

  it("leave zero sessions a label — there is no session to open", () => {
    render({ ...STRATEGY, sessions: [] });
    expect(count(/^\d+ sessions?$/)).toBeUndefined();
    expect(container.textContent).toContain("0 sessions");
  });
});
