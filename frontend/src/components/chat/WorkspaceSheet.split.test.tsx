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
import { flushSync } from "react-dom";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
  second = false,
  pane = true,
}: {
  sheet?: boolean;
  /** A delegation opened from the dock while a report already holds the pane. */
  second?: boolean;
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
      {second && (
        <WorkspaceSheet title="Delegation" onClose={() => {}}>
          <p data-testid="delegation">delegation body</p>
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
const handle = () => container.querySelector<HTMLElement>('[role="separator"]');
/** The pane's share of the row, as the flex ratio the outlet renders. */
const grow = () => Number(outlet()!.style.flexGrow);

/**
 * The row the drag measures itself against.
 *
 * jsdom lays nothing out — every `offsetWidth` is 0 and every rect is empty — so
 * the two columns are given widths and the pane a right edge, which is the whole
 * of the reference frame `PaneResizeHandle` captures on mousedown.
 */
const CHAT_PX = 380;
const PANE_PX = 620;
const AVAIL = CHAT_PX + PANE_PX;
const ROW_RIGHT = 1200;

function stubLayout() {
  Object.defineProperty(HTMLElement.prototype, "offsetWidth", {
    configurable: true,
    get(this: HTMLElement) {
      if (this.tagName === "ASIDE") return PANE_PX;
      if (this.dataset.testid === "chat") return CHAT_PX;
      return 0;
    },
  });
  HTMLElement.prototype.getBoundingClientRect = () =>
    ({ right: ROW_RIGHT, left: ROW_RIGHT - PANE_PX, width: PANE_PX }) as DOMRect;
}

/** Grab the handle and drag it to `clientX`, without letting go. */
async function dragTo(clientX: number) {
  await act(async () => {
    handle()!.dispatchEvent(
      new MouseEvent("mousedown", { bubbles: true, clientX: ROW_RIGHT - PANE_PX }),
    );
  });
  await act(async () => {
    document.dispatchEvent(new MouseEvent("mousemove", { bubbles: true, clientX }));
  });
}

async function drop() {
  await act(async () => {
    document.dispatchEvent(new MouseEvent("mouseup", { bubbles: true }));
  });
}

/** The flex ratio a pane of `px` implies, for a row of {@link AVAIL}. */
const ratio = (px: number) => px / (AVAIL - px);
const report = () =>
  document.querySelector<HTMLElement>('[data-testid="report"]');
const delegation = () =>
  document.querySelector<HTMLElement>('[data-testid="delegation"]');
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
  stubLayout();
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

describe("The split between the two", () => {
  it("opens on the reader's side of the ratio it replaces", async () => {
    await render();

    // 62/38, not the 58/42 the pane was hard-coded to: what is in it is a page,
    // and the transcript stops using width past its own measure.
    expect(grow()).toBeCloseTo(ratio(0.62 * AVAIL), 3);
    expect(grow()).toBeGreaterThan(1.4);
  });

  it("moves with the handle, and says so while it is moving", async () => {
    await render();

    await dragTo(ROW_RIGHT - 500);
    expect(grow()).toBeCloseTo(ratio(500), 3);
    // The cursor is the drag's, everywhere, and nothing selects under it.
    expect(document.body.style.cursor).toBe("col-resize");
    expect(document.body.style.userSelect).toBe("none");

    await drop();
    expect(grow()).toBeCloseTo(ratio(500), 3);
    expect(document.body.style.cursor).toBe("");
    expect(document.body.style.userSelect).toBe("");
  });

  it("lets neither column be squeezed out", async () => {
    await render();

    // Dragged past the pane's own floor, it stops at the floor.
    await dragTo(ROW_RIGHT - 100);
    expect(grow()).toBeCloseTo(ratio(400), 3);
    await drop();

    // And the transcript keeps 360 of the row, however far the handle is pushed.
    await dragTo(ROW_RIGHT - 900);
    expect(grow()).toBeCloseTo(ratio(AVAIL - 360), 3);
    await drop();
  });

  it("is still there after a reload", async () => {
    localStorage.setItem("condor_pane_frac", "0.5");
    await render();

    expect(grow()).toBeCloseTo(1, 3);

    await dragTo(ROW_RIGHT - 500);
    await drop();
    expect(localStorage.getItem("condor_pane_frac")).toBe("0.5");
  });

  it("still drags when it has nowhere to write it down", async () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("private mode");
    });
    await render();

    await dragTo(ROW_RIGHT - 500);
    await drop();
    expect(grow()).toBeCloseTo(ratio(500), 3);
  });

  it("answers the keyboard, and a double-click puts it back", async () => {
    await render();

    // Left grows the pane, right gives the room back — the handle moves the way
    // the arrow points.
    await act(async () => {
      handle()!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }),
      );
    });
    expect(grow()).toBeCloseTo(ratio(0.64 * AVAIL), 3);

    await act(async () => {
      handle()!.dispatchEvent(new MouseEvent("dblclick", { bubbles: true }));
    });
    expect(grow()).toBeCloseTo(ratio(0.62 * AVAIL), 3);
  });

  it("exists only while something is in the pane", async () => {
    await render({ sheet: false });
    expect(handle()).toBeNull();

    await render({ sheet: true });
    expect(handle()).toBeTruthy();
  });

  it("does not exist in a window too narrow to split", async () => {
    setWide(false);
    await render();

    // The sheet is the overlay there, so there is no seam to drag either.
    expect(handle()).toBeNull();
    expect(outlet()!.className).toBe("hidden");
  });

  it("is what zen comes back to, not the default", async () => {
    await render();
    await dragTo(ROW_RIGHT - 500);
    await drop();

    await click(button("Full screen")!);
    expect(outlet()!.className).toBe("hidden");
    await click(button("Back beside the chat")!);

    expect(grow()).toBeCloseTo(ratio(500), 3);
  });
});

