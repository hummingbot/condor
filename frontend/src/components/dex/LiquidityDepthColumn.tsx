import { useEffect, useRef } from "react";

import type { ChartPriceAxis } from "@/components/trade/TradeChart";
import { getThemeColors } from "@/lib/theme-colors";

export interface DepthBin {
  price: number;
  base_amount: number;
  quote_amount: number;
  liquidity_usd: number | null;
}

interface Props {
  bins: DepthBin[];
  /** The pool's active price: bins above it hold base, below it hold quote. */
  activePrice: number | null;
  /** The candle chart's own price mapping. Null until the chart is ready. */
  axis: ChartPriceAxis | null;
  /** The range being configured in the panel, drawn across the bars. */
  rangeStart?: number | null;
  rangeEnd?: number | null;
  width?: number;
  /** Ignore the loudest bins when scaling bars. 90 keeps the shape readable. */
  percentile?: number;
}

/** The value at `p` percent of a sorted-ascending array, or 0 for an empty one. */
function percentileOf(sorted: number[], p: number): number {
  if (!sorted.length) return 0;
  const index = Math.min(sorted.length - 1, Math.floor(((p / 100) * (sorted.length - 1)) | 0));
  return sorted[Math.max(0, index)];
}

/**
 * A horizontal histogram of a CLMM pool's liquidity, drawn beside the candles on
 * the chart's own price axis.
 *
 * The alignment is the feature: a bar sits at the pixel row the chart puts its
 * price at, so an LP range drawn across the candles is read against the
 * liquidity already sitting there. That is why this reads `priceToCoordinate`
 * from the chart instead of deriving a scale from the bins — a second scale
 * would look informative and quietly disagree at every zoom level.
 *
 * `lightweight-charts` has no price-keyed horizontal histogram (its histograms
 * are keyed by time), so this is a hand-drawn canvas that owns its own redraw.
 */
