import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  formatAxisCurrency,
  formatAxisTime,
  formatCompactVolume,
  formatCurrencyVolume,
  formatDateTime,
  formatRelativeTime,
  formatRuntimeHours,
  formatTime,
  formatToolName,
  roundToPricePrecision,
  shortBotName,
} from "./formatters";

// The bug this suite pins (READ-251): the chart Y axes used to carry their own
// inline `K`-only ladder, so a $2.4M cumulative volume rendered "$2400.0K" on
// the axis while the header strip and the tooltip a few pixels away — both on
// `formatCurrencyVolume` — rendered "$2.4M". `formatAxisCurrency` is now the one
// ladder, and the "agrees with the neighbouring surfaces" block below is the
// part that must not regress when a later item touches axis or tooltip render.

describe("formatAxisCurrency", () => {
  describe("below $1K — the only tier where the axis has its own rule", () => {
    it("gives a pnl axis 2 decimals under $10, where the axis often spans cents", () => {
      expect(formatAxisCurrency(0, "$", "pnl")).toBe("$0.00");
      expect(formatAxisCurrency(2.5, "$", "pnl")).toBe("$2.50");
      expect(formatAxisCurrency(-2.5, "$", "pnl")).toBe("$-2.50");
      expect(formatAxisCurrency(9.99, "$", "pnl")).toBe("$9.99");
    });

    it("drops to whole dollars from $10 up on a pnl axis", () => {
      expect(formatAxisCurrency(10, "$", "pnl")).toBe("$10");
      expect(formatAxisCurrency(123.45, "$", "pnl")).toBe("$123");
      expect(formatAxisCurrency(999.4, "$", "pnl")).toBe("$999");
    });

    it("never shows decimals on a volume axis — ticks are whole dollars", () => {
      expect(formatAxisCurrency(0, "$", "volume")).toBe("$0");
      expect(formatAxisCurrency(2.5, "$", "volume")).toBe("$3");
      expect(formatAxisCurrency(250, "$", "volume")).toBe("$250");
      expect(formatAxisCurrency(999.4, "$", "volume")).toBe("$999");
    });

    it("defaults to the volume rule when no kind is given", () => {
      expect(formatAxisCurrency(2.5)).toBe("$3");
    });
  });

  describe("$1K to $1M — the K tier", () => {
    it("switches to K at exactly $1,000", () => {
      expect(formatAxisCurrency(1_000, "$", "pnl")).toBe("$1.0K");
      expect(formatAxisCurrency(1_000, "$", "volume")).toBe("$1.0K");
      expect(formatAxisCurrency(999.9, "$", "volume")).toBe("$1000");
    });

    it("renders one decimal across the tier, for both kinds alike", () => {
      expect(formatAxisCurrency(2_450, "$", "pnl")).toBe("$2.5K");
      expect(formatAxisCurrency(2_450, "$", "volume")).toBe("$2.5K");
      expect(formatAxisCurrency(999_900, "$", "volume")).toBe("$999.9K");
    });
  });

  describe("above $1M — the tier the axis used to be missing", () => {
    it("renders M instead of a four-digit K", () => {
      expect(formatAxisCurrency(1_000_000, "$", "volume")).toBe("$1.0M");
      expect(formatAxisCurrency(2_400_000, "$", "volume")).toBe("$2.4M");
      expect(formatAxisCurrency(14_500_000, "$", "volume")).toBe("$14.5M");
      expect(formatAxisCurrency(2_400_000, "$", "pnl")).toBe("$2.4M");
    });

    it("carries on to a B tier past $1B, as formatCompactVolume does", () => {
      expect(formatAxisCurrency(1_000_000_000, "$", "volume")).toBe("$1.00B");
      expect(formatAxisCurrency(2_400_000_000, "$", "volume")).toBe("$2.40B");
    });

    it("keeps every label short enough for the 52px axis gutter", () => {
      // At fontSize 10 the gutter fits ~8 glyphs; the old ladder printed
      // "$14500.0K" (9) and got clipped.
      for (const v of [0, -9.99, 999.4, -2_450, 999_900, 14_500_000, -2_400_000_000]) {
        expect(formatAxisCurrency(v, "$", "pnl").length).toBeLessThanOrEqual(8);
        expect(formatAxisCurrency(v, "$", "volume").length).toBeLessThanOrEqual(8);
      }
    });
  });

  describe("negatives and zero", () => {
    it("carries the minus inside the number, as the other helpers do", () => {
      expect(formatAxisCurrency(-2_400_000, "$", "volume")).toBe("$-2.4M");
      expect(formatAxisCurrency(-2_450, "$", "volume")).toBe("$-2.5K");
      expect(formatAxisCurrency(-250, "$", "volume")).toBe("$-250");
      expect(formatAxisCurrency(-1_000_000_000, "$", "volume")).toBe("$-1.00B");
    });

    it("renders zero without a sign in either kind", () => {
      expect(formatAxisCurrency(0, "$", "pnl")).toBe("$0.00");
      expect(formatAxisCurrency(-0, "$", "volume")).toBe("$0");
    });
  });

  it("honours a non-$ currency symbol at every tier", () => {
    expect(formatAxisCurrency(2_400_000, "€", "volume")).toBe("€2.4M");
    expect(formatAxisCurrency(2_450, "€", "volume")).toBe("€2.5K");
    expect(formatAxisCurrency(250, "€", "volume")).toBe("€250");
  });

  describe("agrees with the neighbouring surfaces", () => {
    // Header strip and both chart tooltips run every number through
    // `formatCurrencyVolume`; the axis must not disagree with them.
    const values = [1_000_000, 2_400_000, 14_500_000, -2_400_000, 999_900, 1_000];

    it("matches formatCurrencyVolume from $1K up", () => {
      for (const v of values) {
        expect(formatAxisCurrency(v, "$", "volume")).toBe(formatCurrencyVolume(v, "$"));
        expect(formatAxisCurrency(v, "$", "pnl")).toBe(formatCurrencyVolume(v, "$"));
      }
    });

    it("matches formatCompactVolume at every tier including B", () => {
      for (const v of [...values, 2_400_000_000, 250]) {
        expect(formatAxisCurrency(v, "$", "volume")).toBe(formatCompactVolume(v, "$"));
      }
    });
  });
});


