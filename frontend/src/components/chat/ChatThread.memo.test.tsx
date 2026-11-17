/**
 * Settled turns hold still while an answer streams (FU-04).
 *
 * `ChatMessageView` is memoised and its settled branch runs `ReactMarkdown`,
 * which parses the whole text on every render. The page around the thread
 * re-renders on every 50 ms flush, so each prop the thread forwards to a
 * message has to keep its identity across a flush — and `onRetryModel` did
 * not: the page passed it as an inline arrow, so the memo never held and every
 * settled message was re-parsed twenty times a second.
 *
 * What is pinned: a re-render of the host with a fresh inline `onRetryModel`
 * re-parses no settled message, and the wrapper the thread hands down still
 * calls the latest one.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act, useEffect, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage, ChatSlot } from "@/hooks/useChatSocket";

vi.mock("./ChatInput", () => ({
  ChatInput: () => null,
}));

/** Every markdown pass over a settled message, by its text. */
const parses: string[] = [];

vi.mock("react-markdown", () => ({
  default: ({ children }: { children: string }) => {
    parses.push(children);
    return <div data-testid="markdown">{children}</div>;
  },
}));

/** The retry callback the `start_error` note was handed, to call from a test. */
let noteRetry: ((agentKey: string, messageId: string) => void) | undefined;

vi.mock("./ChatMessage", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./ChatMessage")>();
  const { memo, useEffect } = await import("react");
  const Real = actual.ChatMessageView;
  return {
    ...actual,
    // Same memo boundary as the real one — the spy only reads the prop, and
    // only when it commits: a new identity is the one thing that makes it.
    ChatMessageView: memo(function Spy(props: React.ComponentProps<typeof Real>) {
      const { message, onRetryModel } = props;
      useEffect(() => {
        if (message.kind === "start_error") noteRetry = onRetryModel;
      });
      return <Real {...props} />;
    }),
  };
});

const { ChatThread } = await import("./ChatThread");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const MESSAGES: ChatMessage[] = [
  { id: "m1", role: "user", text: "first question", toolCalls: [] },
  { id: "m2", role: "assistant", text: "first **answer**", toolCalls: [] },
  { id: "m3", role: "assistant", text: "second answer", toolCalls: [] },
  { id: "m4", role: "system", kind: "start_error", text: "no key", toolCalls: [] },
];

const SLOT: ChatSlot = {
  info: { slot_id: "s1", conversation_id: "s1", agent_key: "claude-code" },
  messages: MESSAGES,
};

const AGENTS = [{ key: "claude-code", label: "Claude Code" }];

let flush: () => void = () => {};
let hostRenders = 0;
const retried: string[] = [];

/** The page around the thread, wired as `AgentChatTab` wires it. */
function Host() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    hostRenders++;
  });
  useEffect(() => {
    flush = () => setTick((t) => t + 1);
  }, []);
  return (
    <ChatThread
      slot={SLOT}
      agents={AGENTS}
      isStreaming={false}
      permissionRequest={null}
      onResolvePermission={() => {}}
      onSend={() => {}}
      onAbort={() => {}}
      // A fresh arrow per render, closing over this render's state.
      onRetryModel={(agentKey, messageId) =>
        retried.push(`${tick}:${agentKey}:${messageId}`)
      }
    />
  );
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  parses.length = 0;
  retried.length = 0;
  hostRenders = 0;
  noteRetry = undefined;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("the transcript across a stream flush (FU-04)", () => {
  it("re-parses no settled message when the page re-renders", async () => {
    await act(async () => root.render(<Host />));
    expect(parses).toContain("first **answer**");
    expect(parses).toContain("second answer");
    const before = parses.length;
    const hostBefore = hostRenders;

    for (let i = 0; i < 20; i++) {
      await act(async () => flush());
    }

    // The page did re-render twenty times — no message did.
    expect(hostRenders).toBe(hostBefore + 20);
    expect(parses.length).toBe(before);
  });

  it("still calls the latest onRetryModel through the stable wrapper", async () => {
    await act(async () => root.render(<Host />));
    const first = noteRetry;
    expect(first).toBeTypeOf("function");

    await act(async () => flush());
    await act(async () => flush());

    // One identity across renders, and it reaches this render's closure.
    expect(noteRetry).toBe(first);
    act(() => noteRetry!("gpt", "m4"));
    expect(retried).toEqual(["2:gpt:m4"]);
  });
});