export function LiquidityDepthColumn({
  bins,
  activePrice,
  axis,
  rangeStart,
  rangeEnd,
  width = 72,
  percentile = 90,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const container = containerRef.current;
    if (!canvas || !container || !axis) return;

    const draw = () => {
      const ctx = canvas.getContext("2d");
      if (!ctx) return;
      const dpr = window.devicePixelRatio || 1;
      const w = container.clientWidth;
      const h = axis.height() || container.clientHeight;
      if (!w || !h) return;
      if (canvas.width !== Math.round(w * dpr) || canvas.height !== Math.round(h * dpr)) {
        canvas.width = Math.round(w * dpr);
        canvas.height = Math.round(h * dpr);
        canvas.style.width = `${w}px`;
        canvas.style.height = `${h}px`;
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);

      const rows = bins ?? [];
      if (!rows.length) return;
      const active = activePrice;

      // Only what the chart currently shows: scaling against off-screen whale
      // bins would flatten the visible ones to a line as soon as you zoom in.
      const visible: { y: number; value: number; isBase: boolean }[] = [];
      const priced = rows.every((b) => b.liquidity_usd != null);
      for (const bin of rows) {
        const y = axis.priceToCoordinate(bin.price);
        if (y == null || y < 0 || y > h) continue;
        const isBase = active != null ? bin.price >= active : bin.base_amount >= bin.quote_amount;
        const value = priced
          ? (bin.liquidity_usd as number)
          : isBase
            ? bin.base_amount
            : bin.quote_amount;
        if (!(value > 0)) continue;
        visible.push({ y, value, isBase });
      }
      if (!visible.length) return;

      // With both sides priced in USD one scale is comparable across the whole
      // column; with raw amounts it is not — two tokens, two units — so each
      // side is normalized against its own bins.
      const trim = (values: number[]) =>
        percentileOf([...values].sort((a, b) => a - b), percentile) ||
        Math.max(...values, 0);
      let baseScale: number;
      let quoteScale: number;
      if (priced) {
        baseScale = quoteScale = trim(visible.map((v) => v.value));
      } else {
        const baseValues = visible.filter((v) => v.isBase).map((v) => v.value);
        const quoteValues = visible.filter((v) => !v.isBase).map((v) => v.value);
        baseScale = baseValues.length ? trim(baseValues) : 1;
        quoteScale = quoteValues.length ? trim(quoteValues) : 1;
      }

      // Bin rows crowd together as you zoom out; one pixel is the floor and the
      // typical gap is the ceiling, so bars never overlap into a solid block.
      const ys = visible.map((v) => v.y).sort((a, b) => a - b);
      let gap = h;
      for (let i = 1; i < ys.length; i++) gap = Math.min(gap, ys[i] - ys[i - 1] || gap);
      const barHeight = Math.max(1, Math.min(6, Math.floor(gap) || 1));

      const colors = getThemeColors();
      const maxBar = w - 2;
      for (const { y, value, isBase } of visible) {
        const scale = isBase ? baseScale : quoteScale;
        const len = Math.max(1, Math.min(maxBar, (value / (scale || 1)) * maxBar));
        ctx.fillStyle = isBase ? colors.up : colors.down;
        ctx.globalAlpha = 0.55;
        ctx.fillRect(w - len, y - barHeight / 2, len, barHeight);
      }
      ctx.globalAlpha = 1;

      // The range being configured, so "am I inside the liquidity" is answered
      // by looking. It shares the bars' axis, so it cannot drift from them.
      if (rangeStart != null && rangeEnd != null && rangeStart > 0 && rangeEnd > 0) {
        const y1 = axis.priceToCoordinate(Math.min(rangeStart, rangeEnd));
        const y2 = axis.priceToCoordinate(Math.max(rangeStart, rangeEnd));
        if (y1 != null && y2 != null) {
          const top = Math.min(y1, y2);
          const box = Math.abs(y1 - y2);
          ctx.fillStyle = colors.yellow;
          ctx.globalAlpha = 0.12;
          ctx.fillRect(0, top, w, box);
          ctx.strokeStyle = colors.yellow;
          ctx.globalAlpha = 0.7;
          ctx.beginPath();
          ctx.moveTo(0, Math.round(top) + 0.5);
          ctx.lineTo(w, Math.round(top) + 0.5);
          ctx.moveTo(0, Math.round(top + box) + 0.5);
          ctx.lineTo(w, Math.round(top + box) + 0.5);
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
      }

      // The active price, so the two sides are readable as sides.
      if (active != null) {
        const y = axis.priceToCoordinate(active);
        if (y != null && y >= 0 && y <= h) {
          ctx.strokeStyle = colors.text;
          ctx.globalAlpha = 0.6;
          ctx.beginPath();
          ctx.moveTo(0, Math.round(y) + 0.5);
          ctx.lineTo(w, Math.round(y) + 0.5);
          ctx.stroke();
          ctx.globalAlpha = 1;
        }
      }
    };

    draw();

    const unsubscribe = axis.onScaleChange(draw);
    const observer = new ResizeObserver(draw);
    observer.observe(container);

    // The scale-change subscription covers pan and zoom, but not a drag of the
    // price axis or an autoscale after new candles — and a bar at a
    // plausible-but-wrong price is worse than no bar. So the mapping itself is
    // probed (two calls, no drawing) and a redraw only follows a mapping that
    // actually moved.
    //
    // An idle chart's mapping cannot move on its own, so probing it every frame
    // forever buys nothing: the page a user parks on while an LP position runs
    // would wake the main thread 60 times a second to learn that nothing
    // changed. Instead the probe idles on a ~10 Hz timer — an autoscale after a
    // new candle is caught within a tick — and the moment it sees the mapping
    // move it escalates to per-frame, so a price-axis drag is still followed
    // frame by frame. It drops back once the axis has been still for SETTLE_MS.
    const IDLE_PROBE_MS = 100;
    const SETTLE_MS = 400;

    let frame = 0;
    let timer = 0;
    let lastProbe = "";
    let lastMoveAt = 0;
    const probePrice = bins[Math.floor(bins.length / 2)]?.price;

    /** Redraws if the chart's mapping moved since the last probe. */
    const probe = () => {
      if (probePrice == null) return false;
      const current = `${axis.priceToCoordinate(probePrice)}:${axis.height()}`;
      if (current === lastProbe) return false;
      lastProbe = current;
      draw();
      return true;
    };

    const idleTick = () => {
      if (probe()) {
        lastMoveAt = performance.now();
        frame = requestAnimationFrame(burstTick);
      } else {
        timer = window.setTimeout(idleTick, IDLE_PROBE_MS);
      }
    };

    const burstTick = () => {
      if (probe()) lastMoveAt = performance.now();
      if (performance.now() - lastMoveAt >= SETTLE_MS) {
        frame = 0;
        timer = window.setTimeout(idleTick, IDLE_PROBE_MS);
        return;
      }
      frame = requestAnimationFrame(burstTick);
    };

    timer = window.setTimeout(idleTick, IDLE_PROBE_MS);

    return () => {
      unsubscribe();
      observer.disconnect();
      window.clearTimeout(timer);
      cancelAnimationFrame(frame);
    };
  }, [axis, bins, activePrice, rangeStart, rangeEnd, percentile]);

  return (
    <div
      ref={containerRef}
      style={{ width }}
      className="relative shrink-0 overflow-hidden border-l border-[var(--color-border)]"
      title="Liquidity by price — base above the active price, quote below"
    >
      <canvas ref={canvasRef} className="absolute left-0 top-0" />
    </div>
  );
}
