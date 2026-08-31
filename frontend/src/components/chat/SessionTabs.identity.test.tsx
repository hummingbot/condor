/**
 * A session tab names *who* is answering, and nothing else (READ-275).
 *
 * The tab strip sits ~40px left of `BrainPicker` and `SessionServerChip`, the
 * two controls that own the model and the server. It used to print both again:
 * an unbound chat fell back to a model label hand-truncated to `Claude (ACP)...`
 * and appended ` · {server_name}` inside a 140px truncate, so the row said the
 * same two things twice and the tab's copies were the unreadable ones.
 *
 * These cases pin the identity rule the tab now follows: the bound Agent's
 * name, or `Condor` when nothing is bound — never a model, never a server — and
 * the server kept reachable in the tooltip, where a background tab still has it
 * even though its chip is off screen.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatSlot } from "@/hooks/useChatSocket";

import { SessionTabs } from "./SessionTabs";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function slot(info: Partial<ChatSlot["info"]>): ChatSlot {
  return {
    info: { slot_id: "s1", agent_key: "claude_acp", ...info },
    messages: [],
  };
}

async function render(slots: ChatSlot[]) {
  await act(async () => {
    root.render(
      <SessionTabs
        slots={slots}
        activeSlotId={slots[0]?.info.slot_id ?? null}
        isSlotStreaming={() => false}
        permissionRequests={{}}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
  });
}

/** The tabs themselves — the close X is a `role="button"` span, not a button. */
const tabs = () => Array.from(container.querySelectorAll("button"));

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

describe("SessionTabs identity", () => {
  it("calls an unbound session Condor rather than the model", async () => {
    // The backend labels an unbound session "Condor"; an older one sends none
    // at all, and the tab has to land on the same word either way — it is what
    // `ChatRail` calls the very same conversation, one click away.
    await render([slot({ agent_slug: "", agent_key: "claude_acp" })]);

    expect(tabs()[0].textContent).toContain("Condor");
    expect(tabs()[0].textContent).not.toContain("Claude");
    // Nothing hand-truncates a label any more: no promise of a longer name.
    expect(tabs()[0].textContent).not.toContain("...");
  });

  it("names a bound Agent, and never its server", async () => {
    await render([
      slot({ agent_slug: "arbitrage", label: "Arbitrage", server_name: "brigado_2" }),
    ]);

    expect(tabs()[0].textContent).toContain("Arbitrage");
    // `SessionServerChip` is the row's one place for the server.
    expect(tabs()[0].textContent).not.toContain("brigado_2");
  });

  it("keeps the server reachable in the tooltip of a background tab", async () => {
    await render([
      slot({ slot_id: "a", agent_slug: "arb", label: "Arbitrage", server_name: "brigado_2" }),
      slot({ slot_id: "b", agent_slug: "", label: "Condor" }),
    ]);

    expect(tabs()[0].getAttribute("title")).toBe("Arbitrage — brigado_2");
    // No server, no dangling separator.
    expect(tabs()[1].getAttribute("title")).toBe("Condor");
  });

  it("still numbers two chats with the same agent", async () => {
    await render([
      slot({ slot_id: "a", agent_slug: "arb", label: "Arbitrage" }),
      slot({ slot_id: "b", agent_slug: "arb", label: "Arbitrage", server_name: "brigado_2" }),
    ]);

    expect(tabs()[0].textContent).not.toContain("#");
    expect(tabs()[1].textContent).toContain("#2");
    expect(tabs()[1].getAttribute("title")).toBe("Arbitrage #2 — brigado_2");
  });
});
