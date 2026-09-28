/**
 * The Decisions section is a *trimmed* section, and the panel must say so.
 *
 * The journal keeps the newest N decisions and moves the rest into
 * `journal_archive.md`, leaving one marker line in their place. Without reading
 * that marker the panel shows the tail and nothing else — a run past the cap
 * reads as one that never opened a position, because the tick-1 deploy is simply
 * absent with nothing saying it was moved rather than never made.
 */
import { describe, expect, it } from "vitest";

import { parseJournal, parseSnapshot } from "@/lib/parse-agent";

function journal(decisions: string): string {
  return `# Journal\n\n## Decisions\n${decisions}\n\n## Ticks\n`;
}

describe("parseJournal decisions", () => {
  it("reads the trim marker the journal left behind", () => {
    const { decisionsArchived } = parseJournal(
      journal(
        [
          "- archived 7 earlier decisions to journal_archive.md",
          "- **#8** (12:33) hold -- no change",
        ].join("\n"),
      ),
    );

    expect(decisionsArchived).toBe("archived 7 earlier decisions to journal_archive.md");
  });

  it("says nothing when nothing has been archived", () => {
    const { decisionsArchived } = parseJournal(
      journal("- **#1** (09:00) deploy_grid -- opened the position"),
    );

    expect(decisionsArchived).toBe("");
  });

  it("does not read the marker back as a decision", () => {
    const { decisions } = parseJournal(
      journal(
        [
          "- archived 7 earlier decisions to journal_archive.md",
          "- **#8** (12:33) hold -- no change",
        ].join("\n"),
      ),
    );

    expect(decisions).toHaveLength(1);
    expect(decisions[0].tick).toBe(8);
  });

  it("still parses decisions and errors either side of a marker", () => {
    const { decisions } = parseJournal(
      journal(
        [
          "- archived 2 earlier decisions to journal_archive.md",
          "- **#3** (12:33) stop_executor -- limit reached [risk]",
          "- **error** (12:34) API unreachable",
        ].join("\n"),
      ),
    );

    expect(decisions.map((d) => d.tick)).toEqual([3, 0]);
    expect(decisions[0].riskNote).toBe("risk");
    expect(decisions[1].action).toBe("ERROR: API unreachable");
  });
});

describe("parsed shapes carry only what the panels render", () => {
  it("parseJournal does not parse an executors ledger", () => {
    const parsed = parseJournal(
      "# Journal\n\n## Executors\n- executor=abc | type=grid | binance SOL-USDT BUY | amount=$10\n\n## Ticks\n",
    );

    expect(Object.keys(parsed).sort()).toEqual(
      ["decisions", "decisionsArchived", "metrics", "summary", "ticks"].sort(),
    );
  });

  it("parseSnapshot does not read the experiment Mode:/Model: lines", () => {
    const parsed = parseSnapshot("# Experiment #3 — 2026-09-16 10:00\nMode: dry_run\nModel: claude-fable-5\n");

    expect(parsed.tick).toBe(3);
    expect(parsed).not.toHaveProperty("model");
    expect(parsed).not.toHaveProperty("executionMode");
  });
});
