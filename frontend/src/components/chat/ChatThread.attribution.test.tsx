/**
 * Who a turn is credited to, across a handover (CORR-294).
 *
 * The transcript is allowed to hold two counterparts, and the whole point of
 * naming and colouring each turn is that you can tell them apart. That broke
 * the moment a conversation handed over: the speaker of every turn was walked
 * forward from the conversation's *current* binding, which after a handover is
 * the agent who took over — so the answer given before the switch was labelled
 * and coloured as the newcomer, and the two-agent transcript collapsed back to
 * the one name the gutter exists to avoid.
 *
 * These render the real caller shape — a slot whose label is already the
 * post-handover agent — rather than the hypothetical `initial` the unit test
 * used to pass, because that hypothetical is what let the bug through.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, ChatSlot } from "@/hooks/useChatSocket";
import type { AgentSummary } from "@/lib/api";

// The composer is not what is under test and it opens a react-query fetch of
// its own; the transcript above it is the whole subject here.
vi.mock("./ChatInput", () => ({
  ChatInput: () => null,
}));

const { ChatThread } = await import("./ChatThread");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function msg(partial: Partial<ChatMessage> & { role: ChatMessage["role"] }): ChatMessage {
  return { id: Math.random().toString(36), text: "", toolCalls: [], ...partial };
}

/** Just enough of the roster to put a name to a slug. */
const ROSTER = [
  { slug: "condor", name: "Condor" },
  { slug: "brigado", name: "Brigado" },
] as unknown as AgentSummary[];

/**
 * The transcript as it stands after a handover: Condor answered, Brigado took
 * over, Brigado answered. `label`/`agent_slug` are Brigado's, because the
 * binding is last-write-wins — `switchBrain` rewrites it, and a reload
 * hydrates the same values.
 */
function handoverSlot(messages: ChatMessage[]): ChatSlot {
  return {
    info: {
      slot_id: "s1",
      conversation_id: "s1",
      agent_key: "claude-code",
      agent_slug: "brigado",
      label: "Brigado",
    },
    messages,
  };
}

function render(slot: ChatSlot, roster?: AgentSummary[]) {
  act(() => {
    root.render(
      <ChatThread
        slot={slot}
        agents={[]}
        roster={roster}
        isStreaming={false}
        permissionRequest={null}
        onResolvePermission={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
        boundAgent={{ name: "Brigado" }}
      />,
    );
  });
}

/**
 * The name printed above each answer, in transcript order.
 *
 * Read off the agent-coloured eyebrow: an assistant turn is the only thing in
 * the transcript that draws its label in one of the series variables, so this
 * picks out exactly the turns whose attribution is in question.
 */
function speakerLabels(): string[] {
  return Array.from(container.querySelectorAll<HTMLElement>("span[style*='color']"))
    .filter((el) => el.style.color.includes("--chart-series-"))
    .map((el) => el.textContent?.trim() ?? "");
}

let container: HTMLDivElement;
let root: Root;

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

describe("a transcript that changed hands", () => {
  it("keeps the pre-handover answer on the agent that gave it, live", () => {
    // Live: the bubble was stamped when it opened, from the slot's binding at
    // that moment — "" being the default agent rather than "unknown".
    render(
      handoverSlot([
        msg({ role: "assistant", text: "before", agentSlug: "" }),
        msg({ role: "system", kind: "switch", text: "Switched to Brigado" }),
        msg({ role: "assistant", text: "after", agentSlug: "brigado" }),
      ]),
      ROSTER,
    );

    expect(speakerLabels()).toEqual(["Condor", "Brigado"]);
  });

  it("says the same thing after a reload, off the stored turns", () => {
    // What `turnsToMessages` produces from the backend's `agent_slug` /
    // `agent_key`: the reloaded transcript has to read exactly like the live
    // one, or the names change under the user on refresh.
    render(
      handoverSlot([
        msg({ id: "hist_0", role: "assistant", text: "before", agentSlug: "" }),
        msg({ id: "hist_1", role: "system", kind: "switch", text: "Switched to Brigado" }),
        msg({ id: "hist_2", role: "assistant", text: "after", agentSlug: "brigado" }),
      ]),
      ROSTER,
    );

    expect(speakerLabels()).toEqual(["Condor", "Brigado"]);
  });

  it("still separates the two when the roster has not loaded yet", () => {
    // No roster is a worse label, never a wrong one: the binding may answer
    // for its own slug, and the earlier turn falls back to the default agent's
    // name rather than borrowing Brigado's.
    render(
      handoverSlot([
        msg({ role: "assistant", text: "before", agentSlug: "" }),
        msg({ role: "system", kind: "switch", text: "Switched to Brigado" }),
        msg({ role: "assistant", text: "after", agentSlug: "brigado" }),
      ]),
    );

    expect(speakerLabels()).toEqual(["Condor", "Brigado"]);
  });

  it("leaves an unstamped legacy transcript to the divider walk", () => {
    // Nothing was stamped before the backend recorded attribution, and those
    // conversations must keep rendering exactly as they did.
    render(
      handoverSlot([
        msg({ role: "assistant", text: "before" }),
        msg({ role: "system", kind: "switch", text: "Switched to Brigado" }),
        msg({ role: "assistant", text: "after" }),
      ]),
      ROSTER,
    );

    expect(speakerLabels()).toEqual(["Brigado", "Brigado"]);
  });
});
