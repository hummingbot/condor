/**
 * The perf browser's stop dialog says what the selection will actually do
 * (CORR-307).
 *
 * Its multi-select can include lp executors, and an LP executor's pool position
 * is closed on-chain whether or not the keep box is ticked — only the withdrawn
 * tokens are kept. The dialog used to say the position stayed open regardless.
 * These cases render the real dialog, so the wording is pinned where a reader
 * meets it, and check the keep flag still reaches `onConfirm` unchanged.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { StopConfirmDialog } from "./ExecutorRows";
import { type ExecutorInfo } from "@/lib/api";

declare global {
  // eslint-disable-next-line no-var
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const executor = (id: string, type: string) => ({ id, type }) as ExecutorInfo;

let container: HTMLDivElement;
let root: Root;

function render(node: React.ReactNode) {
  act(() => {
    root.render(node);
  });
}

const helper = () => document.querySelector<HTMLElement>("form p")!.textContent ?? "";
const label = () => document.querySelector<HTMLElement>("form label span")!.textContent ?? "";
const checkbox = () => document.querySelector<HTMLInputElement>('input[type="checkbox"]')!;

function tickKeep() {
  const box = checkbox();
  act(() => {
    box.click();
  });
}

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

describe("StopConfirmDialog copy", () => {
  it("tells an LP stop the pool position closes either way", () => {
    render(
      <StopConfirmDialog
        ids={["e1"]}
        executors={[executor("e1", "lp")]}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(label()).toBe("Keep token exposure");
    expect(helper()).toContain("closed on-chain");

    tickKeep();
    expect(helper()).toContain("position hold");
    expect(helper()).not.toMatch(/stays open|remain(s)? open/);
  });

  it("keeps the position-stays-open wording for a non-LP stop", () => {
    render(
      <StopConfirmDialog
        ids={["e1"]}
        executors={[executor("e1", "position")]}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(label()).toBe("Keep position open");

    tickKeep();
    expect(helper()).toContain("stays open on the exchange");
  });

  it("ignores executors outside the ids being stopped", () => {
    // The scoped list holds every visible row; only the armed ids matter.
    render(
      <StopConfirmDialog
        ids={["e2"]}
        executors={[executor("e1", "lp"), executor("e2", "grid")]}
        onConfirm={() => {}}
        onCancel={() => {}}
      />,
    );
    expect(label()).toBe("Keep position open");
  });

  it("still hands the keep flag through untouched", () => {
    const onConfirm = vi.fn();
    render(
      <StopConfirmDialog
        ids={["e1"]}
        executors={[executor("e1", "lp")]}
        onConfirm={onConfirm}
        onCancel={() => {}}
      />,
    );
    tickKeep();
    act(() => {
      document.querySelector<HTMLButtonElement>('button[type="submit"]')!.click();
    });
    expect(onConfirm).toHaveBeenCalledWith(["e1"], true);
  });
});