/**
 * Two sheets, one `aside`.
 *
 * Both portalling into the pane draws one over the other, with no way to tell
 * which scrollbar belongs to what. It was reachable before this feature — open
 * the routine library in the pane, then click a dock task, whose delegation is
 * a sheet too — and the agent panel would have walked into it twice over.
 */
describe("Only one sheet can be in the pane", () => {
  it("leaves the second as an overlay, with the first still beside the chat", async () => {
    await render({ sheet: true, second: true });

    expect(outlet()!.contains(report())).toBe(true);
    // The second is on screen, and it is not in the pane.
    expect(delegation()).toBeTruthy();
    expect(outlet()!.contains(delegation())).toBe(false);
    expect(overlay()).toBeTruthy();
    // Exactly one thing portalled in, whatever else is mounted.
    expect(outlet()!.children).toHaveLength(1);
  });

  it("does not care which order they arrive in", async () => {
    // The delegation first this time: whoever finds the pane free keeps it,
    // and the report opened after it is the overlay.
    await render({ sheet: false, second: true });
    expect(outlet()!.contains(delegation())).toBe(true);

    await render({ sheet: true, second: true });
    expect(outlet()!.contains(delegation())).toBe(true);
    expect(outlet()!.contains(report())).toBe(false);
    expect(outlet()!.children).toHaveLength(1);
  });

  /**
   * The agent panel opening a strategy: the holder unmounts and the claimant
   * mounts in one commit, and the claimant reads `taken` during that render —
   * before the holder has released.
   *
   * `flushSync` is the browser's own vantage point. It commits and runs layout
   * effects, including whatever they re-render synchronously, and stops short of
   * the passive effects — so the DOM it leaves behind is the frame the reader
   * actually sees. Released passively, the claimant was painted once as the
   * full-screen overlay it falls back to when the pane is taken, and replaced by
   * the docked pane a frame later; that flash is the bug this pins.
   */
  it("hands over in one frame, with no overlay in between", async () => {
    await render({ sheet: true, second: false });
    expect(outlet()!.contains(report())).toBe(true);

    globalThis.IS_REACT_ACT_ENVIRONMENT = false;
    flushSync(() => root.render(<Workspace sheet={false} second />));

    expect(overlay()).toBeNull();
    expect(outlet()!.contains(delegation())).toBe(true);
  });

  it("hands the pane on once the sheet holding it closes", async () => {
    await render({ sheet: true, second: true });
    expect(outlet()!.contains(report())).toBe(true);

    await render({ sheet: false, second: true });
    // The pane is free again and the sheet still open takes it, rather than
    // the column collapsing with a delegation stranded in an overlay.
    expect(outlet()!.contains(delegation())).toBe(true);
    expect(overlay()).toBeNull();
  });
});
