import { describe, expect, it } from "vitest";

import {
  getSentiment,
  parseSignedNumber,
  shouldSkipSentimentColumn,
} from "./sentiment-color";

describe("parseSignedNumber", () => {
  it("parses currency with plus sign", () => {
    expect(parseSignedNumber("$+189.90")).toBe(189.9);
  });

  it("parses negative currency", () => {
    expect(parseSignedNumber("$-12.50")).toBe(-12.5);
  });

  it("parses percent suffix", () => {
    expect(parseSignedNumber("-3.5%")).toBe(-3.5);
  });
});

describe("getSentiment", () => {
  it("returns positive for $+189.90", () => {
    expect(getSentiment("$+189.90")).toBe("positive");
  });

  it("returns negative for -3.5", () => {
    expect(getSentiment("-3.5")).toBe("negative");
  });

  it("returns positive for plain numbers", () => {
    expect(getSentiment(42)).toBe("positive");
  });
});

describe("shouldSkipSentimentColumn", () => {
  it("skips session column", () => {
    expect(shouldSkipSentimentColumn("Session")).toBe(true);
  });

  it("allows pnl column", () => {
    expect(shouldSkipSentimentColumn("Sim PnL $")).toBe(false);
  });
});
