import { describe, expect, it } from "vitest";

import { chartFences } from "./chartFences";

const SPEC = '{"type":"line","x":"t","series":[{"key":"v"}],"data":[{"t":1,"v":2}]}';

describe("finding the chart fences in an answer", () => {
  it("finds none in prose", () => {
    expect(chartFences("Just words about a chart, no fence.")).toEqual([]);
  });

  it("returns the body of a closed fence", () => {
    expect(chartFences(`Here:\n\n\`\`\`chart\n${SPEC}\n\`\`\`\n\nDone.\n`)).toEqual([
      SPEC,
    ]);
  });

  it("keeps a still-arriving fence at the ordinal it will keep once closed", () => {
    const partial = chartFences(`\`\`\`chart\n${SPEC.slice(0, 20)}`);
    const closed = chartFences(`\`\`\`chart\n${SPEC}\n\`\`\`\n`);
    expect(partial).toHaveLength(1);
    expect(closed).toHaveLength(1);
  });

  it("keeps two charts in the order they were written", () => {
    const a = '{"type":"bar","x":"t","series":[{"key":"a"}],"data":[{"t":1}]}';
    const b = '{"type":"line","x":"t","series":[{"key":"b"}],"data":[{"t":1}]}';
    expect(
      chartFences(`\`\`\`chart\n${a}\n\`\`\`\n\ntext\n\n\`\`\`chart\n${b}\n\`\`\`\n`),
    ).toEqual([a, b]);
  });

  it("does not read a chart fence quoted inside another fence", () => {
    expect(
      chartFences("```python\nprint('```chart')\n```\n\nnot a chart\n"),
    ).toEqual([]);
  });

  it("reads an indented fence and strips the indent from its body", () => {
    expect(chartFences("   ```chart\n   " + SPEC + "\n   ```\n")).toEqual([SPEC]);
  });

  it("ignores a fence in another language", () => {
    expect(chartFences("```json\n" + SPEC + "\n```\n")).toEqual([]);
  });

  it("reads a tilde fence too", () => {
    expect(chartFences(`~~~chart\n${SPEC}\n~~~\n`)).toEqual([SPEC]);
  });
});
