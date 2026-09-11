/**
 * A screenshot goes into the composer the three ways a user would try it.
 *
 * ⌘V, a drag from the desktop and the paperclip all end in the same handler,
 * because a screenshot arrives on the clipboard as a `File` — so there is one
 * code path to get right rather than three. What these cases pin is the part
 * that is not obvious from that: the chips are previewed from the bytes the
 * browser already has (no round trip, and nothing on the server yet), Send is
 * enabled by an image *alone*, and `onSend` hands over the browser's own `File`s
 * for the caller to upload at send time (FEAT-098).
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

// jsdom has no object-URL implementation, and the composer mints one per chip.
const created: string[] = [];
const revoked: string[] = [];

function png(name = "shot.png", size = 1024): File {
  const file = new File([new Uint8Array(8)], name, { type: "image/png" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

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

function textarea(): HTMLTextAreaElement {
  return container.querySelector("textarea")!;
}

function sendButton(): HTMLButtonElement {
  return container.querySelector<HTMLButtonElement>(
    'button[aria-label="Send message"]',
  )!;
}

function chips(): HTMLImageElement[] {
  return Array.from(container.querySelectorAll("img"));
}

/** A paste carrying files, in the shape React reads off the event. */
async function paste(...files: File[]) {
  await act(async () => {
    const event = new Event("paste", { bubbles: true });
    Object.defineProperty(event, "clipboardData", { value: { files } });
    textarea().dispatchEvent(event);
  });
}

async function drop(...files: File[]) {
  const box = container.querySelector('[data-testid="composer-box"]')!;
  await act(async () => {
    const event = new Event("drop", { bubbles: true });
    Object.defineProperty(event, "dataTransfer", { value: { files } });
    box.dispatchEvent(event);
  });
}

async function pick(...files: File[]) {
  const input = container.querySelector<HTMLInputElement>(
    '[data-testid="attach-input"]',
  )!;
  await act(async () => {
    Object.defineProperty(input, "files", { value: files, configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
  });
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

beforeEach(() => {
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  onSend.mockReset();
  created.length = 0;
  revoked.length = 0;
  let n = 0;
  URL.createObjectURL = vi.fn(() => {
    const url = `blob:local/${++n}`;
    created.push(url);
    return url;
  });
  URL.revokeObjectURL = vi.fn((url: string) => revoked.push(url));
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("attaching an image to a message", () => {
  it("shows a thumbnail chip for a pasted screenshot", async () => {
    await render();
    await paste(png());

    expect(chips()).toHaveLength(1);
    expect(chips()[0].src).toContain("blob:local/1");
    // The preview costs no round trip: the bytes are already here.
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
  });

  it("does the same for a file dragged onto the composer", async () => {
    await render();
    await drop(png());
    expect(chips()).toHaveLength(1);
  });

  it("does the same for the paperclip", async () => {
    await render();
    await pick(png());
    expect(chips()).toHaveLength(1);
  });

  it("removes a chip and its object URL when ✕ is pressed", async () => {
    await render();
    await paste(png());

    const remove = container.querySelector<HTMLButtonElement>(
      'button[aria-label="Remove shot.png"]',
    )!;
    await act(async () => remove.click());

    expect(chips()).toHaveLength(0);
    expect(revoked).toEqual(["blob:local/1"]);
  });

  it("enables Send with an image and no words at all", async () => {
    await render();
    expect(sendButton().disabled).toBe(true);

    await paste(png());
    expect(sendButton().disabled).toBe(false);

    await act(async () => sendButton().click());
    expect(onSend).toHaveBeenCalledWith("", [expect.any(File)]);
  });

  it("hands over the browser's own File, not an id or a data URI", async () => {
    await render();
    await type("what is wrong here?");
    await paste(png("chart.png"));

    await act(async () => sendButton().click());

    const [text, files] = onSend.mock.calls[0];
    expect(text).toBe("what is wrong here?");
    expect(files).toHaveLength(1);
    expect(files[0].name).toBe("chart.png");
  });

  it("sends nothing with the message when nothing was attached", async () => {
    await render();
    await type("how is SOL-USDC doing?");
    await act(async () => sendButton().click());

    // `undefined`, not `[]`: the call sites that only ever send text must read
    // exactly as they did before this existed.
    expect(onSend).toHaveBeenCalledWith("how is SOL-USDC doing?", undefined);
  });

  it("clears the chips once the message is on its way", async () => {
    await render();
    await paste(png());
    await act(async () => sendButton().click());

    expect(chips()).toHaveLength(0);
    // Not revoked: the transcript's optimistic bubble renders these same URLs,
    // and revoking here would blank the picture the user just sent.
    expect(revoked).toEqual([]);
  });

  it("refuses a file over 5 MB and says so", async () => {
    await render();
    await paste(png("huge.png", 6 * 1024 * 1024));

    expect(chips()).toHaveLength(0);
    expect(container.textContent).toContain("over 5 MB");
  });

  it("ignores a file that is not an image", async () => {
    await render();
    await paste(new File(["x"], "notes.pdf", { type: "application/pdf" }));
    expect(chips()).toHaveLength(0);
  });

  it("caps a message at four images", async () => {
    await render();
    await paste(
      png("a.png"),
      png("b.png"),
      png("c.png"),
      png("d.png"),
      png("e.png"),
    );

    expect(chips()).toHaveLength(4);
    expect(container.textContent).toContain("At most 4 images");
  });
});
