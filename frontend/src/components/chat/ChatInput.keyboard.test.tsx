/**
 * The composer's keys do what they say (FU-16).
 *
 * Enter sent the message even when it was the Enter that confirms an IME
 * candidate, so a half-composed sentence went to an agent that can trade. And
 * the voice shortcut was advertised as ⌘M while the handler wanted ⌘⇧M with a
 * lower-case `m` — which Shift never produces on Windows or Linux.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ChatInput } from "./ChatInput";

vi.mock("@/lib/api", () => ({
  api: { getVoiceSettings: () => Promise.resolve({ auto_send: true }) },
}));

vi.mock("@/lib/auth-token", () => ({
  authFetch: () => Promise.resolve(new Response("{}")),
}));

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
const onSend = vi.fn();
const getUserMedia = vi.fn();

async function render() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ChatInput onSend={onSend} />
      </QueryClientProvider>,
    );
  });
}

const textarea = () => container.querySelector("textarea")!;

async function type(text: string) {
  await act(async () => {
    const setter = Object.getOwnPropertyDescriptor(
      HTMLTextAreaElement.prototype,
      "value",
    )!.set!;
    setter.call(textarea(), text);
    textarea().dispatchEvent(new Event("input", { bubbles: true }));
  });
}

async function keydown(target: EventTarget, init: KeyboardEventInit) {
  await act(async () => {
    target.dispatchEvent(
      new KeyboardEvent("keydown", { bubbles: true, cancelable: true, ...init }),
    );
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  // Refused, so the shortcut's effect is observable without a recorder.
  getUserMedia.mockRejectedValue(new Error("denied"));
  vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
  vi.spyOn(console, "error").mockImplementation(() => {});
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

describe("Enter in the composer (FU-16)", () => {
  it("does not send while an IME composition is being confirmed", async () => {
    await render();
    await type("こんにち");

    await keydown(textarea(), { key: "Enter", isComposing: true });
    expect(onSend).not.toHaveBeenCalled();
    expect(textarea().value).toBe("こんにち");

    // The same fact as older engines report it.
    await keydown(textarea(), { key: "Enter", keyCode: 229 });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("sends on a plain Enter", async () => {
    await render();
    await type("hello");

    await keydown(textarea(), { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("hello", undefined);
  });
});

describe("the voice shortcut (FU-16)", () => {
  it.each([
    ["⌘⇧M as macOS reports it", { metaKey: true, shiftKey: true, key: "m" }],
    ["Ctrl+Shift+M as Windows and Linux report it", { ctrlKey: true, shiftKey: true, key: "M" }],
  ])("starts recording on %s", async (_label, init) => {
    await render();

    await keydown(window, init);

    expect(getUserMedia).toHaveBeenCalledTimes(1);
  });

  it("leaves plain ⌘M — the system's minimise — alone", async () => {
    await render();

    await keydown(window, { metaKey: true, key: "m" });

    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("advertises the shortcut it actually listens for", async () => {
    await render();

    const mic = container.querySelector<HTMLButtonElement>(
      'button[title^="Record voice message"]',
    )!;
    expect(mic.title).toBe("Record voice message (⌘⇧M)");
  });
});
