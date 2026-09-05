/**
 * The run strip draws the run in the order it happened (ARCH-330).
 *
 * It used to render the whole of the reasoning and then the whole of the tool
 * list, whatever the model actually did — so "think, call, think, call" came
 * out as "think think, call call" and the reader could not tell which thought
 * led to which call. The order now travels on the message; this pins that the
 * strip renders it, and the two things it must not do while renderering it:
 * invent an order for a turn that has none, and lose a call the order forgot.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatMessage } from "@/hooks/useChatSocket";
import { ChatMessageView } from "./ChatMessage";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const CALLS = [
  { tool_call_id: "t1", title: "get_prices", status: "completed" },
  { tool_call_id: "t2", title: "get_portfolio", status: "completed" },
];

function message(partial: Partial<ChatMessage>): ChatMessage {
  return {
    id: "m1",
    role: "assistant",
    text: "You have $50.",
    toolCalls: CALLS,
    thought: "Check the book. Now the balances.",
    ts: 1_756_000_000,
    ...partial,
  };
}

let container: HTMLDivElement;
let root: Root;

function render(msg: ChatMessage) {
  act(() => {
    root.render(<ChatMessageView message={msg} live agentName="Brigado" />);
  });
}

/** The opened run, one line per row, in the order the DOM holds them. */
function rows(): string[] {
  const strip = container.querySelector("button[aria-expanded='true']");
  expect(strip).not.toBeNull();
  const body = strip!.parentElement!.querySelector("div");
  return Array.from(body!.children).map((el) => el.textContent?.trim() ?? "");
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

describe("the opened run", () => {
  it("interleaves reasoning and calls the way they happened", () => {
    render(
      message({
        events: [
          { type: "thought", text: "Check the book." },
          { type: "tool", id: "t1" },
          { type: "thought", text: "Now the balances." },
          { type: "tool", id: "t2" },
        ],
      }),
    );

    expect(rows()).toEqual([
      "Check the book.",
      "get prices",
      "Now the balances.",
      "get portfolio",
    ]);
  });

  it("falls back to reasoning-then-calls when no order was recorded", () => {
    // Every turn on disk before this existed. The flat render is what the
    // record actually supports; inventing an interleaving for it would be a
    // confident lie about what the agent did.
    render(message({ events: undefined }));

    expect(rows()).toEqual([
      "Check the book. Now the balances.",
      "get prices",
      "get portfolio",
    ]);
  });

  it("still shows a call the order does not name", () => {
    // The strip accounts for every tool that ran. An order that is somehow
    // incomplete may cost the reader the placement of a call; it must never
    // cost them the call.
    render(
      message({
        events: [
          { type: "thought", text: "Check the book." },
          { type: "tool", id: "t2" },
        ],
      }),
    );

    expect(rows()).toEqual(["Check the book.", "get portfolio", "get prices"]);
  });

  it("ignores a step naming a call the turn does not hold", () => {
    render(
      message({
        toolCalls: [CALLS[0]],
        thought: "Check the book.",
        events: [
          { type: "tool", id: "ghost" },
          { type: "thought", text: "Check the book." },
          { type: "tool", id: "t1" },
        ],
      }),
    );

    expect(rows()).toEqual(["Check the book.", "get prices"]);
  });
});
