/**
 * The spine is the run's navigation, and it must not lie about the ticks.
 *
 * Four beat states, one of which exists only because of history: every session
 * written before FEAT-097 journals `actions=0` on every tick and keeps no
 * `actions.jsonl` at all. The naive reading of that is "twenty ticks that did
 * nothing", which is an assertion the data does not support — so the fourth
 * state says *unrecorded*, and this file pins it beside the three real ones.
 *
 * The other promise is the click: a beat is an address, and clicking one hands
 * up the tick so the page can put it in the URL.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AgentActionRow } from "@/lib/agent-attribution";
import { TickSpine } from "./TickSpine";

vi.mock("@/lib/api", () => ({
  api: {
    getSessionJournal: vi.fn(async () => ({ content: JOURNAL })),
    getSessionActions: vi.fn(async () => ({ actions: ACTIONS })),
  },
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let JOURNAL = "";
let ACTIONS: AgentActionRow[] = [];

function journal(ticks: { tick: number; actions: number; summary?: string }[]): string {
  const lines = ticks.map(
    (t) =>
      `- tick#${t.tick} | 2026-08-06 22:${String(t.tick).padStart(2, "0")} | actions=${t.actions} | ${t.summary ?? "held"}`,
  );
  return `# Journal\n\n## Ticks\n\n${lines.join("\n")}\n`;
}

function deed(over: Partial<AgentActionRow> = {}): AgentActionRow {
  return {
    tick: 1,
    at: 1_700_000_000,
    tool: "create_grid_executor",
    verb: "create_grid_executor",
    summary: "Create grid executor on SOL-USDC",
    ok: true,
    error: "",
    ...over,
  };
}

// ── Harness ──

let container: HTMLDivElement;
let root: Root;
let picked: (number | null)[];

async function render({
  hasActionsLog = true,
  selectedTick = null as number | null,
  bare = false,
} = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <TickSpine
          slug="brigado"
          sslug="brl_mm"
          sessionNum={1}
          hasActionsLog={hasActionsLog}
          selectedTick={selectedTick}
          onSelectTick={(t) => picked.push(t)}
          bare={bare}
        />
      </QueryClientProvider>,
    );
  });
  await settle();
}

/** react-query resolves on a later macrotask than the render that asked. */
async function settle() {
  for (let i = 0; i < 5; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const beats = () => [...document.querySelectorAll<HTMLElement>("[data-beat]")];
const states = () => beats().map((b) => b.dataset.beatState);

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  JOURNAL = "";
  ACTIONS = [];
  picked = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("one beat per tick", () => {
  it("draws every tick the journal recorded, oldest first", async () => {
    JOURNAL = journal([
      { tick: 1, actions: 0 },
      { tick: 2, actions: 0 },
      { tick: 3, actions: 0 },
    ]);
    await render();

    expect(beats().map((b) => b.dataset.beat)).toEqual(["1", "2", "3"]);
  });

  it("says so rather than drawing an empty strip for a run with no ticks", async () => {
    JOURNAL = "# Journal\n";
    await render();

    expect(beats()).toHaveLength(0);
    expect(container.querySelector("[data-spine-empty]")).not.toBeNull();
  });
});

describe("what a beat's colour claims", () => {
  it("is green when the tick's deeds all worked", async () => {
    JOURNAL = journal([{ tick: 1, actions: 2 }]);
    ACTIONS = [deed({ tick: 1 }), deed({ tick: 1, verb: "stop_executor" })];
    await render();

    expect(states()).toEqual(["ok"]);
  });

  it("is red when any deed on the tick failed", async () => {
    JOURNAL = journal([{ tick: 1, actions: 2 }]);
    ACTIONS = [deed({ tick: 1 }), deed({ tick: 1, ok: false, error: "rejected" })];
    await render();

    expect(states()).toEqual(["failed"]);
  });

  it("is hollow when the run keeps a log and the tick did nothing", async () => {
    JOURNAL = journal([
      { tick: 1, actions: 1 },
      { tick: 2, actions: 0 },
    ]);
    ACTIONS = [deed({ tick: 1 })];
    await render({ hasActionsLog: true });

    expect(states()).toEqual(["ok", "idle"]);
  });

  it("never claims a pre-log run did nothing", async () => {
    // Every session on disk before FEAT-097: `actions=0` on every line and no
    // `actions.jsonl` to check it against.
    JOURNAL = journal([
      { tick: 1, actions: 0 },
      { tick: 2, actions: 0 },
      { tick: 3, actions: 0 },
    ]);
    await render({ hasActionsLog: false });

    expect(states()).toEqual(["unlogged", "unlogged", "unlogged"]);
    expect(container.textContent).toContain("no action log for this run");
  });
});

describe("a beat is an address", () => {
  it("hands up the tick it was clicked on", async () => {
    JOURNAL = journal([
      { tick: 1, actions: 0 },
      { tick: 7, actions: 0 },
    ]);
    await render();

    act(() => {
      beats()[1].click();
    });
    expect(picked).toEqual([7]);
  });

  it("goes back to the run overview", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }]);
    await render({ selectedTick: 1 });

    act(() => {
      container.querySelector<HTMLElement>("[data-spine-overview]")!.click();
    });
    expect(picked).toEqual([null]);
  });

  it("shows the deed on hover, not the model's narration", async () => {
    JOURNAL = journal([{ tick: 4, actions: 1, summary: "thinking about it" }]);
    ACTIONS = [deed({ tick: 4, summary: "Deploy bot 'brl_mm'" })];
    await render();

    expect(beats()[0].getAttribute("aria-label")).toBe("#4 — Deploy bot 'brl_mm'");
  });
});

