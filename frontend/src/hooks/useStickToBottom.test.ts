/**
 * The decisions behind the transcript's scroll (FU-07), as pure functions.
 *
 * jsdom has no layout, so the part worth pinning is the logic: when a reader
 * counts as following, and which changes are allowed to move them.
 */

import { describe, expect, it } from "vitest";

import {
  NEAR_BOTTOM_PX,
  isNearBottom,
  nextPinned,
  scrollIntent,
} from "./useStickToBottom";

/** A 400px viewport over `scrollHeight` of content, scrolled to `scrollTop`. */
const at = (scrollTop: number, scrollHeight = 2000) => ({
  scrollTop,
  scrollHeight,
  clientHeight: 400,
});

describe("isNearBottom", () => {
  it("is true at the end and within the threshold of it", () => {
    expect(isNearBottom(at(1600))).toBe(true);
    expect(isNearBottom(at(1600 - NEAR_BOTTOM_PX))).toBe(true);
    expect(isNearBottom(at(1600 - NEAR_BOTTOM_PX - 1))).toBe(false);
  });
});

describe("nextPinned", () => {
  it("unpins a reader who scrolls up and away from the end", () => {
    expect(nextPinned(true, 1600, at(1000))).toBe(false);
  });

  it("keeps following through a nudge that stays near the end", () => {
    expect(nextPinned(true, 1600, at(1570))).toBe(true);
  });

  it("does not read a tall flush as the reader leaving", () => {
    // The old test measured after layout: a table that added 600px put a
    // pinned reader 600px from the end, past the threshold, and following
    // stopped. The viewport did not move up, so nothing changed.
    expect(nextPinned(true, 1600, at(1600, 2600))).toBe(true);
  });

  it("stays pinned through a smooth scroll that has not arrived yet", () => {
    expect(nextPinned(true, 1000, at(1200))).toBe(true);
  });

  it("re-pins a reader who scrolls back to the end", () => {
    expect(nextPinned(false, 1000, at(1590))).toBe(true);
  });

  it("leaves a reader who is away, away — scrolling down partway is not back", () => {
    expect(nextPinned(false, 200, at(600))).toBe(false);
  });
});

describe("scrollIntent", () => {
  it("opens a different conversation at its end", () => {
    expect(scrollIntent({ slotChanged: true, ownMessage: true })).toBe("jump");
    expect(scrollIntent({ slotChanged: true, ownMessage: false })).toBe("jump");
  });

  it("always shows the user's own message", () => {
    expect(scrollIntent({ slotChanged: false, ownMessage: true })).toBe("own");
  });

  it("only follows for anything else — a note, a chunk, a tool call", () => {
    expect(scrollIntent({ slotChanged: false, ownMessage: false })).toBe("follow");
  });
});
