/**
 * The model picker's panel is portalled, not `absolute` (CORR-259).
 *
 * It used to hang off the trigger as `absolute right-0 w-64`, which pinned its
 * right edge to the trigger's and drew all 256px of it *leftward*. On
 * /agents/:slug that runs inside `main`'s `overflow-auto`, and leftward
 * overflow of a scroll container is unreachable — no scroll position brings it
 * back — so with a short model label ("Claude Code") the left edge of every
 * option was simply gone. Whether it broke depended on which model happened to
 * be selected, which is why the fix is the portal and not a wider class string.
 *
 * These cases pin the properties that survive a refactor of the menu's
 * contents: the panel is a child of `document.body` at fixed coordinates that
 * never cross the window's left edge, and dismissal + aria live on the
 * primitive rather than on a hand-rolled backdrop.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatAgentOption } from "@/lib/api";
import { BrainPicker } from "./BrainPicker";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const AGENTS: ChatAgentOption[] = [
  { key: "claude-code:", label: "Claude Code" },
  { key: "gemini-cli:", label: "Gemini CLI" },
];

let container: HTMLDivElement;
let root: Root;
let picked: string[];

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <BrainPicker
          agents={AGENTS}
          selectedAgentKey="claude-code:"
          onSelect={(sel) => picked.push(sel.agentKey ?? "")}
          variant="inline"
        />
      </QueryClientProvider>,
    );
  });
}

const trigger = () => container.querySelector("button")!;
/** The option row, wherever in the document it ended up. */
const option = (label: string) =>
  Array.from(document.querySelectorAll("button")).find(
    (b) => b.textContent?.trim() === label,
  );
/** Walk up from an option row to the element `document.body` owns directly. */
const panel = () => {
  let node: HTMLElement | null = option("Gemini CLI") ?? null;
  while (node && node.parentElement !== document.body) node = node.parentElement;
  return node;
};

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("mousedown", { bubbles: true }));
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  picked = [];
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("BrainPicker panel placement", () => {
  it("renders the open panel outside the trigger's subtree, at the window edge", async () => {
    await render();
    await click(trigger());

    const p = panel();
    expect(p).toBeTruthy();
    // Portalled: nothing in the trigger's ancestry can clip it.
    expect(p!.parentElement).toBe(document.body);
    expect(container.contains(p!)).toBe(false);
    expect(p!.style.position).toBe("fixed");
    // Clamped to the viewport rather than run off it to the left.
    expect(parseFloat(p!.style.left)).toBeGreaterThanOrEqual(8);
    expect(p!.style.right).toBe("");
    // The cap travels as an inline height, since a Tailwind max-h-* would lose
    // to it — and the panel is never wider than the room beside it.
    expect(parseFloat(p!.style.maxHeight)).toBeGreaterThan(0);
    expect(parseFloat(p!.style.maxWidth)).toBeGreaterThan(0);
  });

  it("has no hand-rolled backdrop and closes on an outside mousedown", async () => {
    await render();
    await click(trigger());
    expect(panel()).toBeTruthy();

    const outside = document.createElement("div");
    document.body.appendChild(outside);
    await click(outside);

    expect(panel()).toBeNull();
    outside.remove();
  });

  it("announces its state on the trigger and closes after a pick", async () => {
    await render();
    expect(trigger().getAttribute("aria-haspopup")).toBe("listbox");
    expect(trigger().getAttribute("aria-expanded")).toBe("false");

    await click(trigger());
    expect(trigger().getAttribute("aria-expanded")).toBe("true");

    await click(option("Gemini CLI")!);
    expect(picked).toEqual(["gemini-cli:"]);
    expect(panel()).toBeNull();
    expect(trigger().getAttribute("aria-expanded")).toBe("false");
  });
});
