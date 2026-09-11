/**
 * What the detail promises (READ-234).
 *
 * It renders an outcome and only an outcome. That is not a style choice: its
 * one consumer, `DelegationSheet`, already prints the ask above it and the
 * status and elapsed time in its subtitle, so anything this component said
 * about those would be a second copy free to disagree with the first. It used
 * to carry that header behind a `showTask` prop nobody ever passed, and a
 * `clamped` prop whose only caller turned it off — both removed here, and both
 * pinned below so a future caller cannot quietly reintroduce the duplication.
 *
 * The height is the other half: a result is the agent's full narrative answer
 * and the sheet is what scrolls, so the body carries no max-height of its own.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { Delegation } from "@/lib/api";
import { DelegationDetail } from "./DelegationDetail";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const RECORD: Delegation = {
  task_id: "t-1",
  agent: "condor",
  user_id: 1,
  chat_id: 1,
  server_name: null,
  task: "count the open positions",
  status: "done",
  result: "**Seven** positions are open.",
  error: "",
  conversation_id: "",
  started_at: 1_700_000_000,
};

let container: HTMLDivElement;
let root: Root;

async function render(d: Delegation) {
  await act(async () => {
    root.render(<DelegationDetail delegation={d} />);
  });
}

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(async () => {
  await act(async () => root.unmount());
  container.remove();
});

describe("DelegationDetail", () => {
  it("renders the result as markdown under a Result label", async () => {
    await render(RECORD);

    expect(container.textContent).toContain("Result");
    expect(container.textContent).toContain("Seven positions are open.");
    // Markdown, not the literal asterisks the record carries.
    expect(container.querySelector("strong")?.textContent).toBe("Seven");
  });

  it("says nothing about the task, agent or status — the sheet owns those", async () => {
    await render(RECORD);

    expect(container.textContent).not.toContain("count the open positions");
    expect(container.textContent).not.toContain("condor");
    expect(container.textContent?.toLowerCase()).not.toContain("done");
  });

  it("leaves the body unclamped so the sheet is what scrolls", async () => {
    await render(RECORD);

    expect(container.querySelector(".max-h-64")).toBeNull();
    expect(container.querySelector("[class*='overflow-auto']")).toBeNull();
  });

  it("renders an error as raw monospace text instead of the result", async () => {
    await render({
      ...RECORD,
      status: "error",
      result: "",
      error: "Traceback: **not** markdown",
    });

    expect(container.textContent).toContain("Error");
    const pre = container.querySelector("pre");
    expect(pre?.textContent).toBe("Traceback: **not** markdown");
  });

  it("distinguishes a running task from one that finished empty", async () => {
    await render({ ...RECORD, status: "running", result: "" });
    expect(container.textContent).toContain("Running…");

    await render({ ...RECORD, result: "" });
    expect(container.textContent).toContain("(no output)");
  });
});
