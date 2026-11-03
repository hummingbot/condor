/**
 * A chart is drawn once per answer, not once per render pass (PERF-328).
 *
 * The bubble renders a streaming answer as frozen chunks plus a live tail and
 * a settled one as a single whole-text pass. Both are deliberate, and the
 * switch between them replaces the answer's DOM — which used to tear down and
 * rebuild every chart in it at the exact moment the turn finished.
 *
 * This pins that it does not any more: through the whole stream and across the
 * settle, the chart component is mounted once and its element is the same
 * element throughout. `ChartBlock` is stubbed so the count is the subject of
 * the test rather than a property of recharts.
 *
 * @vitest-environment jsdom
 */

import { act, useEffect } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatMessage } from "@/hooks/useChatSocket";

const stub = vi.hoisted(() => ({ mounts: 0 }));

vi.mock("./ChartBlock", () => ({
  ChartBlock: ({ raw, live }: { raw: string; live: boolean }) => {
    useEffect(() => {
      stub.mounts++;
    }, []);
    return <div data-testid="chart" data-live={String(live)} data-raw={raw} />;
  },
}));

const { ChatMessageView } = await import("./ChatMessage");

declare global {
  var IS_REACT_ACT_ENVIRONMENT: boolean;
}

const SPEC =
  '{"type":"line","title":"PnL","x":"t","series":[{"key":"pnl","name":"PnL"}],"data":[{"t":1,"pnl":2},{"t":2,"pnl":3}]}';

/** Filler that carries the answer past the point where chunks start freezing. */
function filler(label: string, paragraphs = 5): string {
  return Array.from(
    { length: paragraphs },
    (_, i) =>
      `${label} paragraph ${i}: the book moved against the venue overnight ` +
      `and the hedge did not follow it, which is the whole of the story.\n\n`,
  ).join("");
}

const ANSWER = `${filler("a")}\`\`\`chart\n${SPEC}\n\`\`\`\n\n${filler("b")}`;

function message(text: string): ChatMessage {
  return { id: "m1", role: "assistant", text, toolCalls: [], ts: 1_756_000_000 };
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  globalThis.IS_REACT_ACT_ENVIRONMENT = true;
  stub.mounts = 0;
  container = document.createElement("div");
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function render(text: string, live: boolean) {
  act(() => {
    root.render(
      <ChatMessageView message={message(text)} live={live} agentName="Brigado" />,
    );
  });
}

function chart(): HTMLElement | null {
  return container.querySelector<HTMLElement>('[data-testid="chart"]');
}

function answerHtml(): string {
  return container.querySelector(".chat-markdown")?.innerHTML ?? "";
}

describe("a chart in a streaming answer", () => {
  it("survives the settle with the same instance and the same element", () => {
    render(ANSWER, true);
    const streaming = chart();
    expect(streaming).not.toBeNull();
    expect(stub.mounts).toBe(1);

    render(ANSWER, false);

    // The same node, still in the document, still where the fence was.
    expect(chart()).toBe(streaming);
    expect(streaming!.isConnected).toBe(true);
    expect(container.querySelector(".chat-markdown")!.contains(streaming!)).toBe(
      true,
    );
    // The whole point: one mount for the whole turn, not one per render pass.
    expect(stub.mounts).toBe(1);
    // And the chart knows the turn is over, without having been rebuilt to
    // find out — a malformed spec is an error now, not a half-arrived one.
    expect(streaming!.dataset.live).toBe("false");
  });

  it("survives the chunk freezing while the answer is still arriving", () => {
    // The fence closes early, then the answer keeps growing past it, which is
    // what moves the chart from the live tail into a frozen chunk.
    for (let i = 40; i < ANSWER.length; i += 40) render(ANSWER.slice(0, i), true);
    render(ANSWER, true);
    const streaming = chart();
    expect(streaming).not.toBeNull();
    render(ANSWER, false);
    expect(chart()).toBe(streaming);
    expect(stub.mounts).toBe(1);
  });

  it("settles into exactly the DOM a reload would produce", () => {
    render(ANSWER, true);
    render(ANSWER, false);
    const settled = answerHtml();

    act(() => root.unmount());
    root = createRoot(container);
    render(ANSWER, false);

    expect(settled).toBe(answerHtml());
    expect(settled).toContain('data-testid="chart"');
    expect(settled).not.toContain("```");
  });

  it("renders both inline when two fences cannot be told apart", () => {
    // Nothing in the text names one of them, so neither is adopted and both
    // render where they stand — the behaviour that was always there.
    const twice = `\`\`\`chart\n${SPEC}\n\`\`\`\n\ntext\n\n\`\`\`chart\n${SPEC}\n\`\`\`\n`;
    render(twice, false);
    expect(container.querySelectorAll('[data-testid="chart"]').length).toBe(2);
  });
});
