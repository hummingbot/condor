/**
 * The transcript's visual language (READ-293).
 *
 * These are contrast and identity rules, not decoration: white on the gold
 * primary measures 2.2:1 in dark and 3.3:1 in light, a dashed border means
 * *placeholder* in every design system, and an answer with no name on it is
 * why a transcript holding two agents could not be scanned. Each of them was
 * reintroduced once by a well-meaning edit, so each is pinned here.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatMessage } from "@/hooks/useChatSocket";
import { AGENT_COLOR_VARS } from "@/lib/agentColor";
import { ChatMessageView } from "./ChatMessage";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function message(partial: Partial<ChatMessage> & { role: ChatMessage["role"] }): ChatMessage {
  return { id: "m1", text: "", toolCalls: [], ts: 1_756_000_000, ...partial };
}

let container: HTMLDivElement;
let root: Root;

function render(msg: ChatMessage, agentName = "Brigado") {
  act(() => {
    root.render(<ChatMessageView message={msg} agentName={agentName} />);
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

describe("an agent's turn", () => {
  it("is named, and owns a gutter in one of the validated series colours", () => {
    render(message({ role: "assistant", text: "The book is thin." }));

    expect(container.textContent).toContain("Brigado");
    const gutter = container.querySelector<HTMLElement>("[style*='border-color']");
    expect(gutter).not.toBeNull();
    expect(AGENT_COLOR_VARS.some((v) => gutter!.style.borderColor.includes(v))).toBe(true);
  });

  it("draws no container of its own — the answer sits on the page ground", () => {
    render(message({ role: "assistant", text: "The book is thin." }));
    expect(container.innerHTML).not.toContain("--color-surface-hover");
  });

  it("exposes a copy action and the time it was said", () => {
    render(message({ role: "assistant", text: "The book is thin." }));

    expect(container.querySelector("button[aria-label='Copy this message']")).not.toBeNull();
    const time = container.querySelector("time");
    expect(time?.textContent).toBeTruthy();
    expect(time?.getAttribute("datetime")).toBe(new Date(1_756_000_000_000).toISOString());
  });
});

describe("the user's turn", () => {
  it("keeps the bubble but never writes white on the gold", () => {
    render(message({ role: "user", text: "how thin?" }));

    const bubble = container.querySelector<HTMLElement>("[class*='--color-primary']");
    expect(bubble?.className).toContain("text-[var(--on-primary)]");
    expect(container.innerHTML).not.toContain("text-white");
  });
});

describe("out-of-band notes", () => {
  const kinds: [string, string][] = [
    ["delegation", "Delegation"],
    ["resume", "Resumed"],
    ["notification", "Notified"],
    ["routine", "Routine"],
  ];

  for (const [kind, label] of kinds) {
    it(`names a ${kind} instead of boxing it in a dashed border`, () => {
      render(message({ role: "system", kind, text: "the run finished" }));

      expect(container.textContent).toContain(label);
      expect(container.innerHTML).not.toContain("border-dashed");
    });
  }

  it("leaves a key-material warning standing out from the quiet ones", () => {
    render(message({ role: "system", kind: "secret_notice", text: "**A key was removed.**" }));

    expect(container.textContent).toContain("Key material");
    expect(container.innerHTML).toContain("amber-500/10");
  });

  it("still renders a handover as a divider, not as a turn", () => {
    render(message({ role: "system", kind: "switch", text: "Switched to Condor" }));

    expect(container.textContent).toContain("Switched to Condor");
    expect(container.querySelector("time")).toBeNull();
  });
});
