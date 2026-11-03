/**
 * The rail's promise is that one column answers "what has this agent done".
 *
 * Three of the things FEAT-111 changed are only observable in the DOM: an agent
 * with no strategy at all now has rows at all, a chat is named by its title
 * rather than by a strategy it does not have, and the two asymmetries a chat
 * introduces — it is private, and it is kept for less time than a session — are
 * *stated* rather than left to read as data loss.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RunRail } from "./RunRail";
import type { AgentRunRow } from "@/lib/api";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function run(over: Partial<AgentRunRow> = {}): AgentRunRow {
  return {
    run_id: "s:1",
    kind: "session",
    id: "1",
    number: 1,
    agent_id: "brigado.brl_mm_1",
    status: "stopped",
    execution_mode: "",
    tick_count: 20,
    snapshot_count: 20,
    started_at: 1_786_000_000,
    ended_at: 1_786_003_600,
    error: false,
    has_actions_log: true,
    strategy_slug: "brl_mm",
    strategy_name: "BRL MM",
    title: "",
    ...over,
  };
}

const chat = (over: Partial<AgentRunRow> = {}) =>
  run({
    run_id: "c:7f3a",
    kind: "conversation",
    id: "7f3a",
    number: 0,
    status: "",
    tick_count: 0,
    strategy_slug: "",
    strategy_name: "",
    title: "What is the fleet doing?",
    started_at: 1_786_010_000,
    ended_at: 1_786_010_600,
    ...over,
  });

const task = (over: Partial<AgentRunRow> = {}) =>
  run({
    run_id: "d:abc",
    kind: "delegation",
    id: "abc",
    number: 0,
    status: "done",
    execution_mode: "delegate",
    tick_count: 0,
    strategy_slug: "",
    strategy_name: "",
    title: "Rebalance the BRL book",
    started_at: 1_786_005_000,
    ended_at: 1_786_005_600,
    ...over,
  });

let container: HTMLDivElement;
let root: Root;
const onSelectRun = vi.fn();

async function render(runs: AgentRunRow[], over: Record<string, unknown> = {}) {
  await act(async () => {
    root.render(
      <RunRail
        runs={runs}
        strategyFilter={null}
        onStrategyFilter={() => {}}
        selectedKey={null}
        onSelectRun={onSelectRun}
        {...over}
      />,
    );
  });
}

function click(el: Element | null | undefined) {
  return act(async () => {
    (el as HTMLElement).click();
  });
}

const rows = () => [...container.querySelectorAll("[data-run-row]")];

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  onSelectRun.mockClear();
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

describe("RunRail", () => {
  it("lists an agent whose only work is conversations", async () => {
    // Condor's own case: no loop, no experiment, and a rail that used to say
    // "No runs yet" about an agent that had been talked to for months.
    await render([chat()]);
    expect(rows()).toHaveLength(1);
    expect(container.textContent).toContain("What is the fleet doing?");
    expect(container.textContent).not.toContain("No runs yet");
  });

  it("names a chat by its title and a loop run by its playbook", async () => {
    await render([chat(), run()]);
    expect(container.textContent).toContain("BRL MM");
    expect(container.textContent).toContain("What is the fleet doing?");
    // A tick is a loop concept; a chat says how long it lasted and no more.
    const [chatRow, loopRow] = rows();
    expect(loopRow.textContent).toContain("20 ticks");
    expect(chatRow.textContent).not.toContain("tick");
    expect(chatRow.textContent).toContain("10m");
  });

  it("says an untitled chat is one rather than leaving the row blank", async () => {
    await render([chat({ title: "" })]);
    expect(container.textContent).toContain("Untitled chat");
  });

  it("scopes the list to one kind, and offers only the kinds it has", async () => {
    await render([run(), chat(), task()]);
    const chips = container.querySelector("[data-run-kind-chips]");
    const labels = [...(chips?.querySelectorAll("button") ?? [])].map((b) =>
      b.textContent?.trim(),
    );
    expect(labels).toEqual(["all", "loops", "chats", "tasks"]);

    await click(
      [...(chips?.querySelectorAll("button") ?? [])].find(
        (b) => b.textContent?.trim() === "chats",
      ),
    );
    expect(rows().map((r) => r.getAttribute("data-run-row"))).toEqual([":c:7f3a"]);
  });

  it("offers no kind chips to an agent that only ever loops", async () => {
    await render([run(), run({ run_id: "s:2", id: "2", number: 2 })]);
    expect(container.querySelector("[data-run-kind-chips]")).toBeNull();
  });

  it("states that chats are private and shorter-lived, when there are any", async () => {
    await render([run()]);
    expect(container.textContent).not.toContain("yours alone");
    await render([run(), chat()]);
    expect(container.textContent).toContain("yours alone");
  });

  it("offers an older page only when the window was full", async () => {
    const onShowMore = vi.fn();
    await render([chat()], { hasMore: false, onShowMore });
    expect(container.querySelector("[data-run-show-more]")).toBeNull();

    await render([chat()], { hasMore: true, onShowMore });
    await click(container.querySelector("[data-run-show-more]"));
    expect(onShowMore).toHaveBeenCalledTimes(1);
  });

  it("hands the whole row back, so the caller decides where it opens", async () => {
    // A chat navigates to the chat and a loop run is a selection — a decision
    // the workspace makes, which is why the rail reports the row and not an id.
    await render([chat()]);
    await click(rows()[0]);
    expect(onSelectRun).toHaveBeenCalledWith(expect.objectContaining({ id: "7f3a" }));
  });
});
