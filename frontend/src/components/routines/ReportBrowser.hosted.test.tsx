/**
 * The routine library, hosted in the workspace pane (FEAT-077).
 *
 * Full screen the browser *is* the window, and everything in it may assume so.
 * Beside a live conversation none of that holds: the pane sizes it, the sheet
 * closes it, and a keystroke meant for the composer must never reach it. These
 * cases pin the three properties that make hosting safe — no viewport-owning
 * root, no window key listener, no second Close button — and the one that makes
 * it useful: a run launched from the pane goes to the conversation's server,
 * carrying the session that asked for it.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ServerContext } from "@/hooks/useServer";
import type { RoutineInfo } from "@/lib/api";

const getRoutines = vi.fn();
const getRoutineReports = vi.fn();
const runRoutine = vi.fn();
const scheduleRoutine = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getRoutines: (...a: unknown[]) => getRoutines(...a),
    getRoutineReports: (...a: unknown[]) => getRoutineReports(...a),
    getRoutineSource: () => Promise.resolve({ filename: "x.py", source: "" }),
    getRoutineInstance: () => Promise.resolve(null),
    runRoutine: (...a: unknown[]) => runRoutine(...a),
    scheduleRoutine: (...a: unknown[]) => scheduleRoutine(...a),
    deleteReport: () => Promise.resolve({ deleted: true }),
    getReportHtml: () => Promise.resolve(""),
    getRoutineHooks: () => Promise.resolve({}),
  },
}));

// Read at module scope by the theme hook the report frame pulls in; jsdom has
// no implementation, so it has to exist before the import below.
window.matchMedia = ((media: string) => ({
  matches: false,
  media,
  onchange: null,
  addEventListener: () => {},
  removeEventListener: () => {},
  addListener: () => {},
  removeListener: () => {},
  dispatchEvent: () => false,
})) as unknown as typeof window.matchMedia;

// jsdom implements neither, and the browser calls both on mount.
Element.prototype.scrollIntoView = () => {};

const { ReportBrowser } = await import("./ReportBrowser");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function routine(name: string, owner?: string): RoutineInfo {
  return {
    name,
    description: name,
    is_continuous: false,
    category: "general",
    source: owner ? `agent:${owner}` : "routine",
    fields: {},
    last_modified: null,
    report_count: 0,
  };
}

let container: HTMLDivElement;
let root: Root;
let closes: number;

const RUN_CONTEXT = {
  serverName: "chat-server",
  sessionKey: "web:42:main",
  agentSlug: "scout",
};

async function render(props: {
  hosted?: boolean;
  runContext?: typeof RUN_CONTEXT;
  initialSource?: string;
  initialSourceTypeFilter?: string;
} = {}) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider
          value={{ server: "dashboard-server", setServer: () => {} }}
        >
          <ReportBrowser
            instances={[]}
            onClose={() => {
              closes += 1;
            }}
            {...props}
          />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  // The routine list lands a tick later, and the first source is chosen in an
  // effect off the back of it.
  for (let i = 0; i < 3; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

/** The browser's own root — the only child the tree renders. */
const browser = () => container.firstElementChild as HTMLElement;
const heading = () => container.querySelector("h2")?.textContent?.trim();
const button = (title: string) =>
  container.querySelector<HTMLButtonElement>(`button[title="${title}"]`);
/** The toolbar's own theme toggle, removed in READ-274. */
const themeButton = () =>
  container.querySelector<HTMLButtonElement>('button[title^="Switch to "]');
/** The header row's agent badge — the sidebar rows carry one of their own. */
const agentBadge = () =>
  container
    .querySelector("h2")
    ?.parentElement?.querySelector<HTMLElement>("span.uppercase")
    ?.textContent?.trim() ?? null;
/** What the scope select is showing. */
const scopeSelect = () =>
  container.querySelector<HTMLSelectElement>('select[aria-label="Routine scope"]');

