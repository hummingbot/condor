/**
 * @vitest-environment jsdom
 *
 * The three answers the Settings pane branches on, and the encoding it depends on.
 *
 * `pushSupport()` is the load-bearing one. Condor is commonly reached over a
 * tailnet at `http://host:8088`, where Web Push is not restricted but *absent* —
 * and the two feature checks are false there for a reason that has nothing to do
 * with the browser. Reporting "your browser cannot" in that case sends the user
 * to look in entirely the wrong place, and there is no error anywhere to correct
 * them, so the order of those checks is the assertion.
 */

import { afterEach, describe, expect, it } from "vitest";

import { decodeVapidKey, deviceLabel, isPushSupported, pushSupport } from "@/lib/push";

const originalSecure = Object.getOwnPropertyDescriptor(window, "isSecureContext");

function setContext(secure: boolean, apis: { sw?: boolean; push?: boolean } = {}) {
  Object.defineProperty(window, "isSecureContext", {
    value: secure,
    configurable: true,
    writable: true,
  });
  if (apis.sw === false) Reflect.deleteProperty(navigator, "serviceWorker");
  else if (apis.sw) {
    Object.defineProperty(navigator, "serviceWorker", {
      value: {},
      configurable: true,
      writable: true,
    });
  }
  if (apis.push === false) Reflect.deleteProperty(window, "PushManager");
  else if (apis.push) {
    Object.defineProperty(window, "PushManager", {
      value: class {},
      configurable: true,
      writable: true,
    });
  }
}

afterEach(() => {
  if (originalSecure) {
    Object.defineProperty(window, "isSecureContext", originalSecure);
  }
  Reflect.deleteProperty(window, "PushManager");
  Reflect.deleteProperty(navigator, "serviceWorker");
});

describe("pushSupport", () => {
  it("names the insecure context first, even though the APIs are also missing", () => {
    // The tailnet-by-IP deployment: `http://condor-host:8088`. The fix is
    // `tailscale serve`, not a different browser, and only this branch says so.
    setContext(false, { sw: false, push: false });

    expect(pushSupport()).toBe("insecure-context");
    expect(isPushSupported()).toBe(false);
  });

  it("blames the browser only when the context is already secure", () => {
    setContext(true, { sw: false, push: false });

    expect(pushSupport()).toBe("unsupported");
  });

  it("is supported when the context is secure and both APIs are present", () => {
    setContext(true, { sw: true, push: true });

    expect(pushSupport()).toBe("supported");
    expect(isPushSupported()).toBe(true);
  });

  it("is unsupported with a service worker but no PushManager", () => {
    setContext(true, { sw: true, push: false });

    expect(pushSupport()).toBe("unsupported");
  });
});

describe("decodeVapidKey", () => {
  it("round-trips the unpadded base64url the server sends", () => {
    // What `condor/push.py` writes: an uncompressed P-256 point, so 65 bytes
    // beginning 0x04. A padding or alphabet slip here fails inside
    // `pushManager.subscribe` with a message that names neither.
    const bytes = new Uint8Array(65);
    bytes[0] = 0x04;
    for (let i = 1; i < 65; i += 1) bytes[i] = (i * 7) % 256;
    const base64url = btoa(String.fromCharCode(...bytes))
      .replace(/\+/g, "-")
      .replace(/\//g, "_")
      .replace(/=+$/, "");

    expect(Array.from(decodeVapidKey(base64url))).toEqual(Array.from(bytes));
  });
});

describe("deviceLabel", () => {
  it("names the browser and the platform so devices can be told apart", () => {
    expect(
      deviceLabel(
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
      ),
    ).toBe("Chrome on macOS");
  });

  it("prefers the more specific engine when both strings are present", () => {
    // Every Chromium browser claims Safari and Chrome too; Edge must not read
    // as Chrome, or two rows in the list look like the same machine.
    expect(
      deviceLabel(
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36 Edg/126.0",
      ),
    ).toBe("Edge on Windows");
  });

  it("degrades to something printable rather than empty", () => {
    expect(deviceLabel("some unknown agent")).toBe("Browser");
  });
});
