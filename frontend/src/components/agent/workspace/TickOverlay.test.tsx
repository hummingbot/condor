/**
 * The tick overlay is read forwards: ← and → step through the session's ticks.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { adjacentTicks } from "@/components/agent/lab/runs";
import { TickOverlay } from "./TickOverlay";

vi.mock("@/lib/api", () => ({
  api: {
    getSessionJournal: vi.fn(async () => ({ content: JOURNAL })),
    getSnapshot: vi.fn(async () => ({ content: "" })),
  },
}));

vi.mock("@/components/agent/session/Snapshot", () => ({
  SnapshotDetail: ({ tick }: { tick: number }) => <div data-snapshot={tick} />,
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let JOURNAL = "";

function journal(ticks: number[]): string {
  const lines = ticks.map((t) => `- tick#${t} | 2026-08-06 22:00 | actions=0 | held`);
  return `# Journal\n\n## Ticks\n\n${lines.join("\n")}\n`;
}

describe("adjacentTicks", () => {
  it("steps by position, skipping a hole in the numbering", () => {
    expect(adjacentTicks([1, 2, 4, 5], 2)).toEqual({ prev: 1, next: 4, index: 1 });
  });

  it("stops at either end", () => {
    expect(adjacentTicks([1, 2, 3], 1)).toEqual({ prev: null, next: 2, index: 0 });
    expect(adjacentTicks([1, 2, 3], 3)).toEqual({ prev: 2, next: null, index: 2 });
  });

  it("finds the nearest neighbours of a tick the journal does not list", () => {
    expect(adjacentTicks([1, 2, 5, 6], 4)).toEqual({ prev: 2, next: 5, index: -1 });
  });

  it("does not depend on the journal's order", () => {
    expect(adjacentTicks([3, 1, 2], 2)).toEqual({ prev: 1, next: 3, index: 1 });
  });
});

describe("TickOverlay", () => {
  let container: HTMLDivElement;
  let root: Root;
  let picked: number[];
  let closed: number;

  beforeEach(() => {
    globalThis.IS_REACT_ACT_ENVIRONMENT = true;
    picked = [];
    closed = 0;
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  async function render(tick: number) {
    const client = new QueryClient({
      defaultOptions: { queries: { retry: false, gcTime: 0 } },
    });
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <TickOverlay
            slug="brigado"
            sslug="brl_mm"
            sessionNum={5}
            tick={tick}
            onSelectTick={(t) => picked.push(t)}
            onClose={() => closed++}
            className=""
          />
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 5; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }
  }

  const press = (key: string, init: KeyboardEventInit = {}, target: EventTarget = window) =>
    act(() => {
      target.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true, ...init }));
    });

  it("steps to the neighbouring ticks with the arrow keys", async () => {
    JOURNAL = journal([10, 11, 12]);
    await render(11);

    press("ArrowLeft");
    press("ArrowRight");
    expect(picked).toEqual([10, 12]);
  });

  it("does nothing past either end", async () => {
    JOURNAL = journal([10, 11]);
    await render(11);

    press("ArrowRight");
    expect(picked).toEqual([]);
    expect(
      container.querySelector<HTMLButtonElement>("[data-tick-next]")!.disabled,
    ).toBe(true);
  });

  it("leaves arrows alone while the reader types or holds a modifier", async () => {
    JOURNAL = journal([10, 11, 12]);
    await render(11);

    const input = document.createElement("input");
    container.appendChild(input);
    press("ArrowLeft", {}, input);
    press("ArrowLeft", { metaKey: true });
    expect(picked).toEqual([]);
  });

  it("steps with the header buttons and shows where it is in the run", async () => {
    JOURNAL = journal([10, 11, 12]);
    await render(11);

    expect(container.querySelector("[data-tick-position]")!.textContent).toBe("2/3");
    act(() => container.querySelector<HTMLElement>("[data-tick-prev]")!.click());
    act(() => container.querySelector<HTMLElement>("[data-tick-next]")!.click());
    expect(picked).toEqual([10, 12]);
  });

  it("closes from the X", async () => {
    JOURNAL = journal([1]);
    await render(1);

    act(() =>
      container.querySelector<HTMLElement>('[aria-label="Close tick"]')!.click(),
    );
    expect(closed).toBe(1);
  });
});
