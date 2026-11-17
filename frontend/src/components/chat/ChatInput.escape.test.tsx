/**
 * Escape stops the answer only when it was meant for the chat (FU-05).
 *
 * While an answer streamed, the composer listened on `window` and aborted on
 * any Escape at all — one pressed to close a menu, the market list or a
 * confirm dialog also stopped the agent, and with the bubble open that held on
 * every page. The textarea had a second branch of its own, so Escape typed
 * into the box aborted twice.
 *
 * What is pinned: one abort per press from inside the chat; none for a press
 * elsewhere on the page; none while an overlay is open, which takes the key
 * for itself.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AnchoredMenu } from "@/components/ui/AnchoredMenu";
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
const onAbort = vi.fn();
const onMenuClose = vi.fn();

async function render({
  streaming = true,
  menu = false,
}: { streaming?: boolean; menu?: boolean } = {}) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={client}>
        <button id="elsewhere">A control on the page behind the chat</button>
        <AnchoredMenu
          open={menu}
          onClose={onMenuClose}
          anchor={document.getElementById("elsewhere")}
        >
          <button id="option">Option</button>
        </AnchoredMenu>
        <ChatInput onSend={() => {}} isStreaming={streaming} onAbort={onAbort} />
      </QueryClientProvider>,
    );
  });
}

async function escape(target: Element) {
  await act(async () => {
    target.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }),
    );
  });
}

const textarea = () => container.querySelector("textarea")!;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("Escape while an answer streams (FU-05)", () => {
  it("stops the answer once when pressed in the composer", async () => {
    await render();

    await escape(textarea());

    expect(onAbort).toHaveBeenCalledTimes(1);
  });

  it("does nothing when pressed elsewhere on the page", async () => {
    await render();

    await escape(document.getElementById("elsewhere")!);
    await escape(document.body);

    expect(onAbort).not.toHaveBeenCalled();
  });

  it("closes an open menu instead of stopping the answer", async () => {
    await render();
    await render({ menu: true });

    // Even from inside the composer: the menu is the topmost thing open.
    await escape(textarea());

    expect(onMenuClose).toHaveBeenCalledTimes(1);
    expect(onAbort).not.toHaveBeenCalled();
  });

  it("does nothing when no answer is streaming", async () => {
    await render({ streaming: false });

    await escape(textarea());

    expect(onAbort).not.toHaveBeenCalled();
  });
});
