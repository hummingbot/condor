/**
 * Cutting a still-arriving answer into the part that can no longer change and
 * the part that still can (PERF-327).
 *
 * The live bubble commits 20 times a second, and every commit used to hand the
 * whole accumulated answer to a fresh markdown parse: an answer of length n
 * cost O(n²) before it finished. Measured on a table-heavy report, a 21 KB
 * answer spent 6.3s of main thread on parsing alone while it streamed.
 *
 * The way out is that markdown is a *block* language. Once a block is closed,
 * nothing appended after it can change how it parses — so the text before the
 * last closed block never needs parsing again. This module finds those closed
 * boundaries and hands back the finished chunks separately from the unfinished
 * tail, so the caller can freeze the chunks behind a memo.
 *
 * Everything here is about being *sure*. A boundary is only reported when the
 * scanner can prove that no later line can reach back across it, which is much
 * stricter than "there is a blank line here":
 *
 *   - a blank line inside a fenced code block is just code;
 *   - a blank line inside a list does not end the list — the next item
 *     rejoins it and turns it loose, which re-renders every item above;
 *   - a blank line inside an indented code block or a raw `<pre>`/`<script>`
 *     HTML block does not end those either;
 *   - a link reference definition or a GFM footnote definition arriving late
 *     changes text that came *before* it, so their mere presence anywhere in
 *     the answer switches the whole optimisation off.
 *
 * When in doubt the scanner reports no boundary, and the caller falls back to
 * parsing the whole text — the behaviour that was always correct, only slow.
 */

/**
 * How much text has to pile up before a chunk is frozen.
 *
 * Freezing at every boundary would make one memoised parse per paragraph;
 * grouping them keeps the component count down and, more usefully, bounds the
 * work a single commit can repeat to roughly this many bytes no matter how
 * long the answer grows.
 */
const MIN_FROZEN = 512;

/** `[label]: url` — resolves reference links written anywhere above it. */
const LINK_DEFINITION = /^ {0,3}\[[^\]\n]*\]:/m;

/** Any hint of GFM footnotes: a definition moves a whole section to the end. */
const FOOTNOTE = /\[\^/;

/** An opening (or closing) code fence, at a column markdown still reads. */
const FENCE = /^ {0,3}(`{3,}|~{3,})/;

/** A closing fence: the marker alone on its line. */
const FENCE_CLOSE = /^ {0,3}(`{3,}|~{3,})[ \t]*$/;

/** A bullet or ordered marker — the thing that keeps a list open. */
const LIST_MARKER = /^ {0,3}(?:[-*+]|\d{1,9}[.)])(?:[ \t]|$)/;

/**
 * The HTML blocks that a blank line does *not* close (CommonMark types 1-5).
 * Every other HTML block ends at a blank line like a paragraph does.
 */
const RAW_HTML_TAG = /^ {0,3}<(script|pre|style|textarea)\b/i;
const RAW_HTML_OTHER: [RegExp, string][] = [
  [/^ {0,3}<!--/, "-->"],
  [/^ {0,3}<\?/, "?>"],
  [/^ {0,3}<!\[CDATA\[/, "]]>"],
  [/^ {0,3}<![A-Za-z]/, ">"],
];

/** How far a line is indented, counting a tab as the four columns it fills. */
function indentOf(line: string): number {
  let width = 0;
  for (const ch of line) {
    if (ch === " ") width += 1;
    else if (ch === "\t") width += 4 - (width % 4);
    else break;
    if (width >= 4) return width;
  }
  return width;
}

/** The needle that ends a raw HTML block opened by `line`, if it opens one. */
function rawHtmlCloser(line: string): string | null {
  const tag = RAW_HTML_TAG.exec(line);
  if (tag) return `</${tag[1].toLowerCase()}`;
  for (const [open, close] of RAW_HTML_OTHER) {
    if (open.test(line)) return close;
  }
  return null;
}

export interface StreamedMarkdown {
  /**
   * Finished chunks, in order. Each one is a whole number of closed blocks and
   * is byte-stable: appending to the answer never rewrites a chunk already
   * reported, so a memo keyed on the string never re-parses it.
   */
  frozen: string[];
  /** Everything after the last frozen chunk — the part still being written. */
  tail: string;
}

/**
 * Split a partially-arrived markdown answer into frozen chunks and a live tail.
 *
 * The concatenation of `frozen` and `tail` is always exactly the input, and
 * parsing the pieces separately is guaranteed to produce the same blocks as
 * parsing the input in one go.
 */
export function splitStreamedMarkdown(text: string): StreamedMarkdown {
  // A definition anywhere rewrites text above it, so nothing can be frozen.
  if (FOOTNOTE.test(text) || LINK_DEFINITION.test(text)) {
    return { frozen: [], tail: text };
  }

  const frozen: string[] = [];
  let start = 0;
  let offset = 0;
  /** The fence we are inside, and what it takes to close it. */
  let fence: { char: string; len: number } | null = null;
  /** The string that closes the raw HTML block we are inside. */
  let rawHtml: string | null = null;
  /**
   * A list (or indented code block) is open: a later line can still join it,
   * so a blank line here is inside a block, not between two.
   */
  let container = false;
  /** The previous line was blank — so the next one cannot lazily continue. */
  let blank = false;

  while (offset < text.length) {
    const nl = text.indexOf("\n", offset);
    // The last line has no newline yet: it is still being typed, by
    // definition, and can never be part of a frozen chunk.
    if (nl === -1) break;
    const line = text.slice(offset, nl);
    const end = nl + 1;
    offset = end;

    if (rawHtml !== null) {
      if (line.toLowerCase().includes(rawHtml)) rawHtml = null;
      blank = false;
      continue;
    }

    if (fence !== null) {
      const close = FENCE_CLOSE.exec(line);
      if (close && close[1][0] === fence.char && close[1].length >= fence.len) {
        fence = null;
      }
      blank = false;
      continue;
    }

    if (line.trim() === "") {
      // The one place a chunk can end: a blank line at the outer level, with
      // nothing open above it that a later line could reach back into.
      if (!container && end - start >= MIN_FROZEN) {
        frozen.push(text.slice(start, end));
        start = end;
      }
      blank = true;
      continue;
    }

    const indent = indentOf(line);
    if (indent >= 4) {
      // Indented code, or the body of a list item — either way, appendable.
      container = true;
    } else if (LIST_MARKER.test(line)) {
      container = true;
    } else if (indent === 0 && (blank || !container)) {
      // A block starting hard against the margin after a blank line is the
      // only thing that definitively ends a list. Without the blank it is a
      // lazy continuation of the item above and the list stays open.
      container = false;
    }

    if (indent < 4) {
      const open = FENCE.exec(line);
      if (open) {
        fence = { char: open[1][0], len: open[1].length };
      } else {
        const closer = rawHtmlCloser(line);
        // A one-line `<pre>x</pre>` opens and closes at once; only a block
        // still hanging open at the end of the line spans the lines below.
        rawHtml =
          closer && !line.toLowerCase().includes(closer, 1) ? closer : null;
      }
    }
    blank = false;
  }

  return { frozen, tail: text.slice(start) };
}