// ── READ-250: the X axis was hardcoded to HH:MM ──
//
// The sampling interval is derived from the bot's real runtime (PERF-238), so
// one chart's span runs from a couple of hours to well over a year. At `HH:MM`
// a five-day window prints `08:00 / 12:00 / 16:00` once per day with nothing to
// separate the days. The rule `formatAxisTime` encodes is not "always prepend
// the date" but "the shortest label whose parts can still differ across this
// span" — which is why the date is *dropped* under a day and the time is
// dropped past a week. The uniqueness block at the end is the rule itself,
// stated as a property rather than as a list of examples.

const HOUR = 3_600_000;
const DAY = 86_400_000;

/** Local-time fixtures, so the expected labels hold in any TZ the suite runs in. */
const mar14 = new Date(2026, 2, 14, 8, 30).getTime();
const nov15 = new Date(2025, 10, 15, 8, 30).getTime();

/** The `n` ticks recharts would space evenly across a span ending at `end`. */
function ticks(end: number, spanMs: number, n = 6): number[] {
  return Array.from({ length: n }, (_, i) => end - spanMs + (i * spanMs) / (n - 1));
}

describe("formatAxisTime", () => {
  describe("under a day — the date is the same on every tick, so it is left off", () => {
    it("prints a bare HH:MM", () => {
      expect(formatAxisTime(mar14, 6 * HOUR)).toBe("08:30");
      expect(formatAxisTime(mar14, 23 * HOUR)).toBe("08:30");
    });

    it("falls back to HH:MM for a degenerate span (one point, or none)", () => {
      expect(formatAxisTime(mar14, 0)).toBe("08:30");
      expect(formatAxisTime(mar14, -1)).toBe("08:30");
      expect(formatAxisTime(mar14, NaN)).toBe("08:30");
    });
  });

  describe("a day to a week — both halves move, so both are shown", () => {
    it("switches to Mon D HH:MM at exactly 24h, the first span that can repeat a time", () => {
      expect(formatAxisTime(mar14, DAY - 1)).toBe("08:30");
      expect(formatAxisTime(mar14, DAY)).toBe("Mar 14 08:30");
    });

    it("keeps the day on a multi-day window — the bug this item fixes", () => {
      expect(formatAxisTime(mar14, 5 * DAY)).toBe("Mar 14 08:30");
      expect(formatAxisTime(new Date(2026, 2, 17, 16, 0).getTime(), 5 * DAY)).toBe("Mar 17 16:00");
    });
  });

  describe("a week to a year — ticks are over a day apart, so the time is decoration", () => {
    it("drops the time from 7 days up", () => {
      expect(formatAxisTime(mar14, 7 * DAY - 1)).toBe("Mar 14 08:30");
      expect(formatAxisTime(mar14, 7 * DAY)).toBe("Mar 14");
    });

    it("stays on Mon D across a multi-month window", () => {
      expect(formatAxisTime(mar14, 90 * DAY)).toBe("Mar 14");
      expect(formatAxisTime(nov15, 180 * DAY)).toBe("Nov 15");
    });

    it("needs no year to stay unambiguous across a new year", () => {
      // A month/day pair cannot repeat inside a span shorter than 365 days,
      // which is exactly where the next tier starts.
      const spanned = ticks(new Date(2026, 1, 13, 8, 30).getTime(), 90 * DAY);
      const labels = spanned.map((t) => formatAxisTime(t, 90 * DAY));
      expect(labels[0]).toBe("Nov 15");
      expect(labels[labels.length - 1]).toBe("Feb 13");
      expect(new Set(labels).size).toBe(labels.length);
    });
  });

  describe("a year and beyond — the day is decoration and the year is what repeats", () => {
    it("switches to Mon 'YY at 365 days", () => {
      expect(formatAxisTime(mar14, 365 * DAY - 1)).toBe("Mar 14");
      expect(formatAxisTime(mar14, 365 * DAY)).toBe("Mar '26");
      expect(formatAxisTime(nov15, 400 * DAY)).toBe("Nov '25");
    });

    it("separates the two halves of a span that crosses a year boundary", () => {
      expect(formatAxisTime(nov15, 400 * DAY)).toBe("Nov '25");
      expect(formatAxisTime(new Date(2026, 1, 13, 8, 30).getTime(), 400 * DAY)).toBe("Feb '26");
    });

    it("pads a single-digit year, so 2009 is not '9", () => {
      expect(formatAxisTime(new Date(2009, 5, 2).getTime(), 400 * DAY)).toBe("Jun '09");
    });
  });

  describe("agrees with the neighbouring surfaces", () => {
    it("reuses formatTime under a day and formatDateTime up to a week", () => {
      // The tooltip that opens over a tick runs on formatDateTime
      // (PnlChartTooltips); a tick must not spell the same instant differently.
      expect(formatAxisTime(mar14, 6 * HOUR)).toBe(formatTime(mar14));
      expect(formatAxisTime(mar14, 2 * DAY)).toBe(formatDateTime(mar14));
    });
  });

  describe("the rule itself: no two ticks of a span share a label", () => {
    const spans = [2 * HOUR, 12 * HOUR, DAY, 5 * DAY, 7 * DAY, 90 * DAY, 365 * DAY, 800 * DAY];

    it("labels every tick of every span distinctly", () => {
      for (const span of spans) {
        const labels = ticks(mar14, span).map((t) => formatAxisTime(t, span));
        expect(new Set(labels).size, `span ${span}ms → ${labels.join(" | ")}`).toBe(labels.length);
      }
    });

    it("keeps every label short enough to sit under the axis without wrapping", () => {
      // The widest tier is "Mon D HH:MM" (12 glyphs at fontSize 10); recharts
      // drops overlapping ticks, so this is about the label, not the count.
      for (const span of spans) {
        for (const t of ticks(mar14, span)) {
          expect(formatAxisTime(t, span).length).toBeLessThanOrEqual(12);
        }
      }
    });
  });
});

