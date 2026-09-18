/**
 * The avatar registry: the choice is remembered, and nothing is drawn in the
 * wrong style while the right one is loading.
 *
 * The second part is the one worth a test. Every style is its own chunk (700 KB
 * of JSON between them, on a bundle that already warns about its size), so
 * there is a moment where an address has a face to draw and no style to draw it
 * in. Filling that moment with *some* style would defeat the only thing an
 * avatar is for: the same address looking the same everywhere. Null means the
 * component leaves an empty circle and fills it when the style lands.
 *
 * @vitest-environment jsdom
 */

import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { AVATAR_STYLE_KEY } from "@/lib/sessionState";

import {
  AVATAR_STYLES,
  DEFAULT_AVATAR_STYLE,
  avatarSvg,
  getAvatarStyleId,
  loadAvatarStyle,
  setAvatarStyleId,
} from "./avatarStyle";

const SEED = "2LjWzppfJyTfTJT3gPAvmk6wukYUwi8cv1CuXjBHhfGj";

beforeEach(() => localStorage.clear());
afterEach(() => {
  localStorage.clear();
  setAvatarStyleId(DEFAULT_AVATAR_STYLE);
});

describe("avatar styles", () => {
  it("draws the same address the same way twice", async () => {
    await loadAvatarStyle("identicon");

    expect(avatarSvg(SEED, "identicon")).toBe(avatarSvg(SEED, "identicon"));
  });

  it("draws two addresses differently", async () => {
    await loadAvatarStyle("identicon");

    expect(avatarSvg(SEED, "identicon")).not.toBe(
      avatarSvg("82SggYRE2Vo4jN4a2pk3aQ4SET4ctafZJGbowmCqyHx5", "identicon"),
    );
  });

  it("answers null rather than another style while one is still loading", () => {
    // `stripes` has not been asked for in this test file, so its chunk is not
    // in memory — which is exactly the state a fresh page load is in.
    expect(avatarSvg(SEED, "stripes")).toBeNull();
  });

  it("remembers the chosen style", () => {
    setAvatarStyleId("weave");

    expect(getAvatarStyleId()).toBe("weave");
    expect(localStorage.getItem(AVATAR_STYLE_KEY)).toBe("weave");
  });

  it("refuses a style it does not ship rather than storing it", () => {
    expect(() => setAvatarStyleId("not-a-style")).toThrow(/no avatar style/);
    expect(getAvatarStyleId()).toBe(DEFAULT_AVATAR_STYLE);
  });

  it("ships the default it falls back to", () => {
    expect(AVATAR_STYLES.map((style) => style.id)).toContain(DEFAULT_AVATAR_STYLE);
  });

  it("has no duplicate ids, which would make the picker ambiguous", () => {
    const ids = AVATAR_STYLES.map((style) => style.id);

    expect(new Set(ids).size).toBe(ids.length);
  });
});
