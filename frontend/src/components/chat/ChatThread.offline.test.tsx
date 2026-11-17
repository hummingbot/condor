/**
 * The transcript says when the chat is offline (FU-03).
 *
 * `isConnected` was returned by the socket hook and read by nothing, so a
 * dead socket looked exactly like a slow agent. The thread now carries a
 * "Reconnecting…" strip — debounced, because the socket drops and returns at
 * once on a token refresh and a strip that flashed each time would be noise —
 * and a user bubble written while offline says whether it is still waiting or
 * was never sent.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, ChatSlot } from "@/hooks/useChatSocket";
import { CONNECTION_LOST_DELAY_MS } from "@/hooks/useConnectionLost";

vi.mock("./ChatInput", () => ({
  ChatInput: () => null,
}));

const { ChatThread } = await import("./ChatThread");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function slot(messages: ChatMessage[]): ChatSlot {
  return {
    info: { slot_id: "s1", conversation_id: "s1", agent_key: "claude-code" },
    messages,
  };
}

const USER: ChatMessage = { id: "m1", role: "user", text: "close it", toolCalls: [] };

let container: HTMLDivElement;
let root: Root;

function render(isConnected: boolean | undefined, messages: ChatMessage[] = [USER]) {
  act(() => {
    root.render(
      <ChatThread
        slot={slot(messages)}
        agents={[]}
        isStreaming={false}
        isConnected={isConnected}
        permissionRequest={null}
        onResolvePermission={() => {}}
        onSend={() => {}}
        onAbort={() => {}}
      />,
    );
  });
}

const strip = () => container.querySelector('[role="status"]');
const wait = (ms: number) => act(() => vi.advanceTimersByTime(ms));

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  vi.useFakeTimers();
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.useRealTimers();
});

describe("the reconnecting strip (FU-03)", () => {
  it("appears once the socket has stayed down, not on a blip", () => {
    render(true);
    expect(strip()).toBeNull();

    render(false);
    wait(CONNECTION_LOST_DELAY_MS - 1);
    expect(strip()).toBeNull();

    wait(1);
    expect(strip()?.textContent).toContain("Reconnecting");
  });

  it("goes the moment the socket is back, and waits again on the next drop", () => {
    render(false);
    wait(CONNECTION_LOST_DELAY_MS);
    expect(strip()).not.toBeNull();

    render(true);
    expect(strip()).toBeNull();

    render(false);
    expect(strip()).toBeNull();
    wait(CONNECTION_LOST_DELAY_MS);
    expect(strip()).not.toBeNull();
  });

  it("never shows for a drop that heals inside the delay", () => {
    render(false);
    wait(CONNECTION_LOST_DELAY_MS - 100);
    render(true);
    wait(CONNECTION_LOST_DELAY_MS);

    expect(strip()).toBeNull();
  });

  it("is absent on a surface that does not report the connection", () => {
    render(undefined);
    wait(CONNECTION_LOST_DELAY_MS * 2);

    expect(strip()).toBeNull();
  });
});

describe("a user bubble written while offline (FU-03)", () => {
  it("says it is queued", () => {
    render(true, [{ ...USER, delivery: "queued" }]);

    expect(container.textContent).toContain("Queued");
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it("says it was not sent", () => {
    render(true, [{ ...USER, delivery: "unsent" }]);

    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      "Not sent",
    );
  });

  it("says nothing when the message simply went", () => {
    render(true);

    expect(container.textContent).not.toContain("Queued");
    expect(container.textContent).not.toContain("Not sent");
  });
});