describe("hovering a beat opens its card", () => {
  const card = () => document.querySelector<HTMLElement>("[data-beat-card]");
  // jsdom has no PointerEvent; React keys its handlers on the event *type*.
  const hover = (el: HTMLElement, clientX = 0, clientY = 0) =>
    act(() => {
      el.dispatchEvent(new MouseEvent("pointermove", { bubbles: true, clientX, clientY }));
    });
  // jsdom has no layout; these rectangles can also simulate an animation frame.
  const place = (el: HTMLElement, left: number, width = 8, top = 0) => {
    el.getBoundingClientRect = () =>
      ({ left, right: left + width, width, top, bottom: top + 20, height: 20 }) as DOMRect;
  };
  const unhover = () =>
    act(() => {
      document
        .querySelector('[data-testid="tick-spine"]')!
        .dispatchEvent(
          new MouseEvent("pointerout", { bubbles: true, relatedTarget: document.body }),
        );
    });

  it("shows the tick's time, journal line and every deed with its error", async () => {
    JOURNAL = journal([{ tick: 4, actions: 2, summary: "Fleet healthy, drift 1%" }]);
    ACTIONS = [
      deed({ tick: 4, summary: "Deploy bot 'brl_mm'" }),
      deed({ tick: 4, summary: "Stop executor abc", ok: false, error: "not found" }),
    ];
    await render();

    expect(card()).toBeNull();
    hover(beats()[0]);
    expect(beats()[0].dataset.beatHovered).toBe("true");

    const c = card()!;
    expect(c.dataset.beatCard).toBe("4");
    expect(c.textContent).toContain("2026-08-06 22:04");
    expect(c.textContent).toContain("Action failed");
    expect(c.querySelector("[data-beat-card-summary]")!.textContent).toBe(
      "Fleet healthy, drift 1%",
    );
    const deeds = [...c.querySelectorAll("[data-beat-card-deed]")].map((d) => d.textContent);
    expect(deeds).toEqual(["✓Deploy bot 'brl_mm'", "✗Stop executor abcnot found"]);

    unhover();
    expect(card()).toBeNull();
    expect(beats()[0].dataset.beatHovered).toBeUndefined();
  });

  it("keeps the nearest beat while the pointer crosses a gap", async () => {
    JOURNAL = journal([
      { tick: 1, actions: 0 },
      { tick: 2, actions: 0 },
    ]);
    await render();

    // The visible gap and hover tolerance both use 4px.
    place(beats()[0], 100);
    place(beats()[1], 112);
    const strip = document.querySelector<HTMLElement>('[data-testid="tick-spine"]')!;

    hover(strip, 109); // nearer beat 1's centre (104) than beat 2's (116)
    expect(card()!.dataset.beatCard).toBe("1");
    hover(strip, 111); // past the midpoint
    expect(card()!.dataset.beatCard).toBe("2");
    hover(strip, 300); // well past the last beat
    expect(card()).toBeNull();
  });

  it("does not retarget when layout changes underneath a stationary cursor", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }, { tick: 2, actions: 0 }]);
    await render();
    const [first, second] = beats();
    place(first, 100);
    place(second, 112);
    const strip = container.querySelector<HTMLElement>('[data-testid="tick-spine"]')!;

    hover(strip, 114);
    expect(card()!.dataset.beatCard).toBe("2");
    // Only the visual shapes move during animation, never their hit targets.
    place(first.querySelector<HTMLElement>("[data-beat-visual]")!, 104, 14);
    place(second.querySelector<HTMLElement>("[data-beat-visual]")!, 122, 14);
    hover(strip, 114);
    expect(card()!.dataset.beatCard).toBe("2");

    unhover();
    hover(strip, 114); // re-entry uses the same stable boundaries
    expect(card()!.dataset.beatCard).toBe("2");
  });

  it("uses stable boundaries throughout rightward and leftward sweeps", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }, { tick: 2, actions: 0 }, { tick: 3, actions: 0 }]);
    await render();
    beats().forEach((beat, i) => place(beat, 100 + i * 12));
    const strip = container.querySelector<HTMLElement>('[data-testid="tick-spine"]')!;
    const targets = beats().map((beat) => beat.className);
    const sweep = [
      [104, "1"], [109, "1"], [111, "2"], [117, "2"], [123, "3"], [128, "3"],
      [123, "3"], [121, "2"], [116, "2"], [111, "2"], [109, "1"], [104, "1"],
    ] as const;
    for (const [x, tick] of sweep) {
      hover(strip, x);
      expect(card()!.dataset.beatCard).toBe(tick);
      expect(beats().map((beat) => beat.className)).toEqual(targets);
    }
  });

  it("grows the visual in both dimensions and offsets neighbours within fixed slots", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }, { tick: 2, actions: 0 }, { tick: 3, actions: 0 }]);
    await render();
    const [first, second, third] = beats();
    const visuals = beats().map((beat) => beat.querySelector<HTMLElement>("[data-beat-visual]")!);

    hover(second);
    expect(second.className).toContain("h-5 w-2");
    expect(visuals[1].className).toContain("h-[26px] w-[14px]");
    expect(visuals[0].style.transform).toContain("-3px");
    expect(visuals[2].style.transform).toContain("3px");

    hover(first);
    expect(visuals[1].style.transform).toContain("3px");
    hover(third);
    expect(visuals[1].style.transform).toContain("-3px");
    unhover();
    expect(visuals.every((visual) => visual.style.transform.includes("0px"))).toBe(true);
  });

  it("allows changing wrapped rows regardless of horizontal direction", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }, { tick: 2, actions: 0 }]);
    await render();
    const [first, second] = beats();
    place(first, 100);
    place(second, 100, 8, 30);
    const strip = container.querySelector<HTMLElement>('[data-testid="tick-spine"]')!;

    hover(strip, 106, 10);
    hover(strip, 104, 40); // moving left, but onto the next row
    expect(card()!.dataset.beatCard).toBe("2");
  });

  it("caps a busy tick's deeds and says how many more", async () => {
    JOURNAL = journal([{ tick: 1, actions: 9 }]);
    ACTIONS = Array.from({ length: 9 }, (_, i) => deed({ tick: 1, summary: `deed ${i}` }));
    await render();

    hover(beats()[0]);
    expect(card()!.querySelectorAll("[data-beat-card-deed]")).toHaveLength(6);
    expect(card()!.textContent).toContain("+3 more");
  });

  it("says a pre-log tick is unrecorded rather than empty", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }]);
    await render({ hasActionsLog: false });

    hover(beats()[0]);
    expect(card()!.textContent).toContain("Not logged");
    expect(card()!.textContent).toContain("no action log for this run");
  });

  it("opens on keyboard focus too", async () => {
    JOURNAL = journal([{ tick: 3, actions: 0 }]);
    await render();

    act(() => beats()[0].focus());
    expect(card()!.dataset.beatCard).toBe("3");
  });

  it("lets pointer input resume after keyboard focus moves elsewhere", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }, { tick: 2, actions: 0 }]);
    await render();
    const [first, second] = beats();
    place(first, 100);
    place(second, 112);

    hover(first, 102);
    act(() => second.focus());
    expect(card()!.dataset.beatCard).toBe("2");
    hover(first, 104);
    expect(card()!.dataset.beatCard).toBe("1");
  });
});

