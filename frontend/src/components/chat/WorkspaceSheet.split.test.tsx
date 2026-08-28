/**
 * A routine opened from the chat dock lands *beside* the conversation.
 *
 * It used to land on top of it: `defaultZen` took the whole viewport, so the
 * agent that produced the report was gone until you closed it — the wrong way
 * round for a report whose whole point is asking the agent about it. These
 * cases pin the properties that outlive a refactor of the sheet's chrome: in a
 * workspace wide enough to split, the body renders inside the pane and no
 * overlay exists; full screen is still reachable and reversible; and anywhere
 * without a pane — an agent's own page, a narrow window — the sheet is the
 * overlay it has always been.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import { WorkspacePaneOutlet, WorkspacePaneProvider } from "./WorkspacePane";
import { WorkspaceSheet } from "./WorkspaceSheet";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;
let closes: number;

/** jsdom answers every query `false`; the split turns on window width. */
function setWide(matches: boolean) {
  window.matchMedia = ((media: string) => ({
    matches,
    media,
    onchange: null,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
    dispatchEvent: () => false,
  })) as unknown as typeof window.matchMedia;
}

/** The chat workspace, reduced to what the pane interacts with. */
function Workspace({
  sheet = true,
  pane = true,
}: {
  sheet?: boolean;
  /** `false` is an agent's own page: a sheet with no conversation to sit beside. */
  pane?: boolean;
}) {
  const body = (
    <div className="flex">
      <div data-testid="chat">
        <textarea data-testid="composer" />
      </div>
      {pane && <WorkspacePaneOutlet />}
      {sheet && (
        <WorkspaceSheet
          title="Backtest chart"
          onClose={() => {
            closes += 1;
          }}
          bleed
          defaultZen
        >
          <p data-testid="report">report body</p>
        </WorkspaceSheet>
      )}
    </div>
  );
  return pane ? <WorkspacePaneProvider>{body}</WorkspacePaneProvider> : body;
}

async function render(props: Parameters<typeof Workspace>[0] = {}) {
  await act(async () => {
    root.render(<Workspace {...props} />);
  });
}

const outlet = () =>
  container.querySelector<HTMLElement>('aside[aria-label="Workspace pane"]');
const report = () =>
  document.querySelector<HTMLElement>('[data-testid="report"]');
const overlay = () => document.querySelector<HTMLElement>(".fixed.inset-0");
const button = (title: string) =>
  document.querySelector<HTMLButtonElement>(`button[title^="${title}"]`);

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

async function press(key: string) {
  await act(async () => {
    window.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  closes = 0;
  localStorage.clear();
  setWide(true);
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("WorkspaceSheet beside a conversation", () => {
  it("opens in the pane, leaving the chat on screen", async () => {
    await render();

    expect(outlet()!.contains(report())).toBe(true);
    // No overlay, so nothing is covering the transcript — and the composer is
    // still there to type in.
    expect(overlay()).toBeNull();
    expect(document.querySelector('[data-testid="composer"]')).toBeTruthy();
  });

  it("ignores defaultZen there — a report must not blank the chat", async () => {
    // The stored preference is the zen one; the pane still wins, or every
    // reader who once maximised a report would lose the chat for good.
    localStorage.setItem("condor_sheet_zen", "1");
    await render();

    expect(overlay()).toBeNull();
    expect(outlet()!.contains(report())).toBe(true);
  });

  it("gives the pane width only while something is in it", async () => {
    await render({ sheet: false });
    expect(outlet()!.className).toBe("hidden");

    await render({ sheet: true });
    expect(outlet()!.className).not.toBe("hidden");

    await render({ sheet: false });
    expect(outlet()!.className).toBe("hidden");
  });

  it("goes full screen and comes back, without persisting either", async () => {
    await render();

    await click(button("Full screen")!);
    expect(overlay()).toBeTruthy();
    expect(outlet()!.contains(report())).toBe(false);

    await click(button("Back beside the chat")!);
    expect(overlay()).toBeNull();
    expect(outlet()!.contains(report())).toBe(true);
    // Nothing written down: the next report opens beside the chat again.
    expect(localStorage.getItem("condor_sheet_zen")).toBeNull();
  });

  it("leaves Escape to the conversation and keeps Close", async () => {
    await render();

    await press("Escape");
    expect(closes).toBe(0);

    await click(button("Close")!);
    expect(closes).toBe(1);
  });
});

describe("WorkspaceSheet without a pane", () => {
  it("stays an overlay on a page that has no conversation", async () => {
    await render({ pane: false });

    expect(overlay()).toBeTruthy();
    await press("Escape");
    expect(closes).toBe(1);
  });

  it("stays an overlay in a window too narrow to split", async () => {
    setWide(false);
    await render();

    expect(overlay()).toBeTruthy();
    expect(outlet()!.className).toBe("hidden");
  });
});