async function press(key: string, on: EventTarget) {
  await act(async () => {
    on.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  closes = 0;
  localStorage.clear();
  getRoutines.mockResolvedValue([routine("alpha_check"), routine("beta_check")]);
  getRoutineReports.mockResolvedValue({ reports: [] });
  runRoutine.mockResolvedValue({ instance_id: "i1" });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("ReportBrowser hosted in the pane", () => {
  it("fills its container instead of owning the viewport", async () => {
    await render({ hosted: true });

    expect(browser().className).not.toContain("fixed");
    expect(browser().className).not.toContain("inset-0");
    // The sidebar opens on its rail, which is what makes it fit at pane width.
    expect(container.querySelector(".w-12")).toBeTruthy();
    expect(container.querySelector(".w-64")).toBeNull();
  });

  it("leaves the window's keys to the conversation", async () => {
    await render({ hosted: true });
    expect(heading()).toBe("Alpha Check");

    // What the composer sees never reaches the report list…
    await press("ArrowDown", window);
    expect(heading()).toBe("Alpha Check");
    await press("Escape", window);
    expect(closes).toBe(0);

    // …but the browser still answers to keys aimed at itself.
    await press("ArrowDown", browser());
    expect(heading()).toBe("Beta Check");
  });

  it("leaves closing to the sheet", async () => {
    await render({ hosted: true });

    expect(button("Close (Esc)")).toBeNull();
    // Escape inside it is for its own panels, never for the pane.
    await press("Escape", browser());
    expect(closes).toBe(0);
  });

  it("leaves the theme to the dashboard", async () => {
    await render({ hosted: true });

    // The report is a document inside the app, not a surface with a mode of its
    // own: there is no second, differently-shaped theme control in the toolbar.
    expect(themeButton()).toBeNull();
  });

  it("runs on the conversation's server, as the conversation", async () => {
    await render({ hosted: true, runContext: RUN_CONTEXT });

    await act(async () => {
      button("Run with current config")!.click();
    });

    expect(runRoutine).toHaveBeenCalledWith(
      "chat-server",
      "alpha_check",
      {},
      { sessionKey: "web:42:main", attributeTo: "scout" },
    );
  });
});

describe("ReportBrowser on its own page", () => {
  it("still owns the viewport, its keys and its Close button", async () => {
    await render();

    expect(browser().className).toContain("fixed inset-0");
    expect(container.querySelector(".w-64")).toBeTruthy();

    await press("ArrowDown", window);
    expect(heading()).toBe("Beta Check");
    await press("Escape", window);
    expect(closes).toBe(1);
    expect(button("Close (Esc)")).toBeTruthy();
  });

  it("leaves the theme to the dashboard here too", async () => {
    await render();

    expect(themeButton()).toBeNull();
  });

  it("runs on the dashboard's server, with no conversation behind it", async () => {
    await render();

    await act(async () => {
      button("Run with current config")!.click();
    });

    expect(runRoutine).toHaveBeenCalledWith(
      "dashboard-server",
      "alpha_check",
      {},
      { sessionKey: undefined, attributeTo: undefined },
    );
  });
});

/**
 * READ-276: the header used to name the owning agent three times in one row —
 * the scope select, the `{agent}/` prefix the raw key carries into the `<h2>`,
 * and the badge. The prefix is gone from the title, and the badge appears only
 * where the select is not already naming that agent.
 */
describe("ReportBrowser header, for an agent-owned routine", () => {
  const AGENT_LIST = [
    routine("alpha_check"),
    routine("brigado/mm_regime_detector", "brigado"),
    routine("scout/depth_watch", "scout"),
  ];

  beforeEach(() => {
    getRoutines.mockResolvedValue(AGENT_LIST);
  });

  it("titles the routine without its owner's slug", async () => {
    await render({
      initialSource: "brigado/mm_regime_detector",
      initialSourceTypeFilter: "brigado",
    });

    // The sheet above this pane titles it the same way (DockRoutines).
    expect(heading()).toBe("Mm Regime Detector");
  });

  it("drops the badge when the scope already names that agent", async () => {
    await render({
      initialSource: "brigado/mm_regime_detector",
      initialSourceTypeFilter: "brigado",
    });

    expect(scopeSelect()?.value).toBe("brigado");
    expect(agentBadge()).toBeNull();
  });

  it("keeps the badge when the list is mixed", async () => {
    await render({
      initialSource: "brigado/mm_regime_detector",
      initialSourceTypeFilter: "all",
    });

    expect(agentBadge()).toBe("brigado");
  });

  it("keeps the badge when the pane widens itself past the picked scope", async () => {
    // Focused on brigado's routine while the picker says scout: the pane shows
    // "All routines" rather than hide what the reader clicked, so the badge is
    // the only thing naming the owner.
    await render({
      initialSource: "brigado/mm_regime_detector",
      initialSourceTypeFilter: "scout",
    });

    expect(scopeSelect()?.value).toBe("all");
    expect(agentBadge()).toBe("brigado");
  });

  it("shows no badge for a library routine", async () => {
    await render({ initialSource: "alpha_check", initialSourceTypeFilter: "all" });

    expect(heading()).toBe("Alpha Check");
    expect(agentBadge()).toBeNull();
  });

  it("still shows the owner prefix on the sidebar rows", async () => {
    await render({ initialSourceTypeFilter: "all" });

    const rows = Array.from(
      container.querySelectorAll(".w-64 button"),
    ).map((b) => b.textContent ?? "");
    expect(rows.some((t) => t.includes("brigado/mm regime detector"))).toBe(true);
  });
});
