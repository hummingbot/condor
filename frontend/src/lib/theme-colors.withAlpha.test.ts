import { describe, expect, it } from "vitest";

import { withAlpha } from "./theme-colors";

describe("withAlpha", () => {
  it("dims the theme's hsl tokens, in either syntax", () => {
    expect(withAlpha("hsl(0 72% 51%)", 0.2)).toBe("hsla(0, 72%, 51%, 0.2)");
    expect(withAlpha("hsl(0, 72%, 51%)", 0.2)).toBe("hsla(0, 72%, 51%, 0.2)");
    expect(withAlpha("hsl(0 72% 51% / 0.9)", 0.2)).toBe("hsla(0, 72%, 51%, 0.2)");
  });

  it("dims hex and rgb as before", () => {
    expect(withAlpha("#ef4444", 0.2)).toBe("#ef444433");
    expect(withAlpha("#f00", 0.2)).toBe("#ff000033");
    expect(withAlpha("rgba(1, 2, 3, 0.8)", 0.2)).toBe("rgba(1, 2, 3, 0.2)");
    expect(withAlpha("rgb(1 2 3)", 0.2)).toBe("rgba(1, 2, 3, 0.2)");
  });

  it("leaves what it cannot read alone", () => {
    expect(withAlpha("red", 0.2)).toBe("red");
  });
});
