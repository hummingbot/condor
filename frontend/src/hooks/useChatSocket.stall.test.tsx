/**
 * A turn whose end is lost stops presenting as running (ARCH-329).
 *
 * Nothing used to bound the wait for `prompt_done`. Every recovery from a lost
 * one was something the user had to do — send another message, press Stop — so
 * a dropped socket or a dead backend left the slot streaming for ever: the run
 * strip expanded with its spinner turning, the composer showing Stop, the brain
 * picker disabled, on a turn that ended long ago.
 *
 * Both directions are pinned here, and the second is the one that matters. A
 * watchdog that settles a *healthy* run turns a working turn into a false
 * failure, which is worse than the bug it fixes — so the "slow but alive" cases
 * below deliberately run far past the timeout, in silences much longer than any
 * the timeout allows, held open by nothing but the heartbeat the wire already
 * carries.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";

const getConversation = vi.fn();
const listConversations = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    listConversations: (...args: unknown[]) => listConversations(...args),
    getSessionOptions: () => Promise.resolve({ default_agent: "claude-code" }),
    getConversation: (...args: unknown[]) => getConversation(...args),
  },
}));

vi.mock("@/lib/auth", () => ({
  useAuth: () => ({ token: "jwt", user: { id: 7 } }),
}));

const { useChatSocket } = await import("./useChatSocket");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

class FakeSocket {
  static last: FakeSocket | null = null;

  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = FakeSocket.OPEN;
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  sent: Record<string, unknown>[] = [];

  constructor() {
    FakeSocket.last = this;
  }
  send(raw: string) {
    this.sent.push(JSON.parse(raw));
  }
  close() {
    this.readyState = FakeSocket.CLOSED;
  }
  deliver(frame: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }
}

const sock = () => FakeSocket.last!;

const holder: { current: ReturnType<typeof useChatSocket> | null } = {
  current: null,
};
const chat = () => holder.current!;

function Harness() {
  const state = useChatSocket();
  useEffect(() => {
    holder.current = state;
  });
  return null;
}

let container: HTMLDivElement;
let root: Root;

async function settle() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

/** Two conversations open at once — one slot stalling must not settle the other. */
const ONE = { slot_id: "s1", conversation_id: "c1", agent_key: "k", alive: true };
const TWO = { slot_id: "s2", conversation_id: "c2", agent_key: "k", alive: true };

const slot = (id: string) => chat().slots.find((s) => s.info.slot_id === id)!;
const messages = (id: string) => slot(id).messages;

/** Deliver a frame and let React commit it. */
function deliver(frame: Record<string, unknown>) {
  act(() => {
    sock().deliver(frame);
  });
}

/** Let the clock run, and let React commit whatever the timers set off. */
function advance(ms: number) {
  act(() => {
    vi.advanceTimersByTime(ms);
  });
}

/**
 * The watchdog's own constants, restated so a test reads as a duration rather
 * than as a magic number. Kept in step with `useChatSocket.ts` by the assertions
 * themselves: shorten the timeout there and the "slow but alive" cases below
 * fail, which is exactly the alarm that change should trip.
 */
const STALL_TIMEOUT_MS = 90_000;
const SWEEP_MS = 5_000;
/** Comfortably past the timeout, and past the sweep that has to notice it. */
const PAST_THE_TIMEOUT = STALL_TIMEOUT_MS + SWEEP_MS;
/** The cadence `ACPClient._stream` heartbeats at while its queue stays empty. */
const HEARTBEAT_MS = 30_000;

/** Put one streamed bubble with a live tool call into `slotId`. */
function streamWithPendingCall(slotId: string, callId: string) {
  deliver({ event: "text_chunk", slot_id: slotId, text: "working" });
  deliver({
    event: "tool_call",
    slot_id: slotId,
    tool_call_id: callId,
    title: "run_code",
    status: "in_progress",
  });
}

/**
 * What `ChatThread` computes and hands `RunStrip` as `live`, which is what
 * `useLiveDisclosure` holds the strip open by. Restated here because that
 * expression is the actual symptom the item was filed for, and asserting the
 * two halves separately would not catch a change that broke the conjunction.
 */
const stripIsLive = (slotId: string) =>
  chat().isSlotStreaming(slotId) && !!messages(slotId)[0]?.open;

