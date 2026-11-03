/**
 * Where the library's picker lives, at each size the sheet has.
 *
 * The sheet's nav bar names what is open with the control that changes it: the
 * routine picker, and the ↑/↓ that walk the list, in place of a title and a
 * subtitle that only restated it.
 *
 * Beside it, and at every size, the scope — whose routines the list holds. The
 * two halves used to be split with the dock, which put the filter one column
 * away from the list it filters and took it off screen whenever that column
 * was folded. Both questions are asked where the answer is shown.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { RoutineInfo } from "@/lib/api";
import { WorkspacePaneOutlet, WorkspacePaneProvider } from "./WorkspacePane";

vi.mock("@/lib/api", () => ({ api: {} }));

/** The pane itself is tested in ReportBrowser.hosted.test.tsx. */
vi.mock("@/components/routines/ReportBrowser", () => ({
  ReportBrowser: () => <div data-testid="library" />,
}));

/** Whether the workspace is wide enough to split — set per case below. */
let wide = false;
window.matchMedia = ((media: string) => ({
  get matches() {
    return wide;
  },
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

const { RoutineLibrarySheet } = await import("./DockRoutines");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function routine(name: string, owner?: string): RoutineInfo {
  return {
    name,
    description: `${name} description`,
    is_continuous: false,
    category: "general",
    source: owner ? `agent:${owner}` : "routine",
    fields: {},
    last_modified: null,
    report_count: 0,
  };
}

const LIBRARY = [
  routine("brigado/bot_report", "brigado"),
  routine("brigado/mm_regime_detector", "brigado"),
  routine("mcap_comparison"),
];

let container: HTMLDivElement;
let root: Root;
let picked: string[];
let closes: number;
/** Where the router is, so the door out to the page can be checked. */
let here: string;

function LocationProbe() {
  const loc = useLocation();
  const at = loc.pathname + loc.search;
  // Recorded from an effect rather than during render: writing a module
  // variable while rendering is a side effect the purity rules forbid. Every
  // render here happens inside `act`, so the effect has flushed by the time a
  // test reads `here`.
  useEffect(() => {
    here = at;
  }, [at]);
  return null;
}

/**
 * @param split whether there is a workspace pane to sit beside — false is a
 *   narrow window or an agent's own page, where the sheet is full screen and
 *   the dock, open or not, is behind it.
 */
async function render({
  split = true,
  source = "brigado/bot_report",
  reportId,
}: {
  split?: boolean;
  source?: string;
  reportId?: string;
} = {}) {
  wide = split;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <LocationProbe />
        <QueryClientProvider client={qc}>
          <WorkspacePaneProvider>
            <WorkspacePaneOutlet />
            <RoutineLibrarySheet
              library={{ source, reportId }}
              instances={[]}
              routines={LIBRARY}
              scope="brigado"
              onScopeChange={() => {}}
              onSelectRoutine={(name) => picked.push(name)}
              agentName="Brigado"
              onClose={() => {
                closes += 1;
              }}
            />
          </WorkspacePaneProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  // The pane is a portal host: the sheet finds it on its second render.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

/** The sheet's own nav bar, which is where the picker sits. */
const trigger = () =>
  document.querySelector<HTMLButtonElement>('button[aria-label="Routine"]');
const scopeSelect = () =>
  document.querySelector<HTMLSelectElement>(
    'select[aria-label="Routine scope"]',
  );
const arrow = (label: string) =>
  document.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`);

async function click(el: HTMLElement) {
  await act(async () => {
    el.dispatchEvent(new MouseEvent("click", { bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  picked = [];
  closes = 0;
  here = "";
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("the library sheet's nav bar", () => {
  it("says which routine is open with the control that changes it", async () => {
    await render();

    expect(trigger()?.textContent).toContain("Bot Report");
    // No title and no subtitle repeating what the picker already says.
    expect(document.querySelector("h2")).toBeNull();
  });

  it("walks the list with the arrows, as the sidebar did", async () => {
    await render();

    await click(arrow("Next routine")!);
    expect(picked).toEqual(["brigado/mm_regime_detector"]);
  });

  it("walks it from the keyboard too", async () => {
    await render();

    await act(async () => {
      trigger()!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
      );
    });

    expect(picked).toEqual(["brigado/mm_regime_detector"]);
  });

  it("has no arrow to walk past the end of the list", async () => {
    await render({ source: "brigado/bot_report" });

    // First of the two in scope: nothing before it.
    expect(arrow("Previous routine")?.disabled).toBe(true);
    expect(arrow("Next routine")?.disabled).toBe(false);
  });

  it("asks whose routines beside which one, always", async () => {
    await render();

    // The pair used to be split with the dock, so the scope went off screen
    // whenever that column did. Both questions are asked over the list.
    expect(scopeSelect()).toBeTruthy();
    expect(trigger()?.textContent).toContain("Bot Report");
  });

  it("asks it full screen too, where there is no pane at all", async () => {
    await render({ split: false });

    expect(scopeSelect()).toBeTruthy();
  });
});

/**
 * The pane's full-screen version is the page, not a taller sheet (FEAT-091).
 *
 * `/routines` is the same browser with the sidebar, the cards and the runs
 * strip the pane has no room for, so maximizing hands the reader over to it
 * rather than blanking the conversation with a viewport-sized sheet — and hands
 * over what they were reading with them.
 */
describe("maximizing the library pane", () => {
  const maximize = () =>
    document.querySelector<HTMLButtonElement>(
      'button[title="Full screen, on its own page (f)"]',
    );

  it("goes to the library's own page, on the routine that was open", async () => {
    await render();

    await click(maximize()!);

    expect(here).toBe("/routines?agent=brigado&routine=brigado%2Fbot_report");
    // What the pane held is on screen now; leaving it open behind the page
    // would be two copies of one thing again.
    expect(closes).toBe(1);
  });

  it("opens the page on the same report, not the routine's newest", async () => {
    await render({ reportId: "r-42" });

    await click(maximize()!);

    expect(here).toContain("report=r-42");
  });

  it("answers `f` the same way", async () => {
    await render();

    await act(async () => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "f" }));
    });

    expect(here).toBe("/routines?agent=brigado&routine=brigado%2Fbot_report");
  });

  it("still zens where there is no pane, and keeps that reversible", async () => {
    // Below `xl` the sheet opens full screen already, so the button is the way
    // back rather than a second door out.
    await render({ split: false });

    expect(maximize()).toBeNull();
    expect(
      document.querySelector('button[title="Exit full screen (f)"]'),
    ).toBeTruthy();
  });
});