// A chart pick reads its price off the pixel under the pointer, so it arrives
// with every digit a float has. This is the round that stands between that and
// the config field (CORR-263).
describe("roundToPricePrecision", () => {
  it("rounds to the venue's precision when it is known", () => {
    expect(roundToPricePrecision(105234.87313432834, 2)).toBe(105234.87);
    expect(roundToPricePrecision(105234.87513432835, 2)).toBe(105234.88);
  });

  it("precision 0 leaves a whole number", () => {
    expect(roundToPricePrecision(105234.873, 0)).toBe(105235);
  });

  it("precision 8 keeps a sub-cent price intact", () => {
    expect(roundToPricePrecision(0.000012345678912, 8)).toBe(0.00001235);
  });

  it("falls back to six significant digits when no precision is supplied", () => {
    // The grid's Auto-fill round: a major keeps its cents, a memecoin keeps its
    // digits, where a fixed number of decimals would flatten one or the other.
    expect(roundToPricePrecision(105234.87313432834)).toBe(105235);
    expect(roundToPricePrecision(0.000012345678912)).toBe(0.0000123457);
    expect(roundToPricePrecision(123.4567891, undefined)).toBe(123.457);
    expect(roundToPricePrecision(123.4567891, null)).toBe(123.457);
  });

  it("passes a non-finite price through rather than inventing one", () => {
    expect(roundToPricePrecision(NaN, 2)).toBeNaN();
    expect(roundToPricePrecision(Infinity, 2)).toBe(Infinity);
  });
});

