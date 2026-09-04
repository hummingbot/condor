/**
 * The contract the streaming bubble leans on (PERF-327).
 *
 * Two properties matter, and neither is about speed on its own:
 *
 *  1. the pieces always reassemble into exactly the text handed in, and the
 *     pieces already reported never change — otherwise the memo that makes
 *     this worth doing would be memoising the wrong string;
 *  2. a cut is only ever reported where markdown genuinely closes a block, so
 *     no construct is ever torn in half.
 *
 * The second one is the reason this file is mostly a list of traps: a blank
 * line inside a fence, inside a list, inside indented code or inside a raw
 * HTML block is not a boundary, and a reference definition arriving late
 * rewrites text above it.
 */

import { describe, expect, it } from "vitest";

import { splitStreamedMarkdown } from "./markdownStream";

/** Every prefix of `text`, one character at a time. */
function* prefixes(text: string): Generator<string> {
  for (let i = 1; i <= text.length; i++) yield text.slice(0, i);
}

/** The cut points a document settles on, as byte offsets. */
function cuts(text: string): number[] {
  const { frozen } = splitStreamedMarkdown(text);
  const offsets: number[] = [];
  let at = 0;
  for (const chunk of frozen) {
    at += chunk.length;
    offsets.push(at);
  }
  return offsets;
}

/** Filler that pushes a document past the 512-byte freezing threshold. */
function filler(label: string, paragraphs = 6): string {
  return Array.from(
    { length: paragraphs },
    (_, i) =>
      `${label} paragraph ${i}: the book moved against the venue overnight ` +
      `and the hedge did not follow it, which is the whole of the story.\n\n`,
  ).join("");
}

describe("splitting a streaming answer", () => {
  it("always reassembles into the text it was given", () => {
    const doc = `${filler("a")}| x | y |\n| - | - |\n| 1 | 2 |\n\n${filler("b")}`;
    for (const partial of prefixes(doc)) {
      const { frozen, tail } = splitStreamedMarkdown(partial);
      expect(frozen.join("") + tail).toBe(partial);
    }
  });

  it("never rewrites a chunk it has already reported", () => {
    const doc = `${filler("a")}\`\`\`python\nx = 1\n\ny = 2\n\`\`\`\n\n${filler("b")}- one\n- two\n\n${filler("c")}`;
    let previous: string[] = [];
    for (const partial of prefixes(doc)) {
      const { frozen } = splitStreamedMarkdown(partial);
      expect(frozen.slice(0, previous.length)).toEqual(previous);
      previous = frozen;
    }
  });

  it("bounds the text a single frame re-parses, however long the answer gets", () => {
    const short = filler("s", 8);
    const long = filler("l", 200);
    const worst = (doc: string) => {
      let max = 0;
      for (let i = 1; i <= doc.length; i += 7) {
        max = Math.max(max, splitStreamedMarkdown(doc.slice(0, i)).tail.length);
      }
      return max;
    };
    // The long document is 25x the short one; what a frame re-parses is not.
    expect(long.length / short.length).toBeGreaterThan(20);
    expect(worst(long)).toBeLessThan(worst(short) * 2);
  });
});

describe("what is not a boundary", () => {
  it("does not cut inside a fenced code block", () => {
    const doc = `${filler("a")}\`\`\`\nfirst\n\nsecond\n\`\`\`\n\n${filler("b")}`;
    const fenceStart = doc.indexOf("```");
    const fenceEnd = doc.lastIndexOf("```") + 4;
    for (const cut of cuts(doc)) {
      expect(cut > fenceStart && cut < fenceEnd).toBe(false);
    }
  });

  it("does not cut inside a tilde fence, or on a shorter run of backticks", () => {
    const doc = `${filler("a")}~~~\nfirst\n\n\`\`\`\n\nsecond\n~~~\n\n${filler("b")}`;
    const start = doc.indexOf("~~~");
    const end = doc.lastIndexOf("~~~") + 4;
    for (const cut of cuts(doc)) {
      expect(cut > start && cut < end).toBe(false);
    }
  });

  it("does not cut between the items of a loose list", () => {
    // The blank line here does not end the list: `- two` rejoins it and turns
    // every item above into a paragraph.
    const doc = `${filler("a")}- one\n\n- two\n\n- three\n\nAfter.\n\n${filler("b")}`;
    const listStart = doc.indexOf("- one");
    const listEnd = doc.indexOf("After.");
    for (const cut of cuts(doc)) {
      expect(cut > listStart && cut < listEnd).toBe(false);
    }
  });

  it("does not cut a list closed only by a lazy continuation", () => {
    const doc = `${filler("a")}- one\nlazily continued\n\n- two\n\n${filler("b")}`;
    const listStart = doc.indexOf("- one");
    const listEnd = doc.indexOf("- two") + "- two\n".length;
    for (const cut of cuts(doc)) {
      expect(cut > listStart && cut < listEnd).toBe(false);
    }
  });

  it("does not cut inside an indented code block", () => {
    const doc = `${filler("a")}    first\n\n    second\n\nAfter.\n\n${filler("b")}`;
    const start = doc.indexOf("    first");
    const end = doc.indexOf("After.");
    for (const cut of cuts(doc)) {
      expect(cut > start && cut < end).toBe(false);
    }
  });

  it("does not cut inside a raw HTML block a blank line cannot close", () => {
    const doc = `${filler("a")}<pre>\nfirst\n\nsecond\n</pre>\n\n${filler("b")}`;
    const start = doc.indexOf("<pre>");
    const end = doc.indexOf("</pre>") + "</pre>\n".length;
    for (const cut of cuts(doc)) {
      expect(cut > start && cut < end).toBe(false);
    }
  });

  it("closes a one-line raw HTML block instead of swallowing the rest", () => {
    const doc = `<pre>inline</pre>\n\n${filler("a")}${filler("b")}`;
    expect(cuts(doc).length).toBeGreaterThan(0);
  });

  it("freezes nothing at all once a link reference definition appears", () => {
    const doc = `${filler("a")}See [the note][n].\n\n${filler("b")}[n]: https://example.com\n`;
    expect(splitStreamedMarkdown(doc)).toEqual({ frozen: [], tail: doc });
  });

  it("freezes nothing at all once a footnote appears", () => {
    const doc = `${filler("a")}A claim.[^1]\n\n${filler("b")}[^1]: the source\n`;
    expect(splitStreamedMarkdown(doc)).toEqual({ frozen: [], tail: doc });
  });

  it("never freezes the line still being typed", () => {
    const doc = `${filler("a")}A sentence with no newline yet`;
    const { tail } = splitStreamedMarkdown(doc);
    expect(tail.endsWith("A sentence with no newline yet")).toBe(true);
  });
});

describe("what is a boundary", () => {
  it("freezes finished prose once enough of it has piled up", () => {
    const doc = filler("a", 12);
    const { frozen, tail } = splitStreamedMarkdown(doc);
    expect(frozen.length).toBeGreaterThan(1);
    expect(frozen.join("") + tail).toBe(doc);
    for (const chunk of frozen) expect(chunk.endsWith("\n\n")).toBe(true);
  });

  it("frees a table only once its blank line has arrived", () => {
    const table = "| x | y |\n| - | - |\n| 1 | 2 |\n";
    const doc = `${filler("a")}${table}\n${filler("b")}`;
    const tableStart = doc.indexOf("| x");
    for (const cut of cuts(doc)) {
      expect(cut > tableStart && cut < tableStart + table.length).toBe(false);
    }
    expect(cuts(doc).some((c) => c >= tableStart + table.length)).toBe(true);
  });
});
