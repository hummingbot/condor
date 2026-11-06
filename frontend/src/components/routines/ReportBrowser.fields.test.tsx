/**
 * Greptile, PR #314: `GET /routines` never imports an agent's own routine, so
 * its Config form always built from `fields: {}` and showed "No configurable
 * fields" — covering every agent's own routines, shipped and locally created,
 * not just hub installs. The Config panel now resolves them itself, once the
 * operator actually opens it, through `api.getRoutineFields`.
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

const getRoutineReports = vi.fn();
const getRoutineFields = vi.fn();
const runRoutine = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    getRoutines: () =>
      Promise.resolve([
        routine("scout/beta_check", "agent:scout", {}),
        routine("alpha_check", "general", {
          amount: { type: "int", default: 5, description: "Order size" },
        }),
      ]),
    getRoutineFields: (...a: unknown[]) => getRoutineFields(...a),
    getRoutineReports: (...a: unknown[]) => getRoutineReports(...a),
    getRoutineInstance: () => Promise.resolve(null),
    getRoutineSource: () => Promise.resolve({ filename: "x.py", source: "" }),
    runRoutine: (...a: unknown[]) => runRoutine(...a),
    scheduleRoutine: () => Promise.resolve({ instance_id: "i9" }),
    deleteReport: () => Promise.resolve({ deleted: true }),
    getReportHtml: () => Promise.resolve(""),
    getRoutineHooks: () => Promise.resolve({}),
  },
}));

vi.mock("./ReportFrame", () => ({
  ReportFrame: ({ reportId }: { reportId: string }) => (
    <div data-testid="frame">{reportId}</div>
  ),
}));

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
Element.prototype.scrollIntoView = () => {};

const { ReportBrowser } = await import("./ReportBrowser");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function routine(
  name: string,
  source: string,
  fields: RoutineInfo["fields"],
): RoutineInfo {
  return {
    name,
    description: name,
    is_continuous: false,
    category: "general",
    source,
    fields,
    last_modified: null,
    report_count: 0,
  } as RoutineInfo;
}

let container: HTMLDivElement;
let root: Root;

async function render(props: Record<string, unknown> = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <ServerContext.Provider
          value={{ server: "dashboard-server", setServer: () => {} }}
        >
          <ReportBrowser hosted instances={[]} onClose={() => {}} {...props} />
        </ServerContext.Provider>
      </QueryClientProvider>,
    );
  });
  for (let i = 0; i < 4; i++) {
    await act(async () => {
      await new Promise((r) => setTimeout(r, 0));
    });
  }
}

const openConfig = async () => {
  const configButton = [...container.querySelectorAll("button")].find(
    (b) => b.textContent?.trim() === "Config",
  )!;
  await act(async () => {
    configButton.click();
  });
};

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  localStorage.clear();
  getRoutineReports.mockResolvedValue({ reports: [] });
  getRoutineFields.mockResolvedValue({
    fields: { margin_quote: { type: "float", default: 0.5, description: "Margin quote" } },
  });
  runRoutine.mockResolvedValue({ instance_id: "i9" });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.clearAllMocks();
});

describe("an agent routine's Config form", () => {
  it("fetches its fields on demand and fills the form with them", async () => {
    await render({ initialSource: "scout/beta_check" });
    expect(container.textContent).not.toContain("No configurable fields");

    await openConfig();
    for (let i = 0; i < 4; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }

    expect(getRoutineFields).toHaveBeenCalledWith("scout/beta_check");
    expect(container.textContent).toContain("Margin quote");
    expect(container.textContent).not.toContain("No configurable fields");
  });

  it("never fetches for a routine whose fields already came back non-empty", async () => {
    await render({ initialSource: "alpha_check" });

    await openConfig();
    for (let i = 0; i < 4; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }

    expect(getRoutineFields).not.toHaveBeenCalled();
    expect(container.textContent).toContain("Order size");
  });

  it("never fetches merely from a routine being selected, Config still closed", async () => {
    // Greptile, PR #314: resolving a routine to read its fields is importing
    // it (FEAT-147) — browsing to one to look at its reports must never do
    // that on its own. Only `showConfigPanel` is the explicit action that
    // already means code is about to run.
    await render({ initialSource: "scout/beta_check" });
    for (let i = 0; i < 4; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }

    expect(getRoutineFields).not.toHaveBeenCalled();
  });
});

describe("running an agent routine without ever opening Config", () => {
  it("still resolves its fields, so a saved custom value is not dropped", async () => {
    // Greptile, PR #314: `buildConfigValues` only copies a saved localStorage
    // value for a key that is also in `routine.fields` — so once that stayed
    // `{}` until Config opened, pressing Run directly silently sent `{}`
    // instead of a value the owner had saved in an earlier session.
    localStorage.setItem(
      "routine_config:scout/beta_check",
      JSON.stringify({ margin_quote: 0.75 }),
    );
    await render({ initialSource: "scout/beta_check" });

    const runButton = container.querySelector<HTMLButtonElement>(
      'button[title="Run with current config"]',
    )!;
    await act(async () => {
      runButton.click();
    });
    for (let i = 0; i < 4; i++) {
      await act(async () => {
        await new Promise((r) => setTimeout(r, 0));
      });
    }

    expect(getRoutineFields).toHaveBeenCalledWith("scout/beta_check");
    expect(runRoutine).toHaveBeenCalledWith(
      "dashboard-server",
      "scout/beta_check",
      { margin_quote: 0.75 },
      expect.anything(),
    );
  });
});
