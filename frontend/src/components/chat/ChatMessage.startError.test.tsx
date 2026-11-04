/**
 * How a `start_error` note reads once its own retry has moved it on.
 *
 * `StartErrorNote` used to have one shape: the backend's raw failure text
 * plus a picker, forever — a retry that worked underneath it left the same
 * alarming block on screen, as if nothing had happened. It now has three: the
 * plain failure (unchanged), a quiet one-line resolution with no trace of the
 * failure left on screen, and an in-flight line while the retry is still
 * being answered. A retry that fails again drops straight back to the plain
 * failure shape, with the new error and the picker both live again.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "@/hooks/useChatSocket";
import type { AgentBindingOption, ChatAgentOption } from "@/lib/api";
import { ChatMessageView } from "./ChatMessage";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const AGENTS: ChatAgentOption[] = [
  { key: "openrouter:x/y", label: "OpenRouter: x/y" },
  { key: "claude-sonnet-5", label: "Claude (ACP) — Sonnet" },
];
const BINDINGS: AgentBindingOption[] = [
  {
    slug: "backpack_mm",
    name: "Backpack MM",
    description: "",
    when_to_consult: "",
    agent_key: "openrouter:x/y",
  },
];

function note(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: "m1",
    role: "system",
    kind: "start_error",
    text: "OPENROUTER_API_KEY is not set.",
    toolCalls: [],
    ts: 1_756_000_000,
    attemptedAgentKey: "openrouter:x/y",
    attemptedAgentSlug: "backpack_mm",
    ...overrides,
  };
}

let container: HTMLDivElement;
let root: Root;

function render(msg: ChatMessage, onRetryModel = vi.fn()) {
  act(() => {
    root.render(
      <ChatMessageView
        message={msg}
        brainOptions={AGENTS}
        agentBindings={BINDINGS}
        defaultAgentKey="claude-sonnet-5"
        onRetryModel={onRetryModel}
      />,
    );
  });
  return onRetryModel;
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

describe("the plain failure", () => {
  it("shows the backend's own text and offers the default and a picker", () => {
    render(note({}));
    expect(container.textContent).toContain("OPENROUTER_API_KEY is not set.");
    expect(container.textContent).toContain("Use Sonnet");
    expect(container.querySelector('button[aria-haspopup="listbox"]')).not.toBeNull();
  });

  it("retries with the note's own id, so the outcome lands back on this note", () => {
    const onRetryModel = render(note({}));
    const useDefault = Array.from(container.querySelectorAll("button")).find((b) =>
      b.textContent?.includes("Sonnet"),
    )!;
    act(() => useDefault.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(onRetryModel).toHaveBeenCalledWith("claude-sonnet-5", "m1");
  });

  it("offers no retry at all when the surface passes none", () => {
    act(() => {
      root.render(
        <ChatMessageView message={note({})} brainOptions={AGENTS} agentBindings={BINDINGS} />,
      );
    });
    // Copying the failure text is still on offer (`TurnActions`, every note
    // gets it) — only the retry controls, which need `onRetryModel`, are gone.
    expect(container.querySelector('button[aria-haspopup="listbox"]')).toBeNull();
    expect(container.textContent).not.toContain("Use ");
    expect(container.textContent).toContain("OPENROUTER_API_KEY is not set.");
  });
});

describe("a bound Agent's failure, with no concrete attempted key", () => {
  it("still offers the one-click default, even when the guessed label matches it", () => {
    // `attemptedAgentKey` empty is the real shape for a bound Agent (the
    // backend resolves "" to "whatever is bound" at spawn time and never
    // reports back which model that turned out to be) — chat_ws.py's
    // start-failure frame carries no `agent_key` to check this against. Here
    // the binding's own cached `agent_key` happens to read as the same model
    // the install falls back to by default — the resolution used to treat
    // that coincidence as proof the two are the same spawn, and hid the only
    // working fix.
    const bindingsMatchingDefault: AgentBindingOption[] = [
      { ...BINDINGS[0], agent_key: "claude-sonnet-5" },
    ];
    act(() => {
      root.render(
        <ChatMessageView
          message={note({ attemptedAgentKey: "", attemptedAgentSlug: "backpack_mm" })}
          brainOptions={AGENTS}
          agentBindings={bindingsMatchingDefault}
          defaultAgentKey="claude-sonnet-5"
          onRetryModel={vi.fn()}
        />,
      );
    });
    expect(container.textContent).toContain("Use Sonnet");
  });
});

describe("a retry in flight", () => {
  it("says so instead of leaving the old failure sitting there", () => {
    render(note({ retrying: true, retryTargetKey: "claude-sonnet-5" }));
    // The target resolves through the bound Agent it belongs to, same as the
    // picker's own trigger would name it.
    expect(container.textContent).toContain("Switching to Backpack MM · Sonnet");
    expect(container.textContent).not.toContain("OPENROUTER_API_KEY");
    expect(container.querySelector('button[aria-haspopup="listbox"]')).toBeNull();
  });

  it("still says so without a named target", () => {
    render(note({ retrying: true }));
    expect(container.textContent).toContain("Switching…");
  });
});

describe("a retry that resolved", () => {
  it("reads as one quiet line naming the model now answering, with no picker", () => {
    render(
      note({
        resolved: true,
        resolvedAgentKey: "claude-sonnet-5",
        resolvedAgentSlug: "",
      }),
    );
    expect(container.textContent).toContain("Switched to");
    expect(container.textContent).toContain("Sonnet");
    expect(container.textContent).toContain("ready");
    expect(container.querySelector('button[aria-haspopup="listbox"]')).toBeNull();
    // Nothing of the failure survives the switch: no error heading, no
    // original failure text, no disclosure to reopen it from.
    expect(container.textContent).not.toContain("Error");
    expect(container.textContent).not.toContain("OPENROUTER_API_KEY");
    expect(container.querySelector("details")).toBeNull();
  });

  it("names a bound Agent's own label, the same resolution the trigger uses", () => {
    render(
      note({
        resolved: true,
        resolvedAgentKey: "claude-sonnet-5",
        resolvedAgentSlug: "backpack_mm",
      }),
    );
    expect(container.textContent).toContain("Backpack MM");
  });
});

describe("a retry that failed again", () => {
  it("falls back to the plain failure shape with the new error and a picker", () => {
    const failedAgain = note({
      text: "claude-sonnet-5 is also not configured",
      attemptedAgentKey: "claude-sonnet-5",
      resolved: false,
      retrying: false,
    });
    render(failedAgain);
    expect(container.textContent).toContain("claude-sonnet-5 is also not configured");
    expect(container.textContent).not.toContain("OPENROUTER_API_KEY");
    expect(container.querySelector('button[aria-haspopup="listbox"]')).not.toBeNull();
    // Offering the model that *just* failed again as "the fix" would repeat
    // the same error, so the default fallback button is gone this time.
    expect(container.textContent).not.toContain("Use Sonnet");
  });
});
