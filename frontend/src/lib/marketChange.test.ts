import { describe, expect, it } from "vitest";

import { changeColumnLabel, changeLabel } from "./marketChange";

const H = 3600;

describe("change window labels", () => {
  it("says the measured window, in days once it passes 48h", () => {
    expect(changeLabel(140 * H)).toBe("6d change");
    expect(changeColumnLabel(140 * H)).toBe("6d Δ");
  });

  it("keeps 24h, hours and minutes as before", () => {
    expect(changeLabel(24 * H)).toBe("24h change");
    expect(changeLabel(5 * H)).toBe("5h change");
    expect(changeColumnLabel(20 * 60)).toBe("20m Δ");
  });

  it("claims no window when there is none", () => {
    expect(changeLabel(null)).toBe("Change");
    expect(changeColumnLabel(null)).toBe("Δ");
  });
});