describe("drawn bare, beside the tabs (ARCH-425)", () => {
  // jsdom lays nothing out, so a strip is as wide as this says it is.
  const realScrollWidth = Object.getOwnPropertyDescriptor(
    Element.prototype,
    "scrollWidth",
  );
  beforeEach(() => {
    Object.defineProperty(Element.prototype, "scrollWidth", {
      configurable: true,
      get: () => 2_400,
    });
  });
  afterEach(() => {
    if (realScrollWidth) {
      Object.defineProperty(Element.prototype, "scrollWidth", realScrollWidth);
    }
  });

  const strip = () =>
    container.querySelector<HTMLElement>('[data-testid="tick-spine"]')!;

  it("keeps to one scrolling line and brings no border of its own", async () => {
    JOURNAL = journal(
      Array.from({ length: 300 }, (_, i) => ({ tick: i + 1, actions: 0 })),
    );
    await render({ bare: true });

    expect(beats()).toHaveLength(300);
    const cls = strip().className;
    expect(cls).toContain("flex-nowrap");
    expect(cls).toContain("overflow-x-auto");
    expect(cls).not.toContain("border-b");
    expect(cls).not.toContain("flex-wrap ");
    // A beat that could shrink would turn 300 of them into a hairline.
    expect(beats()[0].className).toContain("shrink-0");
  });

  it("opens on the newest beat", async () => {
    JOURNAL = journal(
      Array.from({ length: 300 }, (_, i) => ({ tick: i + 1, actions: 0 })),
    );
    await render({ bare: true });

    expect(strip().scrollLeft).toBe(2_400);
  });

  it("is still the wrapping, bordered row when not bare", async () => {
    JOURNAL = journal([{ tick: 1, actions: 0 }]);
    await render();

    expect(strip().className).toContain("flex-wrap");
    expect(strip().className).toContain("border-b");
    expect(strip().scrollLeft).toBe(0);
  });
});
