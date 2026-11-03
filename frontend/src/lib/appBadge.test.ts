/**
 * The dock badge is called from a timer, so it must never be able to fail loudly.
 *
 * `setAppBadge` runs on every notification poll, in browsers that do not have
 * the Badging API at all (Firefox) and in contexts where it exists but rejects.
 * Neither is an error the user could have caused or could act on, so the bar is
 * not "handles it" but "is incapable of surfacing it": no throw, and no
 * unhandled rejection escaping into a component that is only rendering a bell.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import { setAppBadge } from "@/lib/appBadge";

const original = Object.getOwnPropertyDescriptor(globalThis, "navigator");

function stubNavigator(value: unknown): void {
  Object.defineProperty(globalThis, "navigator", {
    value,
    configurable: true,
    writable: true,
  });
}

afterEach(() => {
  if (original) Object.defineProperty(globalThis, "navigator", original);
  else Reflect.deleteProperty(globalThis, "navigator");
});

describe("setAppBadge", () => {
  it("sets the count when there is something unread", () => {
    const setSpy = vi.fn(() => Promise.resolve());
    stubNavigator({ setAppBadge: setSpy, clearAppBadge: vi.fn() });

    setAppBadge(3);

    expect(setSpy).toHaveBeenCalledWith(3);
  });

  it("clears the badge at zero rather than showing a 0", () => {
    const clearSpy = vi.fn(() => Promise.resolve());
    const setSpy = vi.fn(() => Promise.resolve());
    stubNavigator({ setAppBadge: setSpy, clearAppBadge: clearSpy });

    setAppBadge(0);

    expect(clearSpy).toHaveBeenCalled();
    expect(setSpy).not.toHaveBeenCalled();
  });

  it("does nothing when the browser has no Badging API", () => {
    stubNavigator({ userAgent: "firefox-ish" });

    expect(() => setAppBadge(5)).not.toThrow();
    expect(() => setAppBadge(0)).not.toThrow();
  });

  it("does not throw when there is no navigator at all", () => {
    Reflect.deleteProperty(globalThis, "navigator");

    expect(() => setAppBadge(5)).not.toThrow();
    expect(() => setAppBadge(0)).not.toThrow();
  });

  it("handles the rejection instead of letting it escape", async () => {
    // A rejected promise with nothing attached to it is an unhandled
    // rejection, so what matters is that the helper attaches the handler
    // itself — asserted on the promise it was handed, not on a warning that
    // would only appear after the run.
    const setRejects = Promise.reject(new Error("no badge here"));
    const clearRejects = Promise.reject(new Error("no badge here"));
    const setCatch = vi.spyOn(setRejects, "catch");
    const clearCatch = vi.spyOn(clearRejects, "catch");
    stubNavigator({
      setAppBadge: () => setRejects,
      clearAppBadge: () => clearRejects,
    });

    setAppBadge(2);
    setAppBadge(0);

    expect(setCatch).toHaveBeenCalled();
    expect(clearCatch).toHaveBeenCalled();
    await expect(setRejects).rejects.toThrow("no badge here");
    await expect(clearRejects).rejects.toThrow("no badge here");
  });

  it("survives an implementation that throws synchronously", () => {
    stubNavigator({
      setAppBadge: () => {
        throw new Error("embedded context");
      },
      clearAppBadge: () => {
        throw new Error("embedded context");
      },
    });

    expect(() => setAppBadge(1)).not.toThrow();
    expect(() => setAppBadge(0)).not.toThrow();
  });
});
