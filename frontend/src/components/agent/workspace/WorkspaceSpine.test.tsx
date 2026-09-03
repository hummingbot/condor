/**
 * The spine is the workspace's only navigation, so it has to hold every view.
 *
 * Two groups and no page reload between any two of them: that is the whole
 * claim FEAT-103 makes about getting from a run back to a memory. It also has
 * to say where you are while you are inside a tick, which is a selection rather
 * than a section — the rule lives in `views.ts` and is honoured here.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { KNOWLEDGE_TABS } from "@/components/agent/knowledgeTabs";
import { WorkspaceSpine } from "./WorkspaceSpine";
import { spineSectionFor, type WorkspaceViewId } from "./views";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function render(props: Parameters<typeof WorkspaceSpine>[0]) {
  act(() => {
    root.render(<WorkspaceSpine {...props} />);
  });
}

const entries = () => [
  ...container.querySelectorAll<HTMLButtonElement>("[data-spine-entry]"),
];
const entry = (id: string) =>
  container.querySelector<HTMLButtonElement>(`[data-spine-entry="${id}"]`);
const current = () =>
  entries().find((e) => e.getAttribute("aria-current") === "page");

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

describe("the spine", () => {
  it("offers the loop and the seven sections in one column", () => {
    render({ current: "now", onSelect: () => {} });

    for (const id of ["now", "runs", "playbook", "money", "fleet"]) {
      expect(entry(id)).not.toBeNull();
    }
    // Every section `AgentKnowledge` has, because this replaced its tab strip:
    // a section the spine forgot is a section with no door.
    for (const tab of KNOWLEDGE_TABS) {
      expect(entry(tab)).not.toBeNull();
    }
  });

  it("splits Doing from Being, in that order", () => {
    render({ current: "now", onSelect: () => {} });

    const ids = entries().map((e) => e.dataset.spineEntry);
    expect(ids.indexOf("fleet")).toBeLessThan(ids.indexOf("brain"));
    expect(container.textContent).toContain("Doing");
    expect(container.textContent).toContain("Being");
  });

  it("lights exactly one entry, and it is the one asked for", () => {
    render({ current: "memories", onSelect: () => {} });

    expect(entries().filter((e) => e.getAttribute("aria-current"))).toHaveLength(1);
    expect(current()!.dataset.spineEntry).toBe("memories");
  });

  it("keeps Runs lit while a tick of one is open", () => {
    // `tick` is not an entry — it is a selection any view can carry — so the
    // spine reports the section the reader came through.
    render({
      current: spineSectionFor("tick" as WorkspaceViewId),
      onSelect: () => {},
    });

    expect(current()!.dataset.spineEntry).toBe("runs");
  });

  it("hands the view up rather than navigating", () => {
    const onSelect = vi.fn();
    render({ current: "now", onSelect });

    act(() => entry("skills")!.click());

    // The page writes it into `?view=`; the spine never touches the URL, which
    // is what lets one component serve every host that has one.
    expect(onSelect).toHaveBeenCalledWith("skills");
  });

  it("carries Now's alert count wherever the reader is", () => {
    render({ current: "brain", onSelect: () => {}, alertCount: 2 });

    expect(entry("now")!.textContent).toContain("2");
    expect(container.querySelectorAll("[data-spine-alerts]")).toHaveLength(1);
  });

  it("says nothing when there is nothing to say", () => {
    render({ current: "now", onSelect: () => {}, alertCount: 0 });

    expect(container.querySelector("[data-spine-alerts]")).toBeNull();
  });
});
