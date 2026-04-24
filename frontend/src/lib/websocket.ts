type MessageHandler = (channel: string, data: unknown, ts: number) => void;

export class CondorWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Set<MessageHandler> = new Set();
  private channels: Set<string> = new Set();
  private _channelExtras: Map<string, Record<string, unknown> | undefined> = new Map();
  private reconnectDelay = 1000;
  private maxReconnectDelay = 30000;
  private shouldConnect = false;
  /** Increments on each successful reconnect */
  version = 0;

  constructor(token: string) {
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    this.url = `${proto}//${window.location.host}/api/v1/ws?token=${token}`;
  }

  connect() {
    this.shouldConnect = true;
    this._connect();
  }

  disconnect() {
    this.shouldConnect = false;
    this.ws?.close();
    this.ws = null;
  }

  subscribe(channel: string, extras?: Record<string, unknown>) {
    this.channels.add(channel);
    this._channelExtras.set(channel, extras);
    this._send({ action: "subscribe", channel, ...extras });
  }

  unsubscribe(channel: string) {
    this.channels.delete(channel);
    this._channelExtras.delete(channel);
    this._send({ action: "unsubscribe", channel });
  }

  /** Send a duration update for a candle channel without re-subscribing. */
  setCandleDuration(channel: string, duration: number) {
    this._send({ action: "set_candle_duration", channel, duration });
  }

  onMessage(handler: MessageHandler) {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  private _connect() {
    if (!this.shouldConnect) return;

    this.ws = new WebSocket(this.url);

    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      this.version++;
      // Re-subscribe to all channels (with original extras like duration)
      for (const ch of this.channels) {
        const extras = this._channelExtras.get(ch);
        this._send({ action: "subscribe", channel: ch, ...extras });
      }
    };

    this.ws.onmessage = (ev) => {
      try {
        const msg = JSON.parse(ev.data);
        for (const handler of this.handlers) {
          handler(msg.channel, msg.data, msg.ts);
        }
      } catch {
        // ignore parse errors
      }
    };

    this.ws.onclose = () => {
      if (this.shouldConnect) {
        setTimeout(() => this._connect(), this.reconnectDelay);
        this.reconnectDelay = Math.min(
          this.reconnectDelay * 2,
          this.maxReconnectDelay,
        );
      }
    };

    this.ws.onerror = () => {
      this.ws?.close();
    };
  }

  private _send(data: unknown) {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }
}
