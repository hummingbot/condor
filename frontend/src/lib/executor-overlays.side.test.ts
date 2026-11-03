/**
 * @vitest-environment jsdom
 *
 * Which way an overlay is drawn (CORR-280).
 *
 * `executor.side` is the only side the backend normalizes: `build_executor_row`
 * folds every encoding through `normalize_executor_side`, while `custom_info`
 * and `config` reach the wire model verbatim. The overlays used to read those raw
 * dicts *first*, and `normSide` only recognized the literals `buy`/`1` -- so the
 * stringified enums and bare words hummingbot really emits (`TradeType.SELL`,
 * `PositionSide.LONG`, `LONG`, `SHORT`) all fell through to "sell". A long was
 * drawn as a short, silently: down arrow, sell colour, stop loss above entry.
 *
 * These tests pin the fixed contract from the outside, through the public
 * `computeMultiOverlays`, for every overlay type that reads a side.
 *
 * jsdom loads no stylesheet, so `getThemeColors()` resolves to its documented
 * fallbacks -- `#22c55e` up, `#ef4444` down -- which is what makes the marker
 * colours assertable here.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExecutorInfo } from "./api";
import { computeMultiOverlays } from "./executor-overlays";

const UP = "#22c55e";
const DOWN = "#ef4444";

function executor(patch: Partial<ExecutorInfo> = {}): ExecutorInfo {
  return {
    id: "e1",
    type: "position",
    connector: "binance_perpetual",
    trading_pair: "SOL-USDC",
    side: "BUY",
    status: "running",
    close_type: "",
    pnl: 5,
    volume: 100,
    timestamp: 1_700_000_000,
    controller_id: "c1",
    cum_fees_quote: 0.1,
    net_pnl_pct: 0.01,
    entry_price: 200,
    current_price: 210,
    close_timestamp: 0,
    custom_info: {},
    config: {},
    ...patch,
  };
}

function sideOf(ex: ExecutorInfo): string {
  return computeMultiOverlays([ex])[0].side;
}

describe("overlay side", () => {
  it("reads the canonical BUY and SELL", () => {
    expect(sideOf(executor({ side: "BUY" }))).toBe("buy");
    expect(sideOf(executor({ side: "SELL" }))).toBe("sell");
  });

  it("takes the normalized executor.side over a raw custom_info.side", () => {
    // The exact case ARCH-121 assumed was latent: the raw dict disagrees with the
    // normalized field, and used to win.
    expect(sideOf(executor({ side: "BUY", custom_info: { side: "TradeType.BUY" } }))).toBe("buy");
    expect(sideOf(executor({ side: "BUY", custom_info: { side: "TradeType.SELL" } }))).toBe("buy");
    expect(sideOf(executor({ side: "SELL", custom_info: { side: "TradeType.BUY" } }))).toBe("sell");
  });

  it.each([
    ["PositionSide.LONG", "BUY", "buy"],
    ["LONG", "BUY", "buy"],
    ["SHORT", "SELL", "sell"],
    ["PositionSide.SHORT", "SELL", "sell"],
    [2, "SELL", "sell"],
    [1, "BUY", "buy"],
  ] as const)(
    "ignores the un-normalized custom_info.side %p and follows executor.side %s",
    (raw, normalized, expected) => {
      expect(sideOf(executor({ side: normalized, custom_info: { side: raw } }))).toBe(expected);
    },
  );

  it.each(["position", "order", "lp", "grid", "unknown"] as const)(
    "resolves a %s overlay's side from executor.side alone",
    (type) => {
      const ex = executor({
        type,
        side: "BUY",
        custom_info: { side: "TradeType.SELL" },
        config: { side: "SELL", start_price: 190, end_price: 210, amount: 1, price: 200 },
      });
      expect(sideOf(ex)).toBe("buy");
    },
  );

  it("draws a buy position with the up colour and an up arrow below the bar", () => {
    const [overlay] = computeMultiOverlays([
      executor({ side: "BUY", custom_info: { side: "TradeType.BUY" } }),
    ]);
    const entry = overlay.markers.find((m) => m.text === "BUY");

    expect(overlay.side).toBe("buy");
    expect(entry).toBeDefined();
    expect(entry?.shape).toBe("arrowUp");
    expect(entry?.position).toBe("belowBar");
    expect(entry?.color).toBe(UP);
  });

  it("draws a sell position with the down colour and a down arrow above the bar", () => {
    const [overlay] = computeMultiOverlays([executor({ side: "SELL" })]);
    const entry = overlay.markers.find((m) => m.text === "SELL");

    expect(overlay.side).toBe("sell");
    expect(entry?.shape).toBe("arrowDown");
    expect(entry?.position).toBe("aboveBar");
    expect(entry?.color).toBe(DOWN);
  });

  it("puts a buy's stop loss below entry and its take profit above", () => {
    // The consequence that is not cosmetic: the drawn side places both brackets.
    const [overlay] = computeMultiOverlays([
      executor({
        side: "BUY",
        custom_info: { side: "TradeType.BUY" },
        config: { stop_loss: 0.1, take_profit: 0.2 },
      }),
    ]);
    const priceOf = (label: string) => overlay.priceLines.find((l) => l.label.startsWith(label))?.price;

    expect(priceOf("SL")).toBeCloseTo(180, 6);
    expect(priceOf("TP")).toBeCloseTo(240, 6);
  });
});

describe("an un-normalized executor.side", () => {
  let warn: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.resetModules();
    warn = vi.spyOn(console, "warn").mockImplementation(() => {});
  });

  afterEach(() => {
    warn.mockRestore();
  });

  it("is reported instead of quietly becoming a sell", async () => {
    // Fresh module: the report is deduped per value for the process's lifetime.
    const { computeMultiOverlays: compute } = await import("./executor-overlays");

    const [overlay] = compute([executor({ side: "TradeType.BUY" })]);

    expect(overlay.side).toBe("sell");
    expect(warn).toHaveBeenCalledTimes(1);
    expect(String(warn.mock.calls[0][0])).toContain("TradeType.BUY");
  });

  it("is reported once however many executors carry it", async () => {
    const { computeMultiOverlays: compute } = await import("./executor-overlays");

    compute([
      executor({ id: "a", side: "NEUTRAL" }),
      executor({ id: "b", side: "NEUTRAL" }),
      executor({ id: "c", side: "NEUTRAL" }),
    ]);

    expect(warn).toHaveBeenCalledTimes(1);
  });

  it("says nothing about an LP position, which has no direction at all", async () => {
    const { computeMultiOverlays: compute } = await import("./executor-overlays");

    const [overlay] = compute([executor({ type: "lp", side: "", custom_info: {}, config: {} })]);

    expect(overlay.side).toBe("sell"); // the hover card labels this type "range"
    expect(warn).not.toHaveBeenCalled();
  });
});