describe("shortBotName", () => {
  it("collapses the deploy stamp the deploy path appends twice", () => {
    expect(shortBotName("pmm-fleet-btcbrl-global-20260829-121810-20260829-121810")).toBe(
      "pmm-fleet-btcbrl-global-20260829-121810",
    );
  });

  it("keeps a single stamp, which is what tells two runs of one config apart", () => {
    expect(shortBotName("rebate-mill-usdtbrl-15-20260831-195909")).toBe(
      "rebate-mill-usdtbrl-15-20260831-195909",
    );
    // Two different stamps are two different deploys: neither is a repeat.
    expect(shortBotName("bot-20260829-121810-20260830-090000")).toBe(
      "bot-20260829-121810-20260830-090000",
    );
  });

  it("leaves a name with no stamp alone", () => {
    expect(shortBotName("main")).toBe("main");
  });
});

describe("formatRuntimeHours", () => {
  it("says hours once there is an hour to say", () => {
    expect(formatRuntimeHours(1)).toBe("1.0h");
    expect(formatRuntimeHours(2.35)).toBe("2.4h");
    expect(formatRuntimeHours(56.2)).toBe("56.2h");
  });

  // The tile used to read "0.1h" for a scope six minutes old — a number with no
  // digits left in it, beside per-hour paces extrapolated from exactly that.
  it("says minutes under the hour, where hours have run out of digits", () => {
    expect(formatRuntimeHours(0.1)).toBe("6m");
    expect(formatRuntimeHours(0.5)).toBe("30m");
    expect(formatRuntimeHours(59 / 60)).toBe("59m");
  });

  it("never rounds a real runtime down to nothing", () => {
    expect(formatRuntimeHours(0.008)).toBe("<1m");
  });

  it("has no runtime to report for zero or nonsense", () => {
    expect(formatRuntimeHours(0)).toBe("\u2014");
    expect(formatRuntimeHours(-3)).toBe("\u2014");
    expect(formatRuntimeHours(NaN)).toBe("\u2014");
  });
});

// ARCH-304 folded the admin tab's own `timeAgo` into this one, so "last seen"
// and the audit log now read in the same units as the routine library and
// the notification bell. Nothing pinned this ladder before the merge; the cases
// below are the ones the deleted copy answered differently, and they are the
// ones a caller has to get right.
describe("formatRelativeTime", () => {
  const NOW = 1_772_600_000_000; // fixed clock — the ladder is relative to it

  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  const secondsAgo = (n: number) => NOW / 1000 - n;

  it("walks seconds to minutes to hours to days, in the dashboard's short units", () => {
    expect(formatRelativeTime(secondsAgo(12))).toBe("12s ago");
    expect(formatRelativeTime(secondsAgo(59))).toBe("59s ago");
    expect(formatRelativeTime(secondsAgo(60))).toBe("1m ago");
    expect(formatRelativeTime(secondsAgo(3599))).toBe("59m ago");
    expect(formatRelativeTime(secondsAgo(3600))).toBe("1h ago");
    expect(formatRelativeTime(secondsAgo(86_399))).toBe("23h ago");
    expect(formatRelativeTime(secondsAgo(86_400))).toBe("1d ago");
  });

  // The deleted copy climbed on to "2 months ago" / "1 year ago". This one does
  // not: a year-old timestamp is 365 days old, and stays in days.
  it("stops at days rather than rounding a year into one word", () => {
    expect(formatRelativeTime(secondsAgo(86_400 * 60))).toBe("60d ago");
    expect(formatRelativeTime(secondsAgo(86_400 * 365))).toBe("365d ago");
  });

  it("takes milliseconds as readily as seconds", () => {
    expect(formatRelativeTime(NOW - 7_200_000)).toBe("2h ago");
    expect(formatRelativeTime(new Date(NOW - 7_200_000))).toBe("2h ago");
    expect(formatRelativeTime(new Date(NOW - 7_200_000).toISOString())).toBe("2h ago");
  });

  it("falls back for a timestamp that is absent or unparseable", () => {
    expect(formatRelativeTime(null, "never")).toBe("never");
    expect(formatRelativeTime(undefined, "never")).toBe("never");
    expect(formatRelativeTime("", "never")).toBe("never");
    expect(formatRelativeTime("not a date", "never")).toBe("never");
    expect(formatRelativeTime(null)).toBe("");
  });

  // The trap ARCH-304 had to route around: the admin API sends `last_seen = 0`
  // for a person who has never been seen, and 0 is a real epoch here, not a
  // missing value. Callers coerce it themselves (`person.last_seen || null`),
  // which is why this function may keep reading 0 as 1970.
  it("treats 0 as an epoch, not as 'missing' — the caller must coerce it", () => {
    const person = { last_seen: 0 };

    expect(formatRelativeTime(person.last_seen, "never")).toMatch(/^\d+d ago$/);
    expect(formatRelativeTime(person.last_seen || null, "never")).toBe("never");
  });

  // Known cosmetic difference from the deleted `timeAgo`, which said "just now"
  // for anything under a minute, a clock skewed into the future included.
  it("counts a future timestamp backwards rather than clamping it", () => {
    expect(formatRelativeTime(secondsAgo(-3))).toBe("-3s ago");
  });
});

