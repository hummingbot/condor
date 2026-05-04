/**
 * Singleton candle data store.
 *
 * Manages candle collections keyed by WS channel, reference-counted WS
 * subscriptions with deferred teardown, and listener notifications for
 * React hooks.  Completely framework-agnostic — the React bridge lives
 * in `useCandleStore.ts`.
 */

import type { CandleData } from "./api";
import type { CondorWebSocket } from "./websocket";

// ── Types ──

interface CandleCollection {
  /** Timestamp-keyed map for O(1) dedup / upsert */
  map: Map<number, CandleData>;
  /** Lazily recomputed sorted array (null = dirty) */
  sorted: CandleData[] | null;
  maxSize: number;
  lastAccessed: number;
}

interface Subscription {
  refCount: number;
  teardownTimer: ReturnType<typeof setTimeout> | null;
}

type Listener = (candles: CandleData[]) => void;

// ── Constants ──

const MAX_COLLECTION_SIZE = 2000;
const MAX_COLLECTIONS = 20;
const TEARDOWN_DELAY_MS = 5 * 60 * 1000; // 5 minutes
const IDLE_CLEANUP_MS = 10 * 60 * 1000; // 10 minutes

// ── Helpers ──

function normalizeTimestamp(ts: number): number {
  return ts > 1e12 ? ts / 1000 : ts;
}

function sortedFromMap(map: Map<number, CandleData>): CandleData[] {
  return Array.from(map.values()).sort((a, b) => a.timestamp - b.timestamp);
}

function evictOldest(col: CandleCollection): void {
  if (col.map.size <= col.maxSize) return;
  // Sort keys ascending, remove the oldest ones
  const timestamps = Array.from(col.map.keys()).sort((a, b) => a - b);
  const excess = timestamps.length - col.maxSize;
  for (let i = 0; i < excess; i++) {
    col.map.delete(timestamps[i]);
  }
  col.sorted = null;
}

// ── Singleton ──

class CandleStore {
  collections = new Map<string, CandleCollection>();
  subscriptions = new Map<string, Subscription>();
  listeners = new Map<string, Set<Listener>>();
  /** Monotonic-ish timestamp of last data update per channel */
  lastUpdateTime = new Map<string, number>();

  private ws: CondorWebSocket | null = null;
  private wsCleanup: (() => void) | null = null;
  /** Tracks insertion order for LRU eviction */
  private accessOrder: string[] = [];

  constructor() {
    setInterval(() => this._cleanupIdle(), 60_000);
  }

  // ── WS wiring ──

  setWs(ws: CondorWebSocket | null): void {
    // Tear down previous handler
    if (this.wsCleanup) {
      this.wsCleanup();
      this.wsCleanup = null;
    }
    this.ws = ws;
    if (!ws) return;

    this.wsCleanup = ws.onMessage((channel: string, data: unknown) => {
      if (!channel.startsWith("candles:")) return;
      const payload = data as {
        type: string;
        candle?: CandleData;
        data?: CandleData[];
        message?: string;
      };

      if (payload.type === "candle_update" && payload.candle) {
        this._upsertOne(channel, payload.candle);
        this._notify(channel);
      } else if (payload.type === "candles" && payload.data?.length) {
        this._upsertMany(channel, payload.data);
        this._notify(channel);
      }
      // errors are still handled by useWebSocket for status display
    });

    // Re-subscribe active channels to trigger a fresh snapshot now that the
    // message handler is registered. The WS onopen re-subscribe may have fired
    // before setWs was called, so the initial snapshot would have been missed.
    for (const [key, sub] of this.subscriptions) {
      if (sub.refCount > 0) {
        ws.subscribe(key);
      }
    }
  }

  // ── Public API ──

  /**
   * Subscribe to a candle channel. Returns cached candles instantly (may be empty).
   * Caller must call `unsubscribe` when done.
   */
  subscribe(key: string): CandleData[] {
    let sub = this.subscriptions.get(key);
    if (!sub) {
      sub = { refCount: 0, teardownTimer: null };
      this.subscriptions.set(key, sub);
    }

    // Cancel pending teardown
    if (sub.teardownTimer !== null) {
      clearTimeout(sub.teardownTimer);
      sub.teardownTimer = null;
    }

    sub.refCount++;

    // Ensure WS subscription (idempotent on the WS side)
    if (sub.refCount === 1 && this.ws) {
      this.ws.subscribe(key);
    }

    this._touchAccess(key);
    return this.getCandles(key);
  }

