import { describe, expect, it } from "vitest";

import { humanizeFieldName } from "./routineFields";

describe("humanizeFieldName", () => {
  it("says a snake_case name as words", () => {
    expect(humanizeFieldName("db_filter")).toBe("DB filter");
    expect(humanizeFieldName("controller_id")).toBe("Controller ID");
    expect(humanizeFieldName("mode")).toBe("Mode");
    expect(humanizeFieldName("trading_pair")).toBe("Trading pair");
  });
});
