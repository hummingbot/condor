/**
 * The agent's own words are markdown, and must arrive as markdown (FEAT-103).
 *
 * `parse-agent`'s own comment says it: "the Agent Response body is free-form LLM
 * markdown". It was printed as plain text anyway, so `**bold**` and `| tables |`
 * reached the reader as literal characters and streamed chunks read glued
 * together — on the one surface the whole agent workspace exists to put in front
 * of a person.
 *
 * Needs a DOM, so this file overrides vitest's default `node` environment.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ParsedSnapshot } from "@/lib/parse-agent";
import { SnapshotBody } from "./AgentSessionContent";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

function snapshot(agentResponse: string): ParsedSnapshot {
  return {
    tick: 12,
    timestamp: "2026-09-03 20:15",
    model: "claude-fable-5",
    executionMode: "loop",
    systemPrompt: "",
    systemPromptLength: 0,
    executorState: "",
    riskState: "",
    agentResponse,
    toolCalls: [],
    stats: { duration: 0 },
  };
}

function render(parsed: ParsedSnapshot) {
  act(() => {
    root.render(<SnapshotBody parsed={parsed} />);
  });
}

const response = () =>
  container.querySelector<HTMLElement>("[data-agent-response]")!;

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

describe("the agent's response", () => {
  it("renders emphasis and code rather than printing their punctuation", () => {
    render(snapshot("Widened the **BRL** spread, see `brl_mm`."));

    expect(response().querySelector("strong")?.textContent).toBe("BRL");
    expect(response().querySelector("code")?.textContent).toBe("brl_mm");
    expect(response().textContent).not.toContain("**");
  });

  it("renders a GFM table as a table", () => {
    // The thing that made this worth fixing: a model reporting six controllers
    // writes a table, and a table as plain text is six rows of pipes.
    render(
      snapshot(
        ["| pair | spread |", "| --- | --- |", "| SOL-USDC | 12bps |"].join("\n"),
      ),
    );

    expect(response().querySelector("table")).not.toBeNull();
    expect(response().querySelectorAll("td")).toHaveLength(2);
  });

  it("still shows a plain sentence as a plain sentence", () => {
    render(snapshot("Held the range."));
    expect(response().textContent).toContain("Held the range.");
  });
});
