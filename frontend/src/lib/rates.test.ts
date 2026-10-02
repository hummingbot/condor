/**
 * The display-currency rule, pinned (ARCH-228).
 *
 * These are money strings shown to a user and quoted to an agent, so the two
 * ways of getting them wrong are worth naming: a value converted twice, and a
 * value relabelled with a currency it is not in. Every case below is one or
 * the other.
 *
 * `lib/rates` reaches `CURRENCY_SYMBOLS` through `useDisplayCurrency`, which
 * reads `localStorage` at module load, so this file needs a DOM.
 *
 * @vitest-environment jsdom
 */

import { describe, expect, it } from "vitest";

import { formatCurrency, formatCurrencyPnl } from "./formatters";
import { formatWithRate, rateFor, resolveSymbol, STABLECOINS } from "./rates";

describe("rateFor", () => {
  it("is 1 for the display currency itself, with or without a table", () => {
    expect(rateFor(undefined, "EUR", "EUR")).toBe(1);
    expect(rateFor({ EUR: 42 }, "EUR", "eur")).toBe(1);
  });

  it("is 1 between USD-pegged stablecoins, without waiting for a rate", () => {
    expect(rateFor(undefined, "USDT", "USDC")).toBe(1);
    expect(rateFor(undefined, "USDT", "DAI")).toBe(1);
    for (const coin of STABLECOINS) expect(rateFor(undefined, "USDT", coin)).toBe(1);
  });

  it("does not peg a stablecoin to a non-USD display currency", () => {
    expect(rateFor(undefined, "EUR", "USDC")).toBeNull();
    expect(rateFor({ USDC: 1.1 }, "EUR", "USDC")).toBe(1.1);
  });

  it("treats no entry, a null entry and a non-positive rate as no path", () => {
    expect(rateFor({}, "EUR", "BRL")).toBeNull();
    expect(rateFor({ BRL: null }, "EUR", "BRL")).toBeNull();
    expect(rateFor({ BRL: 0 }, "EUR", "BRL")).toBeNull();
    expect(rateFor({ BRL: -1 }, "EUR", "BRL")).toBeNull();
  });

  it("reads a missing quote as USD, which is what the API totals are in", () => {
    expect(rateFor({ USDT: 1.1 }, "EUR", undefined)).toBe(1.1);
    expect(rateFor({ USDT: 1.1 }, "EUR", "")).toBe(1.1);
  });
});

describe("resolveSymbol", () => {
  it("keeps $ until the USD rate lands, then switches with the number", () => {
    expect(resolveSymbol(undefined, "EUR")).toBe("$");
    expect(resolveSymbol({ USDT: null }, "EUR")).toBe("$");
    expect(resolveSymbol({ USDT: 1.1 }, "EUR")).toBe("€");
  });

  it("is the display symbol straight away when no conversion is needed", () => {
    expect(resolveSymbol(undefined, "USDT")).toBe("$");
  });
});

describe("formatWithRate", () => {
  it("divides by the rate exactly once and labels it with the display currency", () => {
    const fmt = formatWithRate(formatCurrency, { USDT: 1.1 }, "EUR");
    expect(fmt(110, "USDT")).toBe(formatCurrency(100, "€"));
    expect(fmt(110, "USDT")).not.toContain("⚠");
  });

  it("keeps the quote's own symbol when the value could not be converted", () => {
    // The number is still in BRL: calling it euros would be a wrong number,
    // not a formatting detail. This is the half of the rule the page-context
    // block used to get wrong.
    const fmt = formatWithRate(formatCurrencyPnl, { BRL: null }, "EUR");
    const out = fmt(-412.2971, "BRL");
    expect(out).toBe(`${formatCurrencyPnl(-412.2971, "R$")} ⚠`);
    expect(out).toContain("R$");
    expect(out).not.toContain("€");
  });

  it("keeps a stablecoin's $ even before a rate lands", () => {
    // USDC has no display symbol of its own, but it *is* a dollar: suffixing
    // the common X-USDC row "USDC" would only mislabel it the other way.
    const fmt = formatWithRate(formatCurrency, {}, "EUR");
    expect(fmt(500, "USDC")).toBe(`${formatCurrency(500, "$")} ⚠`);
  });

  it("labels a quote it has no symbol for with the quote's own ticker", () => {
    // 0.163 SOL read `$0.16 ⚠` — a dollar it is not. The ticker goes *after*
    // the number, where the /dex surfaces already put it.
    const fmt = formatWithRate(formatCurrency, {}, "EUR");
    expect(fmt(500, "PLN")).toBe("500.00 PLN ⚠");
    expect(fmt(0.163, "SOL")).toBe("0.16 SOL ⚠");
  });

  it("leaves the unconverted value untouched — only its label changes", () => {
    const raw = formatWithRate((val) => String(val), { BRL: null }, "EUR");
    expect(raw(-412.2971, "BRL")).toBe("-412.2971 ⚠");
  });
});
