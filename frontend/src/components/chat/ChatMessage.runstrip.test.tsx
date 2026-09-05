/**
 * What the run strip's two props actually govern (READ-330).
 *
 * The comment above `<RunStrip>` used to claim the disclosure collapsed when
 * the answer text started landing, and that this made a lost `prompt_done`
 * harmless. Neither was ever true of the code: `live` alone drives the
 * disclosure, and `thinking` only picks the wording and the spinner. Pinning
 * that here so the prose above the call site cannot drift away from it again.
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

function message(partial: Partial<ChatMessage>): ChatMessage {
  return {
    id: "m1",
    role: "assistant",
    text: "",
    toolCalls: [],
    thought: "Checking the book first.",
    ts: 1_756_000_000,
    ...partial,
  };
}

let container: HTMLDivElement;
let root: Root;

function render(msg: ChatMessage, live: boolean) {
  act(() => {
    root.render(<ChatMessageView message={msg} live={live} agentName="Brigado" />);
  });
}

function strip(): HTMLButtonElement {
  const button = container.querySelector<HTMLButtonElement>("button[aria-expanded]");
  expect(button).not.toBeNull();
  return button!;
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

describe("the run strip's disclosure", () => {
  it("is open while the turn streams, and stays open once the answer lands", () => {
    render(message({}), true);
    expect(strip().getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("Thinking...");

    // The answer arriving is exactly the case the old comment claimed would
    // collapse the strip. It does not: only `live` speaks to the disclosure.
    render(message({ text: "The book is thin." }), true);
    expect(strip().getAttribute("aria-expanded")).toBe("true");
    expect(container.textContent).toContain("Thought");
    expect(container.textContent).not.toContain("Thinking...");
  });

  it("is closed on a settled turn", () => {
    render(message({ text: "The book is thin." }), false);
    expect(strip().getAttribute("aria-expanded")).toBe("false");
  });

  it("holds the user's own choice against the auto behaviour", () => {
    render(message({}), true);
    act(() => {
      strip().dispatchEvent(new MouseEvent("click", { bubbles: true }));
    });
    expect(strip().getAttribute("aria-expanded")).toBe("false");

    render(message({ text: "The book is thin." }), true);
    expect(strip().getAttribute("aria-expanded")).toBe("false");
  });
});
