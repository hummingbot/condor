/**
 * A streamed answer reads exactly like the same answer reloaded (PERF-327).
 *
 * The live bubble no longer re-parses the whole answer on every frame: it
 * freezes the stretches whose blocks are closed and only re-parses the end.
 * That is only worth having if it is invisible, so this file pins the
 * invariant rather than the mechanism — at *every* frame of the stream, the
 * incrementally parsed bubble must render the same DOM as one whole-text pass
 * over the same partial answer would have. If a cut ever lands inside a fence,
 * a table or a list, this is what says so.
 *
 * The settled turn is checked too, and separately: that is the render a
 * reloaded transcript goes through, and it is the one the reader keeps.
 *
 * @vitest-environment jsdom
 */

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import type { ChatMessage } from "@/hooks/useChatSocket";
import { ChatMessageView } from "./ChatMessage";

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

function message(text: string): ChatMessage {
  return { id: "m1", role: "assistant", text, toolCalls: [], ts: 1_756_000_000 };
}

let container: HTMLDivElement;
let root: Root;

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

/** The rendered answer, without the chrome around it. */
function answerHtml(): string {
  return container.querySelector(".chat-markdown")?.innerHTML ?? "";
}

/**
 * The answer as the blocks it is made of, plus whatever loose text sits
 * between them.
 *
 * Compared block by block rather than as one HTML string because
 * `mdast-util-to-hast` puts a `\n` text node between a document's top-level
 * blocks, and a chunk rendered on its own has no neighbour to be separated
 * from — so the seams carry one fewer collapsed newline than the single pass
 * does. Nothing renders differently for it (inter-block whitespace collapses
 * to nothing), and every difference that *would* show up — a torn fence, a
 * split table, an escaped marker — is a difference in the blocks themselves or
 * in the loose text, both of which are checked.
 */
function answerBlocks(): { blocks: string[]; loose: string } {
  const el = container.querySelector(".chat-markdown");
  if (!el) return { blocks: [], loose: "" };
  return {
    blocks: Array.from(el.children).map((child) => child.outerHTML),
    loose: Array.from(el.childNodes)
      .filter((node) => node.nodeType === Node.TEXT_NODE)
      .map((node) => node.textContent)
      .join(""),
  };
}

function render(text: string, live: boolean) {
  act(() => {
    root.render(
      <ChatMessageView message={message(text)} live={live} agentName="Brigado" />,
    );
  });
}

/**
 * Stream `text` in `chunk`-sized pieces and compare every frame against the
 * whole-text render of the same partial answer.
 */
function expectStreamMatchesOneShot(text: string, chunk = 11) {
  const mismatches: string[] = [];
  for (let i = chunk; i < text.length; i += chunk) {
    const partial = text.slice(0, i);
    render(partial, true);
    const streamed = answerBlocks();
    // A fresh root, so the one-shot render cannot inherit any of the streamed
    // tree — this is what a reload would produce for the same text.
    act(() => root.unmount());
    root = createRoot(container);
    render(partial, false);
    const oneShot = answerBlocks();
    act(() => root.unmount());
    root = createRoot(container);

    if (streamed.loose.trim() !== "" || oneShot.loose.trim() !== "") {
      mismatches.push(`at ${i} bytes: loose text ${JSON.stringify(streamed.loose)}`);
      continue;
    }
    if (streamed.blocks.length !== oneShot.blocks.length) {
      mismatches.push(
        `at ${i} bytes: ${streamed.blocks.length} blocks streamed vs ${oneShot.blocks.length} in one pass`,
      );
      continue;
    }
    const differing = streamed.blocks.findIndex((b, n) => b !== oneShot.blocks[n]);
    if (differing !== -1) {
      mismatches.push(
        `at ${i} bytes, block ${differing}:\n  streamed: ${streamed.blocks[differing]}\n  one-shot: ${oneShot.blocks[differing]}`,
      );
    }
  }
  expect(mismatches).toEqual([]);
}

/** Filler that carries the answer past the point where chunks start freezing. */
function filler(label: string, paragraphs = 5): string {
  return Array.from(
    { length: paragraphs },
    (_, i) =>
      `${label} paragraph ${i}: the book moved against the venue overnight ` +
      `and the hedge did not follow it, which is the whole of the story.\n\n`,
  ).join("");
}

const TABLE = "| Controller | Notional | PnL |\n| --- | --- | --- |\n| alpha | 1200.42 | -3.10 |\n| beta | 980.00 | 12.75 |\n";
const FENCE = "```python\ndef rebalance(book):\n\n    return book\n```\n";

describe("an answer arriving in chunks", () => {
  it("matches the one-shot render at every frame, through a fence and a table", () => {
    expectStreamMatchesOneShot(
      `${filler("a")}${FENCE}\n${filler("b")}${TABLE}\n${filler("c")}`,
    );
  });

  it("matches the one-shot render across a loose list and a lazy continuation", () => {
    expectStreamMatchesOneShot(
      `${filler("a")}- one\n\n- two\n\n- three\nlazily continued\n\n${filler("b")}`,
    );
  });

  it("matches the one-shot render across indented code and a quote", () => {
    expectStreamMatchesOneShot(
      `${filler("a")}    first\n\n    second\n\n> quoted\n\n${filler("b")}`,
    );
  });

  it("matches the one-shot render when a reference definition lands late", () => {
    expectStreamMatchesOneShot(
      `${filler("a")}See [the note][n] and [another][m].\n\n${filler("b")}[n]: https://example.com\n[m]: https://example.org\n`,
    );
  });

  it("matches the one-shot render across headings and a thematic break", () => {
    expectStreamMatchesOneShot(
      `# Report\n\n${filler("a")}Setext\n===\n\n---\n\n## Next\n\n${filler("b")}`,
    );
  });

  it("settles into exactly the DOM a reload would produce", () => {
    const answer = `${filler("a")}${FENCE}\n${filler("b")}${TABLE}\n${filler("c")}- one\n\n- two\n`;
    for (let i = 7; i < answer.length; i += 7) render(answer.slice(0, i), true);
    render(answer, true);
    render(answer, false);
    const settled = answerHtml();

    act(() => root.unmount());
    root = createRoot(container);
    render(answer, false);
    expect(settled).toBe(answerHtml());
    expect(settled).toContain("<table");
    expect(settled).toContain("data-num-cols");
    expect(settled).not.toContain("```");
  });
});
