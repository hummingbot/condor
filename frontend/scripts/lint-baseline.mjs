#!/usr/bin/env node
/**
 * Lint gate that fails on *new* errors rather than on the whole backlog.
 *
 * `eslint .` reports 76 errors that predate the CI gate — mostly React Compiler
 * findings (stale refs, cascading setState) that are real but each need their
 * own fix. Blocking every PR on all of them would have meant either a
 * frontend-wide refactor bolted onto whatever branch got there first, or
 * switching the rules off and losing the findings. So the known set is recorded
 * in eslint-baseline.json and only what exceeds it fails.
 *
 * Entries are keyed by file + rule, not by line: line numbers churn on every
 * edit above them, which would make the baseline fail for reasons unrelated to
 * lint. Counts are compared, so a second violation of an already-known rule in
 * an already-known file is still caught.
 *
 * The baseline only ever ratchets down. A fixed error is reported as drift and
 * fails the run too, so the file cannot quietly keep granting an exception that
 * is no longer used — rerun with --update to bank the fix.
 *
 *   node scripts/lint-baseline.mjs            check (CI)
 *   node scripts/lint-baseline.mjs --update   rewrite the baseline
 */
import { execFileSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const baselinePath = join(root, "eslint-baseline.json");
const update = process.argv.includes("--update");

function currentCounts() {
  let stdout;
  try {
    // eslint exits non-zero whenever it reports anything, which is the normal
    // case here — the report on stdout is what matters, not the status.
    stdout = execFileSync("npx", ["eslint", ".", "-f", "json"], {
      cwd: root,
      encoding: "utf8",
      maxBuffer: 64 * 1024 * 1024,
    });
  } catch (err) {
    if (!err.stdout) throw err;
    stdout = err.stdout;
  }

  const counts = {};
  for (const file of JSON.parse(stdout)) {
    const path = file.filePath.slice(root.length + 1);
    for (const msg of file.messages) {
      if (msg.severity !== 2) continue; // warnings are not gated
      const key = `${path} | ${msg.ruleId ?? "(no-rule)"}`;
      counts[key] = (counts[key] ?? 0) + 1;
    }
  }
  return counts;
}

const counts = currentCounts();
const total = Object.values(counts).reduce((a, b) => a + b, 0);

if (update) {
  const sorted = Object.fromEntries(Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)));
  writeFileSync(baselinePath, `${JSON.stringify(sorted, null, 2)}\n`);
  console.log(`Baseline updated: ${Object.keys(sorted).length} entries, ${total} errors.`);
  process.exit(0);
}

let baseline;
try {
  baseline = JSON.parse(readFileSync(baselinePath, "utf8"));
} catch {
  console.error(`Missing or unreadable ${baselinePath}. Run: npm run lint:baseline -- --update`);
  process.exit(1);
}

const regressions = [];
const fixed = [];
for (const key of new Set([...Object.keys(counts), ...Object.keys(baseline)])) {
  const now = counts[key] ?? 0;
  const was = baseline[key] ?? 0;
  if (now > was) regressions.push(`  ${key}  (${was} allowed, ${now} found)`);
  else if (now < was) fixed.push(`  ${key}  (${was} allowed, ${now} found)`);
}

if (regressions.length) {
  console.error(`\nNew lint errors not in the baseline:\n${regressions.join("\n")}`);
  console.error("\nFix them, or if they are genuinely acceptable, run:");
  console.error("  npm run lint:baseline -- --update\n");
  process.exit(1);
}

if (fixed.length) {
  console.error(`\nBaseline is stale — these are already fixed:\n${fixed.join("\n")}`);
  console.error("\nBank the improvement so it cannot regress:");
  console.error("  npm run lint:baseline -- --update\n");
  process.exit(1);
}

console.log(`No new lint errors. ${total} baselined errors remain to pay down.`);
