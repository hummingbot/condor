/**
 * Where the ```chart fences are in an answer, read straight off the text
 * (PERF-328).
 *
 * The bubble renders a streaming answer through one tree and a settled one
 * through another, so nothing *inside* the markdown can name a chart in a way
 * that survives the switch — React reconciles by position, and the two trees
 * have no position in common. The answer's own text does: it is the one thing
 * both passes are made from, and the nth chart fence in it is the nth chart
 * fence whichever pass drew it.
 *
 * So this hands back the fence bodies in order, and that ordinal is the
 * identity a chart's instance is kept under while the trees are swapped.
 *
 * It is a scanner, not a parser: it agrees with remark on which fences carry
 * the `chart` language, and where it does not, the caller compares the body it
 * found against the body remark handed the component and simply declines to
 * match. A miss costs the optimisation, never correctness.
 */

/** An opening fence at a column markdown still reads, with its first info word. */
const FENCE_OPEN = /^( {0,3})(`{3,}|~{3,})[ \t]*([^\s`]*)/;

const NONE: string[] = [];

/** A closing fence for `marker`: the same character, at least as long, alone. */
function closes(line: string, marker: string): boolean {
  const match = /^ {0,3}(`{3,}|~{3,})[ \t]*$/.exec(line);
  return (
    !!match && match[1][0] === marker[0] && match[1].length >= marker.length
  );
}

/** Drop up to `indent` leading spaces, the way the opening fence's own indent
 *  is stripped from the block it opens. */
function dedent(line: string, indent: number): string {
  let i = 0;
  while (i < indent && line[i] === " ") i++;
  return line.slice(i);
}

/**
 * The trimmed body of every ```chart fence in `text`, in document order.
 *
 * An unclosed fence at the end counts: that is the shape of a chart that is
 * still arriving, and it has to hold the same ordinal it will hold once its
 * closing fence lands, or the chart would be rebuilt the moment it did.
 */
export function chartFences(text: string): string[] {
  // The whole scan is skipped for the answers that have no chart in them,
  // which is nearly all of them — one native pass instead of splitting a
  // 20 KB answer into lines twenty times a second.
  if (!text.includes("chart")) return NONE;

  const lines = text.split("\n");
  const found: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const open = FENCE_OPEN.exec(lines[i]);
    if (!open) {
      i++;
      continue;
    }
    const [, indent, marker, info] = open;
    const body: string[] = [];
    i++;
    // Every fence is walked to its end, chart or not: a ```chart line inside a
    // ```python block is code, and skipping over it is what keeps the ordinals
    // aligned with the ones the renderer produces.
    while (i < lines.length && !closes(lines[i], marker)) {
      body.push(dedent(lines[i], indent.length));
      i++;
    }
    i++; // the closing fence, or the end of the text
    if (info === "chart") found.push(body.join("\n").trim());
  }
  return found.length > 0 ? found : NONE;
}
