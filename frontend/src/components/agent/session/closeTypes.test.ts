import { describe, expect, it } from "vitest";

import type { AgentPerformance } from "@/lib/api";

import { closeSummary, prettyCloseType } from "./closeTypes";

describe("prettyCloseType", () => {
  it("drops the enum prefix and reads the name as words", () => {
    expect(prettyCloseType("CloseType.EARLY_STOP")).toBe("early stop");
    expect(prettyCloseType("TAKE_PROFIT")).toBe("take profit");
  });
});

describe("closeSummary", () => {
  it("totals only the close types that happened, in their reported order", () => {
    const perf = {
      close_type_counts: { "CloseType.STOP_LOSS": 2, "CloseType.TAKE_PROFIT": 0, "CloseType.EARLY_STOP": 1 },
    } as unknown as AgentPerformance;
    expect(closeSummary(perf)).toEqual({ total: 3, label: "stop loss ×2, early stop ×1" });
  });

  it("is empty for a run with no performance", () => {
    expect(closeSummary(null)).toEqual({ total: 0, label: "" });
    expect(closeSummary(undefined)).toEqual({ total: 0, label: "" });
  });
});
