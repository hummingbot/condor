/**
 * A half-written message outlives the composer that was holding it.
 *
 * Every chat surface sits inside a route, so opening the portfolio and coming
 * back unmounts the box — and the paragraph someone was still writing went with
 * it. What these cases pin is the three moments that made that fix worth
 * having: the words come back on the next mount, they come back *per
 * conversation* (the session strip swaps the key under a composer that never
 * unmounts), and a message that was actually sent does not come back at all.
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

let container: HTMLDivElement;
let root: Root;
const onSend = vi.fn();

async function render(draftKey?: string) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <ChatInput onSend={onSend} draftKey={draftKey} />
      </QueryClientProvider>,
    );
  });
}

function textarea(): HTMLTextAreaElement {
  return container.querySelector("textarea")!;
}

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

/** Leave the page and come back: the same surface, a fresh component. */
async function remount(draftKey?: string) {
  await act(async () => root.unmount());
  root = createRoot(container);
  await render(draftKey);
}

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  onSend.mockReset();
  localStorage.clear();
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
  localStorage.clear();
});

describe("composer drafts", () => {
  it("gives back what was typed after the surface unmounts", async () => {
    await render("conv-1");
    await type("half a thought about the SOL pool");

    await remount("conv-1");

    expect(textarea().value).toBe("half a thought about the SOL pool");
  });

  it("keeps one conversation's draft out of another's box", async () => {
    await render("conv-1");
    await type("for the first chat");

    // The session strip switches conversations without unmounting the box.
    await render("conv-2");
    expect(textarea().value).toBe("");

    await type("for the second chat");
    await render("conv-1");
    expect(textarea().value).toBe("for the first chat");
    await render("conv-2");
    expect(textarea().value).toBe("for the second chat");
  });

  it("does not bring back a message that was sent", async () => {
    await render("conv-1");
    await type("this one goes out");
    await act(async () => {
      textarea().dispatchEvent(
        new KeyboardEvent("keydown", { key: "Enter", bubbles: true }),
      );
    });
    expect(onSend).toHaveBeenCalledWith("this one goes out", undefined);

    await remount("conv-1");
    expect(textarea().value).toBe("");
  });

  it("persists nothing when the surface has no key to file it under", async () => {
    await render(undefined);
    await type("nowhere to keep this");

    expect(Object.keys(localStorage)).toHaveLength(0);

    await remount(undefined);
    expect(textarea().value).toBe("");
  });
});
