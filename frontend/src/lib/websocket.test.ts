/**
 * Reconnect behaviour of the data socket (FD-01, FD-09, FD-10).
 *
 * A fake `WebSocket` and fake timers: what is pinned here is when the client
 * dials, what it replays once it is back, and when it gives up — none of which
 * needs a server.
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CondorWebSocket, WS_AUTH_SUBPROTOCOL } from "./websocket";

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;
  static instances: FakeSocket[] = [];

  readyState = FakeSocket.CONNECTING;
  sent: Record<string, unknown>[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  url: string;
  protocols: string[];

  constructor(url: string, protocols: string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    if (this.readyState === FakeSocket.CLOSED) return;
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.();
  }

  /** Server accepted the handshake. */
  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }
}

const sockets = () => FakeSocket.instances;
const last = () => sockets()[sockets().length - 1];

let fetchMock: ReturnType<typeof vi.fn>;
let ws: CondorWebSocket;

function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { value: state, configurable: true });
  document.dispatchEvent(new Event("visibilitychange"));
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeSocket);
  fetchMock = vi.fn(async () => new Response("{}", { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  // Upper edge of the jitter window, so `reconnectDelay` is the exact wait.
  vi.spyOn(Math, "random").mockReturnValue(1);
  Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  ws = new CondorWebSocket("jwt");
});

afterEach(() => {
  ws.disconnect();
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("connect and replay", () => {
  it("carries the token in the subprotocol, not the URL", () => {
    ws.connect();
    expect(last().protocols).toEqual([WS_AUTH_SUBPROTOCOL, "jwt"]);
    expect(last().url).not.toContain("jwt");
  });

  it("re-subscribes every channel after a reconnect", () => {
    ws.connect();
    last().open();
    ws.subscribe("bots:prod");
    ws.subscribe("orderbook:prod:binance:BTC-USDT", { depth: 20 });

    last().close();
    vi.advanceTimersByTime(1000);
    expect(sockets()).toHaveLength(2);
    last().open();

    expect(last().sent).toEqual([
      { action: "subscribe", channel: "bots:prod" },
      { action: "subscribe", channel: "orderbook:prod:binance:BTC-USDT", depth: 20 },
    ]);
  });

  it("replays the candle duration with the resubscribe (FD-10)", () => {
    const channel = "candles:prod:binance:BTC-USDT:1m";
    ws.connect();
    last().open();
    ws.subscribe(channel);
    ws.setCandleDuration(channel, 86_400);
    expect(last().sent[1]).toEqual({ action: "set_candle_duration", channel, duration: 86_400 });

    last().close();
    vi.advanceTimersByTime(1000);
    last().open();
    expect(last().sent).toEqual([{ action: "subscribe", channel, duration: 86_400 }]);
  });

  it("keeps a duration set before the socket was open (FD-10)", () => {
    const channel = "candles:prod:binance:BTC-USDT:1m";
    ws.connect();
    ws.subscribe(channel);
    ws.setCandleDuration(channel, 3600);
    expect(last().sent).toEqual([]);

    last().open();
    expect(last().sent).toEqual([{ action: "subscribe", channel, duration: 3600 }]);
  });

  it("forgets the duration once the channel is unsubscribed", () => {
    const channel = "candles:prod:binance:BTC-USDT:1m";
    ws.connect();
    last().open();
    ws.subscribe(channel);
    ws.setCandleDuration(channel, 3600);
    ws.unsubscribe(channel);
    ws.subscribe(channel);
    expect(last().sent[last().sent.length - 1]).toEqual({ action: "subscribe", channel });
  });
});

describe("backoff", () => {
  it("doubles the wait between failed attempts and resets it on open", () => {
    ws.connect();
    last().close();
    vi.advanceTimersByTime(999);
    expect(sockets()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets()).toHaveLength(2);

    last().close();
    vi.advanceTimersByTime(1999);
    expect(sockets()).toHaveLength(2);
    vi.advanceTimersByTime(1);
    expect(sockets()).toHaveLength(3);

    last().open();
    last().close();
    vi.advanceTimersByTime(1000);
    expect(sockets()).toHaveLength(4);
  });

  it("jitters the wait downwards, never to zero (FD-09)", () => {
    vi.mocked(Math.random).mockReturnValue(0);
    ws.connect();
    last().close();
    vi.advanceTimersByTime(499);
    expect(sockets()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(sockets()).toHaveLength(2);
  });

  it("does not redial after disconnect", () => {
    ws.connect();
    last().close();
    ws.disconnect();
    vi.advanceTimersByTime(60_000);
    expect(sockets()).toHaveLength(1);
  });
});

describe("wake on online / visible (FD-09)", () => {
  /** Fail enough times that the next retry is several seconds out. */
  function backOff() {
    ws.connect();
    for (let i = 0; i < 2; i++) {
      last().close();
      vi.advanceTimersByTime(30_000);
    }
    last().close();
  }

  it("reconnects at once when the network returns, and only once", () => {
    backOff();
    const before = sockets().length;
    window.dispatchEvent(new Event("online"));
    expect(sockets()).toHaveLength(before + 1);
    // The backoff timer it pre-empted must not dial a second socket.
    vi.advanceTimersByTime(60_000);
    expect(sockets()).toHaveLength(before + 1);
  });

  it("reconnects at once when the tab becomes visible", () => {
    backOff();
    const before = sockets().length;
    setVisibility("hidden");
    expect(sockets()).toHaveLength(before);
    setVisibility("visible");
    expect(sockets()).toHaveLength(before + 1);
  });

  it("leaves an open or connecting socket alone", () => {
    ws.connect();
    window.dispatchEvent(new Event("online"));
    expect(sockets()).toHaveLength(1);
    last().open();
    setVisibility("visible");
    expect(sockets()).toHaveLength(1);
  });

  it("stops listening after disconnect", () => {
    backOff();
    ws.disconnect();
    const before = sockets().length;
    window.dispatchEvent(new Event("online"));
    setVisibility("visible");
    expect(sockets()).toHaveLength(before);
  });
});

describe("dead token (FD-01)", () => {
  async function failOpens(n: number) {
    for (let i = 0; i < n; i++) {
      last().close();
      await vi.advanceTimersByTimeAsync(0);
      if (i < n - 1) await vi.advanceTimersByTimeAsync(30_000);
    }
  }

  it("stops reconnecting once /auth/me refuses the token", async () => {
    fetchMock.mockResolvedValue(new Response("{}", { status: 401 }));
    const onAuthFailure = vi.fn();
    ws.onAuthFailure(onAuthFailure);
    ws.connect();

    await failOpens(2);
    expect(fetchMock).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(30_000);
    await failOpens(1);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/auth/me", {
      headers: { Authorization: "Bearer jwt" },
    });
    expect(onAuthFailure).toHaveBeenCalledTimes(1);

    const before = sockets().length;
    await vi.advanceTimersByTimeAsync(120_000);
    window.dispatchEvent(new Event("online"));
    expect(sockets()).toHaveLength(before);
  });

  it.each([200, 502])("keeps reconnecting when /auth/me answers %s", async (status) => {
    fetchMock.mockResolvedValue(new Response("{}", { status }));
    const onAuthFailure = vi.fn();
    ws.onAuthFailure(onAuthFailure);
    ws.connect();

    await failOpens(3);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onAuthFailure).not.toHaveBeenCalled();
    const before = sockets().length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(sockets()).toHaveLength(before + 1);
  });

  it("keeps reconnecting when the server is unreachable", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    ws.connect();
    await failOpens(3);
    const before = sockets().length;
    await vi.advanceTimersByTimeAsync(30_000);
    expect(sockets()).toHaveLength(before + 1);
  });
});
