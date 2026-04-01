type MessageHandler = (channel: string, data: unknown, ts: number) => void;

export class CondorWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: Set<MessageHandler> = new Set();
  private channels: Set<string> = new Set();
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

  subscribe(channel: string) {
    this.channels.add(channel);
    this._send({ action: "subscribe", channel });
  }

  unsubscribe(channel: string) {
    this.channels.delete(channel);
    this._send({ action: "unsubscribe", channel });
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
      // Re-subscribe to all channels
      for (const ch of this.channels) {
        this._send({ action: "subscribe", channel: ch });
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
