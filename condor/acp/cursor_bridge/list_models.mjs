/**
 * Lists Cursor model IDs for CURSOR_API_KEY.
 *
 *   CURSOR_API_KEY=... npm run list-models
 * Default: one id per line (for Condor agent_key cursor:<id>)
 *   node list_models.mjs --json
 * Compact JSON array for Condor Telegram picker
 *   node list_models.mjs --full
 * Pretty-print full API payload (large)
 */
import { Cursor } from "@cursor/sdk";

const apiKey = process.env.CURSOR_API_KEY;
if (!apiKey) {
  console.error("CURSOR_API_KEY is required");
  process.exit(2);
}

const models = await Cursor.models.list({ apiKey });
const jsonMode = process.argv.includes("--json");
const verbose = process.argv.includes("--full") || process.argv.includes("-a");

if (jsonMode) {
  const compact = models
    .filter((m) => m?.id)
    .map((m) => ({
      id: m.id,
      displayName: m.displayName || m.id,
      description: m.description || "",
      aliases: m.aliases || [],
    }));
  console.log(JSON.stringify(compact));
} else if (verbose) {
  console.log(JSON.stringify(models, null, 2));
} else {
  const ids = [...new Set(models.map((m) => m.id).filter(Boolean))].sort();
  for (const id of ids) {
    console.log(id);
  }
}
