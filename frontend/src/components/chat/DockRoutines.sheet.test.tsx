/**
 * Where the library's picker lives, at each size the sheet has.
 *
 * The sheet's nav bar names what is open with the control that changes it: the
 * routine picker, and the ↑/↓ that walk the list, in place of a title and a
 * subtitle that only restated it. So the routine half is always here.
 *
 * The scope half — whose routines the list holds — belongs to the dock, beside
 * the runs this conversation has made. But the dock is not always on screen:
 * full screen the sheet covers it, and a reader can collapse the column
 * outright. So the bar carries the scope exactly when the dock does not, and
 * never both at once.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router-dom";
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

/**
 * @param split whether there is a workspace pane to sit beside — false is a
 *   narrow window or an agent's own page, where the sheet is full screen and
 *   the dock, open or not, is behind it.
 */
async function render({
  dockOpen,
  split = true,
  source = "brigado/bot_report",
}: {
  dockOpen: boolean;
  split?: boolean;
  source?: string;
}) {
  wide = split;
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <MemoryRouter>
        <QueryClientProvider client={qc}>
          <WorkspacePaneProvider>
            <WorkspacePaneOutlet />
            <RoutineLibrarySheet
              library={{ source }}
              instances={[]}
              routines={LIBRARY}
              scope="brigado"
              onScopeChange={() => {}}
              onSelectRoutine={(name) => picked.push(name)}
              dockOpen={dockOpen}
              agentName="Brigado"
              onClose={() => {}}
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
    await render({ dockOpen: true });

    expect(trigger()?.textContent).toContain("Bot Report");
    // No title and no subtitle repeating what the picker already says.
    expect(document.querySelector("h2")).toBeNull();
  });

  it("walks the list with the arrows, as the sidebar did", async () => {
    await render({ dockOpen: true });

    await click(arrow("Next routine")!);
    expect(picked).toEqual(["brigado/mm_regime_detector"]);
  });

  it("walks it from the keyboard too", async () => {
    await render({ dockOpen: true });

    await act(async () => {
      trigger()!.dispatchEvent(
        new KeyboardEvent("keydown", { key: "ArrowDown", bubbles: true }),
      );
    });

    expect(picked).toEqual(["brigado/mm_regime_detector"]);
  });

  it("has no arrow to walk past the end of the list", async () => {
    await render({ dockOpen: true, source: "brigado/bot_report" });

    // First of the two in scope: nothing before it.
    expect(arrow("Previous routine")?.disabled).toBe(true);
    expect(arrow("Next routine")?.disabled).toBe(false);
  });

  it("leaves the scope to the dock while the dock is on screen", async () => {
    await render({ dockOpen: true });

    // One scope select on screen, not two saying the same thing.
    expect(scopeSelect()).toBeNull();
  });

  it("carries the scope when the dock is collapsed", async () => {
    await render({ dockOpen: false });

    expect(scopeSelect()).toBeTruthy();
    expect(trigger()?.textContent).toContain("Bot Report");
  });

  it("carries it full screen too, where the sheet covers the open dock", async () => {
    await render({ dockOpen: true, split: false });

    expect(scopeSelect()).toBeTruthy();
  });
});