  /**
   * Decrement refCount. At 0: start deferred teardown timer.
   * Collection stays in memory regardless.
   */
  unsubscribe(key: string): void {
    const sub = this.subscriptions.get(key);
    if (!sub) return;

    sub.refCount = Math.max(0, sub.refCount - 1);
    if (sub.refCount > 0) return;

    // Start deferred teardown
    sub.teardownTimer = setTimeout(() => {
      sub.teardownTimer = null;
      if (sub.refCount === 0 && this.ws) {
        this.ws.unsubscribe(key);
        this.subscriptions.delete(key);
      }
    }, TEARDOWN_DELAY_MS);
  }

  /** Merge externally-fetched candles (e.g. REST backfill for range changes). */
  mergeCandles(key: string, candles: CandleData[]): void {
    this._upsertMany(key, candles);
    this._notify(key);
  }

  /** Send a duration hint to the backend without re-subscribing the WS channel. */
  setDuration(key: string, durationSeconds: number): void {
    if (this.ws) {
      this.ws.setCandleDuration(key, durationSeconds);
    }
  }

  /** Get sorted candles for a channel. */
  getCandles(key: string): CandleData[] {
    const col = this.collections.get(key);
    if (!col) return [];
    this._touchAccess(key);
    if (col.sorted === null) {
      col.sorted = sortedFromMap(col.map);
    }
    return col.sorted;
  }

  /** Returns ms since last data update for the given channel, or Infinity if never updated. */
  getLastUpdateAge(key: string): number {
    const t = this.lastUpdateTime.get(key);
    return t ? Date.now() - t : Infinity;
  }

  /** Register a listener. Returns an unsubscribe function. */
  onUpdate(key: string, callback: Listener): () => void {
    let set = this.listeners.get(key);
    if (!set) {
      set = new Set();
      this.listeners.set(key, set);
    }
    set.add(callback);
    return () => {
      set!.delete(callback);
      if (set!.size === 0) this.listeners.delete(key);
    };
  }

  // ── Internal ──

  private _getOrCreateCollection(key: string): CandleCollection {
    let col = this.collections.get(key);
    if (!col) {
      this._enforceMaxCollections();
      col = {
        map: new Map(),
        sorted: null,
        maxSize: MAX_COLLECTION_SIZE,
        lastAccessed: Date.now(),
      };
      this.collections.set(key, col);
    }
    return col;
  }

  private _upsertOne(key: string, candle: CandleData): void {
    const col = this._getOrCreateCollection(key);
    const ts = normalizeTimestamp(candle.timestamp);
    const normalized = { ...candle, timestamp: ts };
    col.map.set(ts, normalized);
    col.sorted = null;
    col.lastAccessed = Date.now();
    this.lastUpdateTime.set(key, Date.now());
    evictOldest(col);
  }

  private _upsertMany(key: string, candles: CandleData[]): void {
    const col = this._getOrCreateCollection(key);
    for (const c of candles) {
      const ts = normalizeTimestamp(c.timestamp);
      col.map.set(ts, { ...c, timestamp: ts });
    }
    col.sorted = null;
    col.lastAccessed = Date.now();
    this.lastUpdateTime.set(key, Date.now());
    evictOldest(col);
  }

  private _notify(key: string): void {
    const set = this.listeners.get(key);
    if (!set || set.size === 0) return;
    const candles = this.getCandles(key);
    for (const cb of set) {
      cb(candles);
    }
  }

  private _touchAccess(key: string): void {
    const idx = this.accessOrder.indexOf(key);
    if (idx >= 0) this.accessOrder.splice(idx, 1);
    this.accessOrder.push(key);
  }

  private _enforceMaxCollections(): void {
    while (this.collections.size >= MAX_COLLECTIONS && this.accessOrder.length > 0) {
      const oldest = this.accessOrder.shift()!;
      // Don't evict collections with active subscribers
      const sub = this.subscriptions.get(oldest);
      if (sub && sub.refCount > 0) {
        this.accessOrder.push(oldest); // put it back
        // If all are active, just break to avoid infinite loop
        break;
      }
      this.collections.delete(oldest);
      this.listeners.delete(oldest);
    }
  }

  private _cleanupIdle(): void {
    const now = Date.now();
    for (const [key, col] of this.collections) {
      if (now - col.lastAccessed > IDLE_CLEANUP_MS) {
        const sub = this.subscriptions.get(key);
        if (!sub || sub.refCount === 0) {
          this.collections.delete(key);
          this.listeners.delete(key);
          const idx = this.accessOrder.indexOf(key);
          if (idx >= 0) this.accessOrder.splice(idx, 1);
        }
      }
    }
  }
}

// ── Export singleton ──

export const candleStore = new CandleStore();
