/**
 * A report is in the dashboard's theme, whichever door you opened it from
 * (READ-274).
 *
 * The frame used to take a `theme` override, and the report browser was the one
 * caller that passed one — hard-coded to dark, so a reader on the light theme got
 * a dark document inside a light dashboard. The override is gone; what is pinned
 * here is what replaced it: the app's theme, normalised to the two values the
 * report protocol understands, and re-sent whenever the shell's theme flips.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/lib/api", () => ({
  api: { getReportHtml: () => Promise.resolve("<html></html>") },
}));

// Read at module scope by the theme hook; jsdom has no implementation, and the
// stored theme below has to be in place before the hook's module first runs.
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
localStorage.setItem("condor_theme", "light");

const { ReportFrame } = await import("./ReportFrame");
const { useTheme } = await import("@/hooks/useTheme");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

/**
 * What the frame told the report.
 *
 * jsdom gives a `srcDoc` frame a real `contentWindow`, but nothing that records
 * what was posted into it, so the property is swapped for a window that does.
 */
const posted: string[] = [];
const fakeWindow = {
  postMessage: (msg: { type: string; theme: string }) => {
    if (msg.type === "set-theme") posted.push(msg.theme);
  },
};

let container: HTMLDivElement;
let root: Root;
let setTheme: (t: "dark" | "light" | "colorblind") => void;

/** Exposes the shell's theme control to the test, beside the frame it drives. */
function Harness() {
  const { setTheme: set } = useTheme();
  useEffect(() => {
    setTheme = set;
  }, [set]);
  return <ReportFrame reportId="r1" title="A report" />;
}

async function render() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  await act(async () => {
    root.render(
      <QueryClientProvider client={qc}>
        <Harness />
      </QueryClientProvider>,
    );
  });
  // The HTML lands a tick later; the iframe only exists once it has.
  await act(async () => {
    await new Promise((r) => setTimeout(r, 0));
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  posted.length = 0;
  Object.defineProperty(HTMLIFrameElement.prototype, "contentWindow", {
    configurable: true,
    get: () => fakeWindow,
  });
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("ReportFrame's theme", () => {
  it("opens in the app's theme, and follows it without reopening", async () => {
    await render();

    // Light app, light report — no override, no second control to find.
    expect(posted.at(-1)).toBe("light");

    await act(async () => setTheme("dark"));
    expect(posted.at(-1)).toBe("dark");

    // Colorblind is a dark-based palette, and the report protocol has no third
    // value: it reads as dark, which is what surrounds the frame anyway.
    await act(async () => setTheme("colorblind"));
    expect(posted.at(-1)).toBe("dark");

    await act(async () => setTheme("light"));
    expect(posted.at(-1)).toBe("light");
  });
});