// CORR-326: `formatToolName` is typed as total and must actually be total. The
// live `tool_call` frame is the reason — it used to build its `ToolCall` with an
// unchecked `data.title as string` cast, so a frame without the field reached
// `title.includes(...)` as `undefined` and threw inside the render of the bubble
// that was *streaming right then*, while the same turn re-read from history
// (which coerces) drew fine. These cases are the contract: no input throws, and
// every input names something a reader can act on.
describe("formatToolName", () => {
  describe("well-formed names — unchanged, byte for byte", () => {
    it("keeps the tool and drops the mcp__<server>__ prefix", () => {
      expect(formatToolName("mcp__condor__run_code")).toBe("run code");
      expect(formatToolName("mcp__condor__manage_routines")).toBe("manage routines");
    });

    it("leaves a bare name alone but for its underscores", () => {
      expect(formatToolName("ToolSearch")).toBe("ToolSearch");
      expect(formatToolName("read_file")).toBe("read file");
    });
  });

  describe("a title that is absent", () => {
    it("returns the fallback instead of throwing", () => {
      expect(() => formatToolName(undefined)).not.toThrow();
      expect(formatToolName(undefined)).toBe("tool");
      expect(formatToolName(null)).toBe("tool");
      expect(formatToolName()).toBe("tool");
    });

    // What the live path now hands it: `String(data.title ?? "")` on a frame
    // that carried no title at all.
    it("returns the fallback for the empty string the coercion produces", () => {
      expect(formatToolName("")).toBe("tool");
      expect(formatToolName("   ")).toBe("tool");
    });
  });

  describe("a title that is not a string", () => {
    // The wire is JSON; nothing stops it sending a number, a flag or an object
    // in a field the types call `string`.
    it("names nothing rather than rendering a coerced value as if it ran", () => {
      expect(formatToolName(42)).toBe("tool");
      expect(formatToolName(true)).toBe("tool");
      expect(formatToolName({ name: "run_code" })).toBe("tool");
      expect(formatToolName(["run_code"])).toBe("tool");
      expect(() => formatToolName({})).not.toThrow();
    });
  });

  describe("a title that is present but says nothing", () => {
    // A real transcript holds five calls whose stored title is the string
    // "undefined", quotes included — five identical rows telling the reader
    // nothing. CORR-327 stops new ones being written; this keeps the ones
    // already on disk readable.
    it("unwraps a double-stringified sentinel and falls back", () => {
      expect(formatToolName('"undefined"')).toBe("tool");
      expect(formatToolName("'undefined'")).toBe("tool");
      expect(formatToolName("undefined")).toBe("tool");
      expect(formatToolName("null")).toBe("tool");
      expect(formatToolName("None")).toBe("tool");
      expect(formatToolName("[object Object]")).toBe("tool");
    });

    it("keeps a real name that merely arrived quoted", () => {
      expect(formatToolName('"mcp__condor__run_code"')).toBe("run code");
    });
  });

  describe("a title that is garbage or unprintable", () => {
    it("turns control characters into spaces so the row stays one line", () => {
      expect(formatToolName("read\nfile")).toBe("read file");
      expect(formatToolName("run\u0000_code")).toBe("run code");
    });

    it("falls back when nothing printable is left", () => {
      expect(formatToolName("\u0000\u0007\u001b")).toBe("tool");
      expect(formatToolName("___")).toBe("tool");
      expect(formatToolName("__")).toBe("tool");
    });

    it("never yields an empty name from a stray separator", () => {
      expect(formatToolName("mcp__condor__")).toBe("condor");
      expect(formatToolName("mcp__condor__run_code__")).toBe("run code");
    });
  });
});
