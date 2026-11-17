type MessageHandler = (channel: string, data: unknown, ts: number) => void;
type ConnectHandler = () => void;
type AuthFailureHandler = () => void;

/**
 * Consecutive failed opens between two session checks. The server refuses a
 * dead token before `accept`, which reaches the browser as a plain failed
 * handshake (close code 1006) with no reason to read — so after this many in a
 * row the socket asks `/auth/me` whether its token is still good (FD-01).
 */
const AUTH_PROBE_EVERY = 3;

/**
 * Sentinel subprotocol that marks the JWT-carrying handshake. The token is
 * sent as the second `Sec-WebSocket-Protocol` value instead of in the URL
 * query string, so it never lands in proxy/access logs or browser history.
 * The server echoes back this sentinel (never the token) on accept.
 */
export const WS_AUTH_SUBPROTOCOL = "condor-jwt";

export class CondorWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private token: string;
  private handlers: Set<MessageHandler> = new Set();
  private connectHandlers: Set<ConnectHandler> = new Set();
  private channels: Set<string> = new Set();
  private _channelExtras: Map<string, Record<string, unknown> | undefined> = new Map();
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private shouldConnect = false;
  private authFailureHandlers: Set<AuthFailureHandler> = new Set();
  /** Candle depth per channel, replayed with the subscribe on reconnect (FD-10). */
  private _candleDurations: Map<string, number> = new Map();
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  /** Closes since the last successful open. */
  private failedOpens = 0;
  /** Increments on each successful reconnect */
  version = 0;

  constructor(token: string) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.url = `${proto}//${window.location.host}/api/v1/ws`;
    this.token = token;
  }

  /** Whether the underlying socket is open right now (diagnostics only). */
  get isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  connect() {
    this.shouldConnect = true;
    window.addEventListener("online", this._wake);
    document.addEventListener("visibilitychange", this._wake);
    this._connect();
  }

  disconnect() {
    this.shouldConnect = false;
    this._stopWaiting();
    this.ws?.close();
    this.ws = null;
  }

  subscribe(channel: string, extras?: Record<string, unknown>) {
    this.channels.add(channel);
    this._channelExtras.set(channel, extras);
    this._send(this._subscribeFrame(channel));
  }

  unsubscribe(channel: string) {
    this.channels.delete(channel);
    this._channelExtras.delete(channel);
    this._candleDurations.delete(channel);
    this._send({ action: "unsubscribe", channel });
  }

  /**
   * Send a duration update for a candle channel without re-subscribing.
   *
   * Remembered as well as sent: a frame sent while the socket is not open is
   * dropped, and the resubscribe after a reconnect would otherwise fall back
   * to the server's default depth.
   */
  setCandleDuration(channel: string, duration: number) {
    this._candleDurations.set(channel, duration);
    this._send({ action: "set_candle_duration", channel, duration });
  }

  /**
   * Register a callback fired when the server no longer accepts this socket's
   * token. The socket has stopped reconnecting by then.
   */
  onAuthFailure(handler: AuthFailureHandler) {
    this.authFailureHandlers.add(handler);
    return () => {
      this.authFailureHandlers.delete(handler);
    };
  }

  onMessage(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  /** Register a callback fired immediately on each (re)connect. */
  onConnect(handler: ConnectHandler) {
    this.connectHandlers.add(handler);
    return () => {
      this.connectHandlers.delete(handler);
    };
  }

  private _subscribeFrame(channel: string) {
    const duration = this._candleDurations.get(channel);
    return {
      action: "subscribe",
      channel,
      ...this._channelExtras.get(channel),
      ...(duration !== undefined ? { duration } : {}),
    };
  }

  private _connect() {
    if (!this.shouldConnect) return;

    const ws = new WebSocket(this.url, [WS_AUTH_SUBPROTOCOL, this.token]);
    this.ws = ws;

    ws.onopen = () => {
      this.reconnectDelay = 1000;
      this.failedOpens = 0;
      this.version++;
      // Re-subscribe to all channels (with original extras like duration)
      for (const ch of this.channels) {
        this._send(this._subscribeFrame(ch));
      }
      // Notify connect listeners (triggers React state update for dependents)
      for (const handler of this.connectHandlers) {
        handler();
      }
    };

    ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        for (const handler of this.handlers) {
          handler(msg.channel, msg.data, msg.ts);
        }
      } catch {
        // ignore parse errors
      }
    };

    ws.onclose = () => {
      // A socket this instance already replaced (see `_wake`) or dropped: its
      // close must not schedule a second connection beside the live one.
      if (this.ws !== ws) return;
      if (!this.shouldConnect) return;
      this.failedOpens++;
      if (this.failedOpens % AUTH_PROBE_EVERY === 0) {
        void this._checkSession();
      }
      // Jittered, so every tab does not redial a restarted server in step.
      const delay = this.reconnectDelay * (0.5 + Math.random() * 0.5);
      this.reconnectTimer = setTimeout(() => {
        this.reconnectTimer = null;
        this._connect();
      }, delay);
      this.reconnectDelay = Math.min(
        this.reconnectDelay * 2,
        this.maxReconnectDelay,
      );
    };

    ws.onerror = () => {
      ws.close();
    };
  }

  /**
   * The network came back, or the tab became visible: reconnect now instead of
   * waiting out a backoff that may have grown to 30 s while nobody was looking
   * (FD-09). An arrow property so the same reference can be unregistered.
   */
  private _wake = () => {
    if (!this.shouldConnect) return;
    if (document.visibilityState === "hidden") return;
    const state = this.ws?.readyState;
    if (state === WebSocket.OPEN || state === WebSocket.CONNECTING) return;
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    this.reconnectDelay = 1000;
    this._connect();
  };

  private _stopWaiting() {
    window.removeEventListener("online", this._wake);
    document.removeEventListener("visibilitychange", this._wake);
    if (this.reconnectTimer !== null) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  /**
   * Ask `/auth/me` whether this socket's token is still accepted, and stop
   * redialling when it is not — the same handshake can never succeed. Any
   * other answer (the server is down, a proxy 502) leaves the loop running.
   */
  private async _checkSession() {
    let status: number;
    try {
      const res = await fetch("/api/v1/auth/me", {
        headers: { Authorization: `Bearer ${this.token}` },
      });
      status = res.status;
    } catch {
      return;
    }
    if (status !== 401 || !this.shouldConnect || this.isOpen) return;
    this.disconnect();
    for (const handler of this.authFailureHandlers) {
      handler();
    }
  }

  private _send(data: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }
}
