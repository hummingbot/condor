/**
 * The tab's status marker tells the truth about the slot (CORR-295).
 *
 * The dot is the strip's only status affordance, and it used to be green for
 * every tab that was not mid-answer — including a slot the roster had already
 * reported as reaped (`alive: false`: an idle detach, an eviction, a dead
 * subprocess). A user reading that green dot waits for a reply from a process
 * that is not there.
 *
 * These pin the three states the backend can put a slot in: reaped, live, and
 * unknown — the last being a roster from a server older than `alive`, which
 * has to keep reading exactly as it did before.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatSlot } from "@/hooks/useChatSocket";

import { SessionTabs } from "./SessionTabs";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function slot(info: Partial<ChatSlot["info"]>): ChatSlot {
  return {
    info: { slot_id: "s1", agent_key: "claude_acp", ...info },
    messages: [],
  };
}

async function render(slots: ChatSlot[], streaming = false) {
  await act(async () => {
    root.render(
      <SessionTabs
        slots={slots}
        activeSlotId={slots[0]?.info.slot_id ?? null}
        isSlotStreaming={() => streaming}
        permissionRequests={{}}
        onSelect={() => {}}
        onClose={() => {}}
      />,
    );
  });
}

/** The status marker of the first tab — the one fixed-width slot at its head. */
const marker = () => container.querySelector("span[aria-label]")!;

/** The green dot itself, found by the token it is painted with. */
const liveDots = () =>
  container.querySelectorAll('[class*="bg-[var(--color-green)]"]');

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("SessionTabs status marker", () => {
  it("shows no live dot for a slot the backend reports as not alive", async () => {
    await render([slot({ alive: false, label: "Arbitrage" })]);

    // The lie this fixes: green, on a slot with nothing running behind it.
    expect(liveDots()).toHaveLength(0);
    expect(marker().getAttribute("aria-label")).toBe(
      "Detached — reattaches on your next message",
    );
    expect(marker().getAttribute("title")).toBe(
      "Detached — reattaches on your next message",
    );
    // Still a tab, and still named — the conversation is intact.
    expect(container.querySelector("button")!.textContent).toContain(
      "Arbitrage",
    );
  });

  it("keeps the green dot for a slot the backend reports as alive", async () => {
    await render([slot({ alive: true })]);

    expect(liveDots()).toHaveLength(1);
    expect(marker().getAttribute("aria-label")).toBe("Session live");
  });

  it("keeps the green dot when the backend sends no `alive` at all", async () => {
    // A server older than the roster's `alive` key listed only live slots, so
    // an absent key is not a detached slot and must not be shown as one.
    await render([slot({})]);

    expect(liveDots()).toHaveLength(1);
    expect(marker().getAttribute("aria-label")).toBe("Session live");
  });

  it("answers first: a detached slot that is streaming shows the spinner", async () => {
    // The reattach is in flight — the marker reports the answer, not the
    // roster entry it is about to replace.
    await render([slot({ alive: false })], true);

    expect(marker().getAttribute("aria-label")).toBe("Answering");
    expect(liveDots()).toHaveLength(0);
  });
});
