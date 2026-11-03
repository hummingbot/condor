/**
 * How much markdown a streamed frame actually re-parses (PERF-327).
 *
 * The bubble commits 20 times a second, and it used to hand the whole
 * accumulated answer to remark every time — so an answer of length n paid
 * O(n²) before it finished, and the cost of a single frame kept climbing as
 * the answer grew. This counts the bytes that reach the parser, which is the
 * quantity that was quadratic.
 *
 * `react-markdown` is wrapped rather than `numericColumns` because it is the
 * parse itself: one render of it is one document parsed, and a chunk held
 * behind the memo never reaches it at all.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "@/hooks/useChatSocket";

const { parsed } = vi.hoisted(() => ({ parsed: [] as string[] }));

vi.mock("react-markdown", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-markdown")>();
  const Real = actual.default;
  return {
    ...actual,
    default: function CountingMarkdown(props: Parameters<typeof Real>[0]) {
      parsed.push(String(props.children ?? ""));
      return <Real {...props} />;
    },
  };
});

const { ChatMessageView } = await import("./ChatMessage");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
  parsed.length = 0;
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

/** A table-heavy report of roughly `sections` × 800 bytes. */
function report(sections: number): string {
  const out: string[] = ["# Fleet report\n"];
  for (let s = 0; s < sections; s++) {
    out.push(`## Section ${s}\n`);
    out.push(
      `The book moved ${s * 137} bps against the venue overnight, and the ` +
        `hedge did not follow it.\n`,
    );
    out.push("| Controller | Notional | PnL |");
    out.push("| --- | --- | --- |");
    for (let r = 0; r < 8; r++) {
      out.push(`| ctrl-${s}-${r} | ${1000 + r * 13}.42 | -${r}.${s}1 |`);
    }
    out.push("");
    out.push("```python");
    out.push(`def rebalance_${s}(book):`);
    out.push("    return book");
    out.push("```");
    out.push("");
  }
  return out.join("\n");
}

/** Stream `text` and report what the parser was handed, frame by frame. */
function streamCost(text: string, chunk = 60) {
  const perFrame: number[] = [];
  for (let i = chunk; i <= text.length; i += chunk) {
    parsed.length = 0;
    act(() => {
      root.render(
        <ChatMessageView
          message={
            {
              id: "m1",
              role: "assistant",
              text: text.slice(0, i),
              toolCalls: [],
              ts: 1,
            } satisfies ChatMessage
          }
          live
          agentName="Brigado"
        />,
      );
    });
    perFrame.push(parsed.reduce((sum, doc) => sum + doc.length, 0));
  }
  return {
    perFrame,
    total: perFrame.reduce((a, b) => a + b, 0),
    /** What the old bubble would have parsed: the whole answer, every frame. */
    wholeTextTotal: perFrame.reduce((sum, _, n) => sum + (n + 1) * chunk, 0),
  };
}

/** Start each measurement on a tree that has never seen the answer before. */
function fresh() {
  act(() => root.unmount());
  root = createRoot(container);
}

describe("the parse cost of a streaming answer", () => {
  it("costs no more per frame at the end of a long answer than at its start", () => {
    const long = report(48);
    expect(long.length).toBeGreaterThan(20_000);
    const { perFrame } = streamCost(long);

    // Before the split, a frame near the end re-parsed the whole 20 KB and so
    // cost several times what an early frame did. Now it re-parses only the
    // unfinished end, and the two are the same size.
    const quarter = Math.floor(perFrame.length / 4);
    const early = Math.max(...perFrame.slice(0, quarter));
    const late = Math.max(...perFrame.slice(-quarter));
    expect(late).toBeLessThan(early * 1.5);
    expect(late).toBeLessThan(2_000);
  });

  it("scales with the length of the answer instead of its square", () => {
    const small = report(12);
    const large = report(48);
    expect(large.length / small.length).toBeGreaterThan(3.5);

    fresh();
    const a = streamCost(small);
    fresh();
    const b = streamCost(large);

    // Quadrupling the answer quadruples what the parser is handed — where the
    // whole-text pipeline would have handed it sixteen times as much.
    expect(b.total / a.total).toBeLessThan(6);
    expect(b.wholeTextTotal / a.wholeTextTotal).toBeGreaterThan(12);
    expect(b.wholeTextTotal / b.total).toBeGreaterThan(10);
  });

  it("never hands the same frozen chunk to the parser twice", () => {
    parsed.length = 0;
    const text = report(8);
    for (let i = 60; i <= text.length; i += 60) {
      act(() => {
        root.render(
          <ChatMessageView
            message={{ id: "m1", role: "assistant", text: text.slice(0, i), toolCalls: [], ts: 1 }}
            live
            agentName="Brigado"
          />,
        );
      });
    }
    // Every frozen chunk ends on the blank line that closed it; the live tail
    // never does, so these are exactly the chunks that were frozen.
    const chunks = parsed.filter((doc) => doc.endsWith("\n\n") && doc.length > 400);
    expect(chunks.length).toBeGreaterThan(4);
    expect(new Set(chunks).size).toBe(chunks.length);
  });
});
