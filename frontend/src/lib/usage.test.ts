/**
 * The dashboard's token fold must be the backend's (FEAT-120), or a live tab
 * drifts from a reloaded one; and the cost figure must never claim more than
 * is known.
 */

import { describe, expect, it } from "vitest";

import type { TokenUsage } from "@/lib/api";
import { addUsage, costDisplay, formatTokens } from "./usage";

const usage = (over: Partial<TokenUsage> = {}): TokenUsage => ({
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_write_tokens: 0,
  cost_usd: 0,
  unpriced_turns: 0,
  context_used: null,
  context_size: null,
  total_tokens: 0,
  ...over,
});

describe("addUsage", () => {
  it("sums the counters and keeps the latest context reading", () => {
    const total = addUsage(
      usage({ input_tokens: 1000, output_tokens: 50, cost_usd: 0.1, context_used: 1000, context_size: 200000 }),
      { input_tokens: 200, output_tokens: 10, cost_usd: 0.05, context_used: 5000 },
    );
    expect(total?.input_tokens).toBe(1200);
    expect(total?.output_tokens).toBe(60);
    expect(total?.total_tokens).toBe(1260);
    expect(total?.cost_usd).toBeCloseTo(0.15);
    expect(total?.context_used).toBe(5000);
    // A turn that did not report a window keeps the one already known.
    expect(total?.context_size).toBe(200000);
  });

  it("reads a pre-FEAT-120 meta's `{}` as zero and nothing as nothing", () => {
    expect(addUsage(undefined, {})?.total_tokens).toBe(0);
    expect(addUsage(undefined, undefined)).toBeUndefined();
    expect(addUsage(usage({ input_tokens: 5 }), null)?.input_tokens).toBe(5);
  });
});

describe("costDisplay", () => {
  it("hides the cost of a chat nothing in which could be priced", () => {
    expect(costDisplay(usage({ unpriced_turns: 3 }))).toBeNull();
  });

  it("marks a chat that moved from a priced model to an unpriced one as a lower bound", () => {
    expect(costDisplay(usage({ cost_usd: 0.41, unpriced_turns: 1 }))).toEqual({ lowerBound: true });
  });

  it("shows a fully priced chat plainly", () => {
    expect(costDisplay(usage({ cost_usd: 0.41 }))).toEqual({ lowerBound: false });
  });
});

describe("formatTokens", () => {
  it("reads at a glance", () => {
    expect(formatTokens(950)).toBe("950");
    expect(formatTokens(38200)).toBe("38.2k");
    expect(formatTokens(45000)).toBe("45k");
    expect(formatTokens(200000)).toBe("200k");
    expect(formatTokens(1_200_000)).toBe("1.2M");
  });
});
