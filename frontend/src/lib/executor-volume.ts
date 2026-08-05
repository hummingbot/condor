// -- Executor volume bucketing --
//
// Charts that overlay executors on a price chart draw one series per executor,
// which stops scaling once a strategy produces hundreds of them. Above a
// threshold they instead render aggregated buy/sell volume: a fixed number of
// series regardless of how many executors the run produced.

/** Buy/sell executor volume aggregated into one candle-width bucket. */
export interface VolumeBucket {
  time: number;
  buyVol: number;
  sellVol: number;
  buyCount: number;
  sellCount: number;
}

/** Minimal executor shape needed to bucket volume (seconds or ms timestamp). */
export interface VolumeSource {
  timestamp: number;
  side?: string;
  volume: number;
}

/**
 * Above this many executors, per-executor overlays are replaced by the
 * aggregated volume histogram.
 */
export const MANY_EXECUTORS_THRESHOLD = 15;

/** Normalize a timestamp expressed in either seconds or milliseconds. */
function toSeconds(ts: number): number {
  return ts > 1e12 ? Math.floor(ts / 1000) : ts;
}

/** Sum executor volume into `intervalSec`-wide buckets, split by side. */
export function computeVolumeBuckets(
  executors: readonly VolumeSource[],
  intervalSec: number,
): VolumeBucket[] {
  const width = intervalSec > 0 ? intervalSec : 60;
  const buckets = new Map<number, VolumeBucket>();

  for (const e of executors) {
    const t = toSeconds(e.timestamp);
    if (t <= 0) continue;

    // Floor to candle bucket boundary
    const bucket = Math.floor(t / width) * width;
    const isBuy = e.side?.toUpperCase() === "BUY";
    const vol = e.volume > 0 ? e.volume : 0;

    let b = buckets.get(bucket);
    if (!b) {
      b = { time: bucket, buyVol: 0, sellVol: 0, buyCount: 0, sellCount: 0 };
      buckets.set(bucket, b);
    }
    if (isBuy) {
      b.buyVol += vol;
      b.buyCount++;
    } else {
      b.sellVol += vol;
      b.sellCount++;
    }
  }

  return Array.from(buckets.values()).sort((a, b) => a.time - b.time);
}