async function arrive() {
  act(() => {
    root.render(
      <QueryClientProvider client={new QueryClient()}>
        <ServerContext value={{ server: "moneymaker", setServer: () => {} }}>
          <Harness />
        </ServerContext>
      </QueryClientProvider>,
    );
  });
  act(() => {
    chat().connect();
    sock().onopen?.();
  });
  deliver({ event: "sessions_list", sessions: [ONE, TWO] });
  await settle();
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // `Date` among them: the watchdog compares timestamps rather than counting
  // ticks, so a faked clock has to move for the sweep to conclude anything.
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeSocket);
  FakeSocket.last = null;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  listConversations.mockResolvedValue([]);
  getConversation.mockResolvedValue({ meta: {}, turns: [] });
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("a turn whose prompt_done never arrives", () => {
  it("stops presenting as running once the wire has gone quiet", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");
    expect(chat().isSlotStreaming("s1")).toBe(true);
    expect(stripIsLive("s1")).toBe(true);

    // The frame that ends the turn is simply never sent.
    advance(PAST_THE_TIMEOUT);

    expect(chat().isSlotStreaming("s1")).toBe(false);
    expect(chat().isStreaming).toBe(false);
    // The symptom the item names: the strip is no longer held open.
    expect(stripIsLive("s1")).toBe(false);
  });

  it("settles a turn the socket dropped out from under", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");

    // `onclose` reconnects but has never cleared `streamingSlots`, which is how
    // a mid-answer drop reaches the stuck state with nobody having done
    // anything wrong.
    act(() => {
      sock().onclose?.();
    });
    expect(chat().isSlotStreaming("s1")).toBe(true);

    advance(PAST_THE_TIMEOUT);

    expect(chat().isSlotStreaming("s1")).toBe(false);
  });

  it("leaves the transcript exactly as a reload would render it", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");

    advance(PAST_THE_TIMEOUT);

    // Nothing was settled, closed or marked. A reload of this conversation
    // finds a turn with a call that never reported an ending, so that is what
    // stays on screen — inventing one here would put the live view back out of
    // step with the reloaded one.
    expect(messages("s1")[0].toolCalls[0].status).toBe("in_progress");
    expect(messages("s1")[0].open).toBe(true);
    expect(messages("s1")[0].interrupted).toBeUndefined();
    expect(messages("s1").some((m) => m.kind === "error")).toBe(false);
  });

  it("settles only the conversation that went quiet", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");
    streamWithPendingCall("s2", "t2");

    // s2 keeps breathing all the way through; s1 says nothing more.
    for (let elapsed = 0; elapsed < PAST_THE_TIMEOUT; elapsed += HEARTBEAT_MS) {
      advance(HEARTBEAT_MS);
      deliver({ event: "heartbeat", slot_id: "s2", elapsed_seconds: elapsed / 1000 });
    }
    advance(SWEEP_MS);

    expect(chat().isSlotStreaming("s1")).toBe(false);
    expect(chat().isSlotStreaming("s2")).toBe(true);
    // One live conversation is enough for the chrome that is not tied to a tab.
    expect(chat().isStreaming).toBe(true);
  });

  it("comes back the moment the conversation speaks again", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");
    advance(PAST_THE_TIMEOUT);
    expect(chat().isSlotStreaming("s1")).toBe(false);

    // A late frame — the backend was alive after all, or the socket came back.
    // The turn was never closed, so there is nothing to rebuild.
    deliver({ event: "text_chunk", slot_id: "s1", text: " on it" });

    expect(chat().isSlotStreaming("s1")).toBe(true);
    expect(stripIsLive("s1")).toBe(true);

    // And it settles for good when the turn actually ends.
    deliver({ event: "prompt_done", slot_id: "s1" });
    expect(chat().isSlotStreaming("s1")).toBe(false);
    expect(messages("s1")[0].toolCalls[0].status).toBe("completed");
  });
});

describe("a slow but healthy turn", () => {
  it("never trips the watchdog while heartbeats keep arriving", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");

    // Ten minutes of one long tool call: no text, no tool update, nothing but
    // the heartbeat the backend emits every 30s that its queue stays empty.
    // Many times the timeout, and it must not settle once.
    for (let beat = 1; beat <= 20; beat++) {
      advance(HEARTBEAT_MS);
      deliver({
        event: "heartbeat",
        slot_id: "s1",
        elapsed_seconds: (beat * HEARTBEAT_MS) / 1000,
      });
      expect(chat().isSlotStreaming("s1")).toBe(true);
    }

    expect(stripIsLive("s1")).toBe(true);
    expect(messages("s1")[0].toolCalls[0].status).toBe("in_progress");
  });

  it("is held open by any frame at all, not only by heartbeats", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");

    // A turn that thinks in long bursts, calls a second tool, then answers —
    // each gap on its own nearly the whole timeout, and no heartbeat anywhere.
    const nearly = STALL_TIMEOUT_MS - SWEEP_MS;
    advance(nearly);
    deliver({ event: "thought_chunk", slot_id: "s1", text: "considering" });
    advance(nearly);
    deliver({
      event: "tool_call_update",
      slot_id: "s1",
      tool_call_id: "t1",
      status: "completed",
    });
    advance(nearly);
    deliver({ event: "text_chunk", slot_id: "s1", text: "here" });
    advance(nearly);

    expect(chat().isSlotStreaming("s1")).toBe(true);
    expect(stripIsLive("s1")).toBe(true);
  });

  it("does not re-arm on a conversation that has properly finished", async () => {
    await arrive();
    streamWithPendingCall("s1", "t1");
    deliver({ event: "prompt_done", slot_id: "s1" });
    expect(chat().isSlotStreaming("s1")).toBe(false);

    // Long silence after a clean ending is just an idle chat. The next turn
    // must start live rather than inheriting a verdict from the last one.
    advance(PAST_THE_TIMEOUT * 2);
    deliver({ event: "text_chunk", slot_id: "s1", text: "next answer" });

    expect(chat().isSlotStreaming("s1")).toBe(true);
  });
});
